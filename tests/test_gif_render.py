"""Real ffmpeg + gifsicle runs against a synthetic clip. No network involved."""

from pathlib import Path

import pytest

from server import gif, media
from server.models import Crop, RenderSpec

from .conftest import SYNTHETIC_ID


def render(spec: RenderSpec, source: Path, out_dir: Path):
    return gif.render(spec, source, out_dir, 320, 240)


@pytest.fixture(scope="module")
def out_dir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("gifs")


def test_render_produces_a_real_gif_with_the_requested_geometry(synthetic_video, out_dir):
    spec = RenderSpec(video_id=SYNTHETIC_ID, start=0.5, duration=1.0, fps=10, width=160, height=120)
    result = render(spec, synthetic_video, out_dir)

    assert result.bytes > 0
    assert (result.width, result.height) == (160, 120)
    # 1.0s at 10fps, with a frame of tolerance for ffmpeg's boundary handling.
    assert 9 <= result.frames <= 11
    assert Path(str(out_dir / result.name)).read_bytes()[:6] in (b"GIF89a", b"GIF87a")


def test_cropping_changes_which_pixels_survive(synthetic_video, out_dir):
    """A crop of a moving test pattern must not encode to the same bytes as the full frame."""
    common = dict(video_id=SYNTHETIC_ID, start=0.5, duration=0.8, fps=10, width=120, height=120)
    full = render(RenderSpec(**common), synthetic_video, out_dir)
    cropped = render(
        RenderSpec(**common, crop=Crop(x=0, y=0, width=120, height=120)),
        synthetic_video,
        out_dir,
    )
    assert (out_dir / full.name).read_bytes() != (out_dir / cropped.name).read_bytes()


def test_output_dimensions_follow_the_spec_not_the_source(synthetic_video, out_dir):
    spec = RenderSpec(video_id=SYNTHETIC_ID, start=0.2, duration=0.6, fps=8, width=64, height=48)
    result = render(spec, synthetic_video, out_dir)
    assert (result.width, result.height) == (64, 48)


def test_higher_fps_yields_more_frames(synthetic_video, out_dir):
    slow = render(RenderSpec(video_id=SYNTHETIC_ID, duration=1.0, fps=5, width=96, height=72),
                  synthetic_video, out_dir)
    fast = render(RenderSpec(video_id=SYNTHETIC_ID, duration=1.0, fps=20, width=96, height=72),
                  synthetic_video, out_dir)
    assert fast.frames > slow.frames


def test_smaller_presets_produce_smaller_files(synthetic_video, out_dir):
    sizes = {}
    for preset in ("max", "balanced", "small", "tiny"):
        spec = RenderSpec(video_id=SYNTHETIC_ID, start=0.5, duration=1.0, fps=12,
                          width=160, height=120, preset=preset)
        sizes[preset] = render(spec, synthetic_video, out_dir).bytes

    assert sizes["max"] > sizes["tiny"], sizes
    assert sizes["balanced"] >= sizes["small"], sizes


def test_the_optimiser_never_makes_the_file_bigger(synthetic_video, out_dir):
    spec = RenderSpec(video_id=SYNTHETIC_ID, start=0.5, duration=1.0, fps=12, width=160, height=120)
    result = render(spec, synthetic_video, out_dir)
    assert result.bytes <= result.bytes_before_optimize


def test_intermediate_files_are_cleaned_up(synthetic_video, out_dir):
    spec = RenderSpec(video_id=SYNTHETIC_ID, start=1.0, duration=0.5, fps=10, width=80, height=60)
    result = render(spec, synthetic_video, out_dir)
    leftovers = [p.name for p in out_dir.iterdir() if ".raw." in p.name or ".palette." in p.name]
    assert leftovers == []
    assert (out_dir / result.name).is_file()


def test_measured_bpp_is_reported_and_plausible(synthetic_video, out_dir):
    spec = RenderSpec(video_id=SYNTHETIC_ID, start=0.5, duration=1.0, fps=12, width=160, height=120)
    result = render(spec, synthetic_video, out_dir)
    assert 0.01 < result.measured_bpp < 12


def test_a_bad_source_path_raises_a_command_failure(out_dir):
    spec = RenderSpec(video_id="nope", duration=0.5, fps=10, width=64, height=48)
    with pytest.raises(media.CommandFailed):
        render(spec, Path("/nonexistent/missing.mp4"), out_dir)
