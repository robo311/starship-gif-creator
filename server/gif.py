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
from .models import MP4_CRF, MP4_SOURCE_CRF, Crop, RenderResult, RenderSpec, Segment


def keeps_source_timing(spec: RenderSpec) -> bool:
    """Source-quality MP4 at the source's own rate keeps its exact frames.

    The rate setting is a whole number, but most video runs at 23.976, 29.97 or
    59.94 fps. Forcing 24, 30 or 60 repeats a frame every few seconds, a visible
    hitch, so when the request is "the source's rate" the frames pass untouched.
    """
    return (
        spec.format == "mp4"
        and spec.mp4_quality == "source"
        and spec.source_fps is not None
        and abs(spec.fps - spec.source_fps) < 0.51
    )


def _retime(spec: RenderSpec) -> list[str]:
    parts = []
    if spec.speed != 1.0:
        # Retime before resampling, so `fps` lands on the final cadence.
        parts.append(f"setpts=PTS/{spec.speed:g}")
    if not keeps_source_timing(spec):
        parts.append(f"fps={spec.fps}")
    return parts


# A region this close to the output size is the same size, rounded to the even
# numbers video needs. Scaling across that gap shifts every pixel by a fraction
# and visibly softens the whole frame, so the region is resized instead.
SNAP_PIXELS = 2


def _snapped(crop: Crop | None, width: int, height: int, source_width: int, source_height: int) -> Crop | None:
    """The crop, nudged to exactly the output size when it is only a rounding away.

    Returns the region unchanged when it is genuinely a different size, or
    when the output cannot fit inside the frame at that position.
    """
    region = crop.clamped(source_width, source_height) if crop else Crop(
        x=0, y=0, width=source_width, height=source_height
    )
    if (region.width, region.height) == (width, height):
        return crop
    if abs(region.width - width) > SNAP_PIXELS or abs(region.height - height) > SNAP_PIXELS:
        return crop
    if width > source_width or height > source_height:
        return crop
    return Crop(
        x=min(region.x, source_width - width),
        y=min(region.y, source_height - height),
        width=width,
        height=height,
    )


def _dedupe(spec: RenderSpec) -> list[str]:
    # hi/lo/frac tuned to catch true duplicates without eating slow pans.
    return ["mpdecimate=hi=64*12:lo=64*5:frac=0.1"] if spec.dedupe_active() else []


def _path_expr(times: list[float], values: list[float]) -> str:
    """A piecewise-linear function of `t` through the given points.

    Written as a flat sum of ramps, `v0 + sum(slope * clip(t - ti, 0, span))`,
    rather than nested ifs: hundreds of points stay one shallow expression, and
    times before the first point or after the last hold the end values.
    """
    terms = [f"{values[0]:.2f}"]
    for i in range(len(times) - 1):
        span = times[i + 1] - times[i]
        rise = values[i + 1] - values[i]
        if span <= 1e-6 or abs(rise) < 0.01:
            continue
        terms.append(f"{rise / span:+.4f}*clip(t-{times[i]:.4f},0,{span:.4f})")
    return "".join(terms)


def _crop_filter(spec: RenderSpec, clip: Segment, source_width: int, source_height: int) -> str | None:
    """A fixed crop, or one whose position follows the clip's tracked path."""
    if clip.crop is None:
        return None
    crop = clip.crop.clamped(source_width, source_height)
    points = sorted(clip.track, key=lambda point: point.t)
    if len(points) < 2:
        return f"crop={crop.width}:{crop.height}:{crop.x}:{crop.y}"

    # Path times are source seconds; the crop sees output time, which starts at
    # the clip's start and runs `speed` times faster.
    rate = spec.speed or 1.0
    times = [(point.t - clip.start) / rate for point in points]
    xs = [min(max(point.x * source_width - crop.width / 2, 0), source_width - crop.width) for point in points]
    ys = [min(max(point.y * source_height - crop.height / 2, 0), source_height - crop.height) for point in points]
    return (
        f"crop={crop.width}:{crop.height}"
        f":x='{_path_expr(times, xs)}':y='{_path_expr(times, ys)}'"
    )


def _zoom_filter(spec: RenderSpec, clip: Segment, width: int, height: int) -> str | None:
    """Push in or pull out evenly over the clip, centred on the framed region.

    `zoompan` is fed one frame per output frame (`d=1`), so it only animates the
    framing and never invents frames. It renders at the region's own size; the
    scale that follows takes it to the output size with the usual lanczos.
    """
    if clip.zoom is None:
        return None
    frames = max(2, estimate.frame_count(clip.duration, spec.fps, spec.speed))
    amount = clip.zoom.amount
    progress = f"on/{frames - 1}"
    level = (
        f"1+{amount - 1:.4f}*{progress}" if clip.zoom.direction == "in"
        else f"{amount:.4f}-{amount - 1:.4f}*{progress}"
    )
    width -= width % 2
    height -= height % 2
    return (
        f"zoompan=z='{level}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2'"
        f":d=1:s={width}x{height}:fps={spec.fps}"
    )


