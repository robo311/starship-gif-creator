"""Hitting a target file size by giving up the least noticeable quality first.

Blind trial and error would need many renders. Instead the first render measures
how compressible this particular clip is, and that measurement then *predicts*
which settings will land under the target — so the search usually converges in
one or two further renders rather than a dozen.

The ladder's order is the opinion this module encodes: raising gifsicle's lossy
level is nearly free perceptually, dropping colours is mild, losing frame rate is
noticeable, and shrinking the image is the last resort because resolution is the
thing the user actually asked for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from . import estimate
from .models import RenderResult, RenderSpec

# Each rung: the lossy floor, the colour ceiling, frames per second to give up,
# and a width multiplier. Ordered from least to most damaging.
LADDER: list[dict] = [
    {"lossy": 60},
    {"lossy": 90},
    {"lossy": 120, "colors": 160},
    {"lossy": 150, "colors": 128},
    {"lossy": 175, "colors": 112, "fps_drop": 3},
    {"lossy": 200, "colors": 96, "fps_drop": 5, "width_scale": 0.85},
    {"lossy": 240, "colors": 80, "fps_drop": 6, "width_scale": 0.72},
    {"lossy": 280, "colors": 64, "fps_drop": 7, "width_scale": 0.6},
    {"lossy": 300, "colors": 48, "fps_drop": 8, "width_scale": 0.5},
]

MIN_FPS = 6
MIN_WIDTH = 120


@dataclass
class FitOutcome:
    result: RenderResult
    spec: RenderSpec
    met: bool
    renders: int
    notes: list[str] = field(default_factory=list)


def apply_rung(spec: RenderSpec, rung: dict) -> RenderSpec:
    """The base spec with one ladder rung applied, never improving quality."""
    settings = spec.resolved()
    width, height = spec.even_size()

    scale = rung.get("width_scale", 1.0)
    new_width = max(MIN_WIDTH, int(width * scale))
    new_height = max(2, round(height * (new_width / width))) if width else height

    return spec.model_copy(update={
        "lossy": max(settings["lossy"], rung["lossy"]),
        "colors": min(settings["colors"], rung.get("colors", settings["colors"])),
        "fps": max(MIN_FPS, spec.fps - rung.get("fps_drop", 0)),
        "width": new_width - (new_width % 2),
        "height": new_height - (new_height % 2),
    })


def predicted_bytes(spec: RenderSpec, calibration: float) -> int:
    return estimate.for_spec(spec, calibration)["bytes"]


def _calibration_from(spec: RenderSpec, result: RenderResult) -> float:
    return estimate.calibration_ratio(
        result.bytes,
        result.width,
        result.height,
        max(1, result.frames),
        estimate.for_spec(spec)["bpp"],
    )


def describe(base: RenderSpec, final: RenderSpec) -> list[str]:
    """Plain-language summary of what was traded away."""
    before, after = base.resolved(), final.resolved()
    notes = []
    if after["lossy"] != before["lossy"]:
        notes.append(f"lossy compression {before['lossy']} → {after['lossy']}")
    if after["colors"] != before["colors"]:
        notes.append(f"colours {before['colors']} → {after['colors']}")
    if final.fps != base.fps:
        notes.append(f"frame rate {base.fps} → {final.fps} fps")
    if final.even_size() != base.even_size():
        bw, bh = base.even_size()
        fw, fh = final.even_size()
        notes.append(f"size {bw}×{bh} → {fw}×{fh}")
    return notes


def fit(
    spec: RenderSpec,
    target_bytes: int,
    render: Callable[[RenderSpec], RenderResult],
    max_renders: int = 4,
    on_step: Callable[[int, str], None] | None = None,
) -> FitOutcome:
    """Render `spec` at or below `target_bytes`, trading quality only as needed.

    `render` is injected so the search can be tested without invoking ffmpeg.
    """
    def step(index: int, message: str) -> None:
        if on_step:
            on_step(index, message)

    step(1, "Rendering at your current settings")
    result = render(spec)
    renders = 1
    best_spec, best_result = spec, result

    if result.bytes <= target_bytes:
        return FitOutcome(result, spec, True, renders, ["already within the target"])

    calibration = _calibration_from(spec, result)
    rung_index = 0

    while renders < max_renders and rung_index < len(LADDER):
        # Advance to the first rung predicted to clear the target with margin,
        # so a slightly optimistic model does not waste a render.
        candidate = None
        while rung_index < len(LADDER):
            trial = apply_rung(spec, LADDER[rung_index])
            rung_index += 1
            if predicted_bytes(trial, calibration) <= target_bytes * 0.95:
                candidate = trial
                break
            candidate = trial  # keep the most aggressive rung as a fallback

        if candidate is None:
            break

        renders += 1
        step(renders, f"Trying {candidate.resolved()['colors']} colours at {candidate.fps} fps")
        attempt = render(candidate)

        if attempt.bytes < best_result.bytes:
            best_spec, best_result = candidate, attempt

        if attempt.bytes <= target_bytes:
            return FitOutcome(attempt, candidate, True, renders, describe(spec, candidate))

        calibration = _calibration_from(candidate, attempt)

    notes = describe(spec, best_spec)
    notes.append("could not reach the target — shorten the clip or crop tighter")
    return FitOutcome(best_result, best_spec, False, renders, notes)
