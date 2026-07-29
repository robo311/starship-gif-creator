"""Predicting GIF size before encoding. Pure arithmetic — no I/O, no subprocess.

The model is deliberately simple: a GIF's size is dominated by
`pixels x bits-per-pixel`, where bits-per-pixel is set by how noisy the
quantised image is. Dithering choice drives that far more than anything else,
so it is the primary term.

The one thing arithmetic cannot know is how busy the *content* is: a static
talking head compresses an order of magnitude better than a fast pan. That is
what `calibration` is for — after a real render we measure the true
bits-per-pixel for that clip and reuse it.
"""

from __future__ import annotations

from math import log2

from .models import RenderSpec

# Every constant below was fitted to measured renders rather than guessed: a
# sweep of lossy levels, colour counts and dither modes over real footage, each
# measured as bits per pixel. See docs/size-model.md for the data.

# Bits per pixel for a 256-colour render at lossy=0, by dither mode.
# Error diffusion scatters isolated pixels and defeats LZW; ordered (bayer)
# dithering repeats a fixed matrix, which compresses better.
DITHER_BPP = {
    "none": 2.07,
    "sierra2": 2.61,
    "sierra2_4a": 3.00,
    "floyd_steinberg": 2.86,
    "bayer": 3.52,  # bayer_scale=0; coarser scales are cheaper, see below
}

# bayer_scale trades pattern coarseness for compressibility.
BAYER_SCALE_BPP = {0: 3.52, 1: 3.17, 2: 2.78, 3: 2.50, 4: 2.27, 5: 2.16}

# Halving the palette saves rather more than one bit per index would suggest,
# because a coarser palette also flattens the dither pattern.
COLOR_EXPONENT = 1.36

# gifsicle's --lossy is far weaker than its numbers imply: even at 300 it only
# removes about a third of the payload, and most of that arrives by 40.
LOSSY_SCALE = 5.0
LOSSY_EXPONENT = 0.103

# GIF header, logical screen descriptor, global colour table, trailer.
HEADER_BYTES = 800

# Sharpening adds high-frequency detail, which is what compresses worst.
SHARPEN_PENALTY = 0.05


def frame_count(duration: float, fps: int, speed: float = 1.0, boomerang: bool = False) -> int:
    """Frames ffmpeg will emit for `duration` seconds of source at `fps`.

    Rounds half up rather than using `round()`'s banker's rounding, so the
    browser-side mirror of this arithmetic agrees on every input.
    """
    rate = speed if speed > 0 else 1.0
    frames = max(1, int((duration / rate) * fps + 0.5))
    if boomerang:
        # The reversed half reuses every frame but the one that would repeat.
        frames = max(1, frames * 2 - 1)
    return frames


def bits_per_pixel(
    dither: str,
    bayer_scale: int,
    colors: int,
    lossy: int,
    sharpen: float = 0.0,
) -> float:
    """Predicted bits per pixel for these encoder settings.

    Frame dropping is deliberately absent. `mpdecimate` removes duplicate frames,
    and how many exist is a property of the footage, not of any setting — on busy
    material it removes none at all. Pretending it saves a fixed fraction made
    estimates worse, so that effect is left to `calibration_ratio`, which
    measures whatever really happened.
    """
    if dither == "bayer":
        base = BAYER_SCALE_BPP.get(bayer_scale, BAYER_SCALE_BPP[3])
    else:
        base = DITHER_BPP.get(dither, DITHER_BPP["bayer"])

    # A smaller palette means fewer bits per index and a flatter dither.
    color_factor = (log2(max(2, colors)) / 8.0) ** COLOR_EXPONENT

    # Sub-linear and quick to saturate; lossy=0 must leave this at exactly 1.
    lossy_factor = (1.0 + max(0, lossy) / LOSSY_SCALE) ** -LOSSY_EXPONENT

    bpp = base * color_factor * lossy_factor
    if sharpen > 0:
        bpp *= 1.0 + SHARPEN_PENALTY * sharpen
    return bpp


def estimate_bytes(
    width: int,
    height: int,
    frames: int,
    bpp: float,
    calibration: float | None = None,
) -> int:
    """Predicted file size. `calibration` is a measured/predicted bpp ratio."""
    if width <= 0 or height <= 0 or frames <= 0:
        return 0
    effective = bpp * (calibration if calibration and calibration > 0 else 1.0)
    return int(HEADER_BYTES + (width * height * frames * effective) / 8.0)


def measured_bpp(actual_bytes: int, width: int, height: int, frames: int) -> float:
    """Back out the true bits per pixel from a finished render."""
    pixels = width * height * frames
    if pixels <= 0:
        return 0.0
    payload = max(0, actual_bytes - HEADER_BYTES)
    return (payload * 8.0) / pixels


def for_spec(spec: RenderSpec, calibration: float | None = None) -> dict:
    """Frames, bits per pixel and predicted size for a whole RenderSpec.

    Single place that knows how a spec maps onto the size model, so the estimate
    endpoint, the target-size search and post-render calibration cannot drift.
    """
    settings = spec.resolved()
    frames = frame_count(spec.duration, spec.fps, spec.speed, spec.boomerang)
    bpp = bits_per_pixel(
        settings["dither"],
        settings["bayer_scale"],
        settings["colors"],
        settings["lossy"],
        spec.sharpen,
    )
    width, height = spec.even_size()
    return {
        "frames": frames,
        "bpp": bpp,
        "bytes": estimate_bytes(width, height, frames, bpp, calibration),
    }


def calibration_ratio(actual_bytes: int, width: int, height: int, frames: int, predicted_bpp: float) -> float:
    """How much busier (>1) or calmer (<1) this clip is than the model assumed."""
    if predicted_bpp <= 0:
        return 1.0
    measured = measured_bpp(actual_bytes, width, height, frames)
    if measured <= 0:
        return 1.0
    # Clamp so one weird render cannot wreck later estimates.
    return min(4.0, max(0.25, measured / predicted_bpp))