def _frame(
    spec: RenderSpec, clip: Segment, source_width: int, source_height: int, fit: bool = False
) -> list[str]:
    """Crop (fixed or following a target), zoom, then scale to the output size.

    `fit` scales within the output box and pads the rest, for a sequence whose
    parts might not share one shape: concat needs equal sizes, and padding is
    better than stretching a part that does not match.
    """
    parts = []
    width, height = spec.even_size()
    snapped = _snapped(clip.crop, width, height, source_width, source_height)
    if snapped is not clip.crop:
        clip = clip.model_copy(update={"crop": snapped})
    crop = _crop_filter(spec, clip, source_width, source_height)
    if crop:
        parts.append(crop)
    region = clip.crop.clamped(source_width, source_height) if clip.crop else None
    region_size = (region.width, region.height) if region else (source_width, source_height)
    zoom = _zoom_filter(spec, clip, *region_size)
    if zoom:
        parts.append(zoom)
    if region_size == (width, height):
        # Pixel for pixel already: any scaler would only resample, never help.
        if fit:
            parts.append("setsar=1")
        return parts
    if fit:
        parts += [
            f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos",
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
            "setsar=1",
        ]
    else:
        parts.append(f"scale={width}:{height}:flags=lanczos")
    return parts


def _finish(spec: RenderSpec) -> list[str]:
    if spec.sharpen > 0:
        # Applied after the downscale, where softening actually happened, and on
        # luma only — sharpening chroma just amplifies quantisation noise.
        return [f"unsharp=5:5:{spec.sharpen:g}:5:5:0"]
    return []


def _boomerang(spec: RenderSpec) -> str:
    if not spec.boomerang:
        return ""
    # Play forward then backward. `trim` drops the reversed copy's first
    # frame, which is the forward pass's last frame, so the turn does not
    # stutter on a repeated image.
    return (
        ",split[fwd][rev];"
        "[rev]reverse,trim=start_frame=1,setpts=N/FRAME_RATE/TB[back];"
        "[fwd][back]concat=n=2:v=1"
    )


def build_filter_chain(spec: RenderSpec, source_width: int, source_height: int) -> str:
    """Shared filter graph used identically by both palette passes.

    Both passes must see pixel-for-pixel identical frames, otherwise the palette
    generated in pass one does not match the frames it is applied to in pass two.
    """
    parts = [
        *_retime(spec),
        *_dedupe(spec),
        *_frame(spec, spec.clips()[0], source_width, source_height),
        *_finish(spec),
    ]
    return ",".join(parts) + _boomerang(spec)


def build_input_args(spec: RenderSpec, source: Path) -> list[str]:
    """One seeked input per clip.

    Seeking and trimming before `-i` lets ffmpeg jump straight to each part
    instead of decoding everything in between, which matters when a sequence
    takes one part from the start of a long video and another from its end.
    """
    argv: list[str] = []
    for clip in spec.clips():
        argv += ["-ss", f"{clip.start:.3f}", "-t", f"{clip.duration:.3f}", "-i", str(source)]
    return argv


def build_graph(spec: RenderSpec, source_width: int, source_height: int) -> str:
    """The filter chain, or for a sequence, every part framed and then joined.

    Each part is retimed and cropped on its own, since each frames its own
    region. Everything after the join — duplicate dropping, sharpening, the
    boomerang — treats the sequence as the one clip it has become.
    """
    clips = spec.clips()
    if len(clips) == 1:
        return build_filter_chain(spec, source_width, source_height)
    branches = []
    for i, clip in enumerate(clips):
        # Each seeked input starts near zero, but not exactly; rebasing them
        # keeps concat from leaving a gap or overlap at the joins.
        steps = [
            "setpts=PTS-STARTPTS",
            *_retime(spec),
            *_frame(spec, clip, source_width, source_height, fit=True),
        ]
        branches.append(f"[{i}:v]{','.join(steps)}[c{i}];")
    joined = "".join(f"[c{i}]" for i in range(len(clips)))
    tail = [*_dedupe(spec), *_finish(spec)]
    graph = f"{''.join(branches)}{joined}concat=n={len(clips)}:v=1:a=0"
    if tail:
        graph += "," + ",".join(tail)
    return graph + _boomerang(spec)


