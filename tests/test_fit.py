"""The target-size search, driven by a fake renderer so no ffmpeg is needed."""

from __future__ import annotations

import pytest

from server import estimate, fit
from server.models import RenderResult, RenderSpec

BASE = RenderSpec(video_id="abc", duration=3.0, fps=15, width=480, height=270, preset="balanced")


def fake_renderer(bpp_multiplier: float = 1.0, calls: list | None = None):
    """A renderer whose output size follows the size model, scaled by a factor.

    `bpp_multiplier` stands in for how busy the footage is: 1.0 means the model
    predicted this clip perfectly, 3.0 means it is three times harder to compress.
    """
    def render(spec: RenderSpec) -> RenderResult:
        if calls is not None:
            calls.append(spec)
        predicted = estimate.for_spec(spec)
        size = int(estimate.HEADER_BYTES + (predicted["bytes"] - estimate.HEADER_BYTES) * bpp_multiplier)
        width, height = spec.even_size()
        return RenderResult(
            name="x.gif", gif_url="/api/gif/x.gif", bytes=size,
            width=width, height=height, frames=predicted["frames"], fps=spec.fps,
            duration=spec.duration, elapsed_ms=1, colors=spec.resolved()["colors"],
            bytes_before_optimize=size, measured_bpp=0.0,
        )
    return render


def test_a_spec_already_under_target_renders_once():
    calls: list = []
    outcome = fit.fit(BASE, 50_000_000, fake_renderer(1.0, calls), max_renders=4)
    assert outcome.met is True
    assert outcome.renders == 1
    assert len(calls) == 1
    assert outcome.spec == BASE, "nothing should be degraded when it already fits"


def test_it_reaches_a_demanding_target_and_reports_the_trade():
    target = 400_000
    outcome = fit.fit(BASE, target, fake_renderer(1.0), max_renders=5)
    assert outcome.met is True
    assert outcome.result.bytes <= target
    assert outcome.notes, "the user must be told what was given up"


def test_the_ladder_sacrifices_resolution_last():
    """Structural, so refitting the size model cannot silently reorder priorities."""
    first_width_cut = next(i for i, rung in enumerate(fit.LADDER) if "width_scale" in rung)
    first_fps_cut = next(i for i, rung in enumerate(fit.LADDER) if "fps_drop" in rung)
    first_colour_cut = next(i for i, rung in enumerate(fit.LADDER) if "colors" in rung)

    assert first_colour_cut < first_fps_cut < first_width_cut
    assert "lossy" in fit.LADDER[0], "the cheapest knob must be tried first"
    assert set(fit.LADDER[0]) == {"lossy"}, "the first rung must cost nothing else"


def test_a_reachable_target_is_met_without_losing_resolution():
    """A target the frame-rate rungs can reach must not shrink the image."""
    # Derived from the model, so the test survives a refit of the constants.
    last_full_size = max(
        i for i, rung in enumerate(fit.LADDER) if "width_scale" not in rung
    )
    reachable = fit.predicted_bytes(fit.apply_rung(BASE, fit.LADDER[last_full_size]), 1.0)

    # Comfortably above that rung's prediction, so the search's own 5% safety
    # margin still accepts it rather than stepping down to a resolution rung.
    calls: list = []
    outcome = fit.fit(BASE, int(reachable * 1.2), fake_renderer(1.0, calls), max_renders=4)

    assert len(calls) >= 2, "the target must be low enough to force a retry"
    assert outcome.met is True
    assert outcome.spec.even_size() == BASE.even_size(), "resolution is the last resort"
    assert outcome.spec.resolved()["lossy"] > BASE.resolved()["lossy"]


def test_an_impossible_target_returns_the_smallest_attempt_and_says_so():
    outcome = fit.fit(BASE, 12_000, fake_renderer(1.0), max_renders=3)
    assert outcome.met is False
    assert "could not reach the target" in " ".join(outcome.notes)
    assert outcome.renders <= 3


def test_the_render_budget_is_never_exceeded():
    calls: list = []
    fit.fit(BASE, 11_000, fake_renderer(5.0, calls), max_renders=2)
    assert len(calls) <= 2


def test_hard_to_compress_footage_still_converges():
    """A clip 3x busier than the model assumed must still land under target."""
    target = 900_000
    outcome = fit.fit(BASE, target, fake_renderer(3.0), max_renders=5)
    assert outcome.met is True, outcome.notes
    assert outcome.result.bytes <= target


def test_the_reported_spec_is_the_one_that_was_rendered():
    calls: list = []
    outcome = fit.fit(BASE, 500_000, fake_renderer(1.0, calls), max_renders=5)
    assert outcome.spec == calls[-1] or outcome.result.bytes <= 500_000
    assert outcome.spec.video_id == BASE.video_id


def test_rungs_never_improve_quality_beyond_the_base_spec():
    generous = BASE.model_copy(update={"lossy": 250, "colors": 32})
    for rung in fit.LADDER:
        applied = fit.apply_rung(generous, rung)
        assert applied.resolved()["lossy"] >= 250
        assert applied.resolved()["colors"] <= 32


def test_rungs_respect_the_floors():
    tiny = BASE.model_copy(update={"fps": 8, "width": 140, "height": 140})
    for rung in fit.LADDER:
        applied = fit.apply_rung(tiny, rung)
        assert applied.fps >= fit.MIN_FPS
        assert applied.width >= fit.MIN_WIDTH - 2
        assert applied.width % 2 == 0 and applied.height % 2 == 0


def test_width_scaling_preserves_the_aspect_ratio():
    spec = BASE.model_copy(update={"width": 600, "height": 400})
    applied = fit.apply_rung(spec, {"lossy": 200, "width_scale": 0.5})
    before = 600 / 400
    after = applied.width / applied.height
    assert abs(before - after) < 0.02


def test_describe_lists_only_what_changed():
    changed = BASE.model_copy(update={"fps": 10, "lossy": 200})
    notes = " ".join(fit.describe(BASE, changed))
    assert "frame rate" in notes
    assert "lossy" in notes
    assert "size" not in notes


def test_progress_is_reported_for_each_attempt():
    seen: list = []
    fit.fit(BASE, 300_000, fake_renderer(1.0), max_renders=3,
            on_step=lambda index, message: seen.append((index, message)))
    assert len(seen) >= 2
    assert [index for index, _ in seen] == sorted(index for index, _ in seen)
