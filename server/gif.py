"""The GIF pipeline: two-pass palette encoding plus a gifsicle optimisation pass.

Filter order is deliberate and is where most of the visible quality comes from:

    fps -> [mpdecimate] -> crop -> scale=lanczos

* `fps` first, so the expensive scaling only touches frames that survive.
* `crop` before `scale`, so the region you kept uses the full output
  resolution instead of being downscaled together with discarded surroundings.
* `lanczos`, which is markedly sharper on downscale than the default bilinear —
  downscaling is precisely where GIF detail is normally thrown away.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from . import estimate, media
from .models import Crop, RenderResult, RenderSpec


def build_filter_chain(spec: RenderSpec, source_width: int, source_height: int) -> str:
    """Shared filter graph used identically by both palette passes.

    Both passes must see pixel-for-pixel identical frames, otherwise the palette
    generated in pass one does not match the frames it is applied to in pass two.
    """
    parts = []

    if spec.speed != 1.0:
        # Retime before resampling, so `fps` lands on the final cadence.
        parts.append(f"setpts=PTS/{spec.speed:g}")

    parts.append(f"fps={spec.fps}")

    if spec.dedupe_active():
        # hi/lo/frac tuned to catch true duplicates without eating slow pans.
        parts.append("mpdecimate=hi=64*12:lo=64*5:frac=0.1")

    if spec.crop is not None:
        crop = spec.crop.clamped(source_width, source_height)
        parts.append(f"crop={crop.width}:{crop.height}:{crop.x}:{crop.y}")

    width, height = spec.even_size()
    parts.append(f"scale={width}:{height}:flags=lanczos")

    if spec.sharpen > 0:
        # Applied after the downscale, where softening actually happened, and on
        # luma only — sharpening chroma just amplifies quantisation noise.
        parts.append(f"unsharp=5:5:{spec.sharpen:g}:5:5:0")

    chain = ",".join(parts)

    if spec.boomerang:
        # Play forward then backward. `trim` drops the reversed copy's first
        # frame, which is the forward pass's last frame, so the turn does not
        # stutter on a repeated image.
        chain += (
            ",split[fwd][rev];"
            "[rev]reverse,trim=start_frame=1,setpts=N/FRAME_RATE/TB[back];"
            "[fwd][back]concat=n=2:v=1"
        )

    return chain


def build_palettegen_args(
    spec: RenderSpec, source: Path, palette: Path, source_width: int, source_height: int
) -> list[str]:
    """Pass 1 — derive an optimal palette for the clip.

    `stats_mode=diff` weights pixels that change between frames, so a limited
    palette gets spent on the moving subject rather than on a large static
    background.
    """
    settings = spec.resolved()
    chain = build_filter_chain(spec, source_width, source_height)
    return [
        media.find_binary("ffmpeg"),
        "-hide_banner", "-v", "error", "-nostdin",
        "-ss", f"{spec.start:.3f}",
        "-t", f"{spec.duration:.3f}",
        "-i", str(source),
        "-vf", f"{chain},palettegen=max_colors={settings['colors']}:stats_mode=diff",
        "-y", str(palette),
    ]


def build_paletteuse_args(
    spec: RenderSpec, source: Path, palette: Path, out: Path, source_width: int, source_height: int
) -> list[str]:
    """Pass 2 — map frames onto the palette and write the GIF.

    `diff_mode=rectangle` confines each frame's rewrite to the bounding box that
    actually changed, which is a large size win at no cost in quality.
    """
    settings = spec.resolved()
    chain = build_filter_chain(spec, source_width, source_height)

    dither = settings["dither"]
    use_opts = [f"dither={dither}"]
    if dither == "bayer":
        use_opts.append(f"bayer_scale={settings['bayer_scale']}")
    use_opts.append("diff_mode=rectangle")

    argv = [
        media.find_binary("ffmpeg"),
        "-hide_banner", "-v", "error", "-nostdin",
        "-ss", f"{spec.start:.3f}",
        "-t", f"{spec.duration:.3f}",
        "-i", str(source),
        "-i", str(palette),
        "-lavfi", f"{chain}[v];[v][1:v]paletteuse={':'.join(use_opts)}",
        "-loop", "0" if spec.loop_forever else "-1",
    ]
    if spec.dedupe_active():
        # Without variable frame timing ffmpeg re-duplicates what mpdecimate
        # dropped. VFR instead lengthens the surviving frame's delay, so the
        # clip keeps its real-time duration.
        argv += ["-fps_mode", "vfr"]
    argv += ["-y", str(out)]
    return argv


def build_gifsicle_args(lossy: int, src: Path, dest: Path) -> list[str]:
    """`-O3` is a lossless cross-frame optimiser; `--lossy` trades detail for size."""
    argv = [media.find_binary("gifsicle"), "-O3", "--no-warnings"]
    if lossy > 0:
        argv.append(f"--lossy={lossy}")
    argv += [str(src), "-o", str(dest)]
    return argv


def fingerprint(spec: RenderSpec) -> str:
    """Short digest of every setting that changes the output bytes.

    Two different crops or quality settings must not share an output filename:
    the second render would overwrite the first, and because the browser sees
    the same URL it would keep showing the stale image.
    """
    payload = json.dumps(spec.model_dump(), sort_keys=True, default=str)
    return hashlib.sha1(payload.encode()).hexdigest()[:10]


def render(
    spec: RenderSpec,
    source: Path,
    out_dir: Path,
    source_width: int,
    source_height: int,
    on_progress=None,
) -> RenderResult:
    """Encode one GIF and return its exact measured properties."""
    started = time.monotonic()
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{spec.video_id}-{int(spec.start * 1000)}-{int(spec.duration * 1000)}-{spec.fps}-{fingerprint(spec)}"
    palette = out_dir / f"{stem}.palette.png"
    raw = out_dir / f"{stem}.raw.gif"
    final = out_dir / f"{stem}.gif"

    def progress(percent: float, message: str) -> None:
        if on_progress:
            on_progress(percent, message)

    settings = spec.resolved()
    commands: list[str] = []

    progress(10, "Building colour palette")
    argv = build_palettegen_args(spec, source, palette, source_width, source_height)
    commands.append(" ".join(argv))
    media.run(argv, timeout=600)

    progress(45, "Encoding frames")
    argv = build_paletteuse_args(spec, source, palette, raw, source_width, source_height)
    commands.append(" ".join(argv))
    media.run(argv, timeout=900)

    bytes_before = raw.stat().st_size

    progress(80, "Optimising")
    argv = build_gifsicle_args(settings["lossy"], raw, final)
    commands.append(" ".join(argv))
    try:
        media.run(argv, timeout=600)
        # gifsicle should never lose, but never ship a regression either.
        if final.stat().st_size > bytes_before:
            final.write_bytes(raw.read_bytes())
    except media.CommandFailed:
        # An optimisation failure must not cost the user their render.
        final.write_bytes(raw.read_bytes())

    progress(95, "Measuring")
    info = media.probe(final)
    frames = media.count_gif_frames(final)
    size = final.stat().st_size

    for scratch in (palette, raw):
        scratch.unlink(missing_ok=True)

    return RenderResult(
        name=final.name,
        gif_url=f"/api/gif/{final.name}",
        bytes=size,
        width=info.width or spec.even_size()[0],
        height=info.height or spec.even_size()[1],
        frames=frames,
        fps=spec.fps,
        # Playback length, which speed and boomerang divorce from the clip length.
        duration=round(info.duration or (frames / spec.fps if spec.fps else spec.duration), 3),
        elapsed_ms=int((time.monotonic() - started) * 1000),
        colors=settings["colors"],
        bytes_before_optimize=bytes_before,
        measured_bpp=estimate.measured_bpp(
            size, info.width or spec.width, info.height or spec.height, frames or 1
        ),
        commands=commands,
    )