def build_palettegen_args(
    spec: RenderSpec, source: Path, palette: Path, source_width: int, source_height: int
) -> list[str]:
    """Pass 1 — derive an optimal palette for the clip.

    `stats_mode=diff` weights pixels that change between frames, so a limited
    palette gets spent on the moving subject rather than on a large static
    background.
    """
    settings = spec.resolved()
    graph = build_graph(spec, source_width, source_height)
    # A lone clip is a simple filter chain; a sequence has several inputs.
    flag = "-vf" if len(spec.clips()) == 1 else "-filter_complex"
    return [
        media.find_binary("ffmpeg"),
        "-hide_banner", "-v", "error", "-nostdin",
        *build_input_args(spec, source),
        flag, f"{graph},palettegen=max_colors={settings['colors']}:stats_mode=diff",
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
    graph = build_graph(spec, source_width, source_height)
    palette_input = len(spec.clips())

    dither = settings["dither"]
    use_opts = [f"dither={dither}"]
    if dither == "bayer":
        use_opts.append(f"bayer_scale={settings['bayer_scale']}")
    use_opts.append("diff_mode=rectangle")

    argv = [
        media.find_binary("ffmpeg"),
        "-hide_banner", "-v", "error", "-nostdin",
        *build_input_args(spec, source),
        "-i", str(palette),
        "-lavfi", f"{graph}[v];[v][{palette_input}:v]paletteuse={':'.join(use_opts)}",
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


def build_mp4_args(
    spec: RenderSpec, source: Path, out: Path, source_width: int, source_height: int
) -> list[str]:
    """One pass to H.264: the same frames as the GIF, without a palette.

    Constant quality by default, mapped from the preset ladder. A bitrate is
    used only to land on a file size, where it is the one dial that is exact.
    `yuv420p` and `faststart` are what browsers, phones and chat apps expect.
    """
    graph = build_graph(spec, source_width, source_height)
    flag = "-vf" if len(spec.clips()) == 1 else "-filter_complex"
    argv = [
        media.find_binary("ffmpeg"),
        "-hide_banner", "-v", "error", "-nostdin",
        *build_input_args(spec, source),
        flag, graph,
        "-an",
        "-c:v", "libx264",
        "-preset", "slow" if spec.mp4_quality == "source" else "medium",
        "-pix_fmt", "yuv420p",
    ]
    if spec.bitrate_kbps:
        rate = spec.bitrate_kbps
        argv += ["-b:v", f"{rate}k", "-maxrate", f"{int(rate * 1.2)}k", "-bufsize", f"{rate * 2}k"]
    elif spec.mp4_quality == "source":
        argv += ["-crf", str(MP4_SOURCE_CRF)]
    elif spec.mp4_crf is not None:
        argv += ["-crf", str(spec.mp4_crf)]
    else:
        argv += ["-crf", str(MP4_CRF[spec.preset])]
    if spec.dedupe_active():
        # Keep the dropped frames dropped; their neighbours simply last longer.
        argv += ["-fps_mode", "vfr"]
    argv += ["-movflags", "+faststart", "-y", str(out)]
    return argv


def render_mp4(
    spec: RenderSpec, source: Path, out_dir: Path, source_width: int, source_height: int, on_progress=None
) -> RenderResult:
    """Encode the clip as a silent MP4 and measure it."""
    started = time.monotonic()
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{spec.video_id}-{int(spec.start * 1000)}-{int(spec.duration * 1000)}-{spec.fps}-{fingerprint(spec)}"
    final = out_dir / f"{stem}.mp4"
    if on_progress:
        on_progress(15, "Encoding MP4")
    argv = build_mp4_args(spec, source, final, source_width, source_height)
    try:
        media.run(argv, timeout=900)
    except BaseException:
        final.unlink(missing_ok=True)
        raise
    if on_progress:
        on_progress(95, "Measuring")
    info = media.probe(final)
    frames = media.count_gif_frames(final)
    size = final.stat().st_size
    width, height = spec.even_size()
    return RenderResult(
        name=final.name,
        format="mp4",
        gif_url=f"/api/gif/{final.name}",
        bytes=size,
        width=info.width or width,
        height=info.height or height,
        frames=frames,
        fps=spec.fps,
        duration=round(info.duration or (frames / spec.fps if spec.fps else spec.duration), 3),
        elapsed_ms=int((time.monotonic() - started) * 1000),
        colors=0,
        bytes_before_optimize=size,
        measured_bpp=estimate.measured_bpp(size, info.width or width, info.height or height, frames or 1),
        commands=[" ".join(argv)],
    )


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
    """Encode one GIF (or MP4) and return its exact measured properties."""
    if spec.format == "mp4":
        return render_mp4(spec, source, out_dir, source_width, source_height, on_progress)
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

    try:
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
    except BaseException:
        # A cancelled or failed encode must not leave its scratch, or a half
        # written GIF that a later identical render would mistake for finished.
        for scratch in (palette, raw, final):
            scratch.unlink(missing_ok=True)
        raise

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
