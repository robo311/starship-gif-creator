"""Speed, sharpening, boomerang and loop behaviour."""

from pathlib import Path

import pytest

from server import estimate, gif, media
from server.models import RenderSpec

from .conftest import SYNTHETIC_ID

SRC = Path("/tmp/in.mp4")
PAL = Path("/tmp/p.png")
OUT = Path("/tmp/out.gif")


# ── filter graph construction ────────────────────────────────────────────

def test_speed_retimes_before_the_frame_rate_is_resampled():
    chain = gif.build_filter_chain(RenderSpec(video_id="a", speed=2.0, fps=15), 640, 480)
    assert chain.startswith("setpts=PTS/2")
    assert chain.index("setpts") < chain.index("fps=")


def test_normal_speed_adds_no_retiming_filter():
    chain = gif.build_filter_chain(RenderSpec(video_id="a", speed=1.0), 640, 480)
    assert "setpts" not in chain


def test_sharpening_is_applied_after_the_downscale():
    chain = gif.build_filter_chain(RenderSpec(video_id="a", sharpen=0.8), 640, 480)
    assert "unsharp=5:5:0.8:5:5:0" in chain
    assert chain.index("scale=") < chain.index("unsharp=")


def test_sharpening_leaves_chroma_untouched():
    chain = gif.build_filter_chain(RenderSpec(video_id="a", sharpen=1.5), 640, 480)
    unsharp = [part for part in chain.split(",") if part.startswith("unsharp")][0]
    assert unsharp.endswith(":0"), "the chroma amount must stay at zero"


def test_zero_sharpen_adds_no_filter():
    assert "unsharp" not in gif.build_filter_chain(RenderSpec(video_id="a"), 640, 480)


def test_boomerang_reverses_and_concatenates():
    chain = gif.build_filter_chain(RenderSpec(video_id="a", boomerang=True), 640, 480)
    assert "split[fwd][rev]" in chain
    assert "reverse" in chain
    assert "concat=n=2:v=1" in chain


def test_boomerang_drops_the_repeated_turning_frame():
    chain = gif.build_filter_chain(RenderSpec(video_id="a", boomerang=True), 640, 480)
    assert "trim=start_frame=1" in chain


def test_boomerang_graph_is_valid_for_both_passes():
    """Pass one appends a filter; pass two appends a label. Both must parse."""
    spec = RenderSpec(video_id="a", boomerang=True)
    palettegen = gif.build_palettegen_args(spec, SRC, PAL, 640, 480)
    paletteuse = gif.build_paletteuse_args(spec, SRC, PAL, OUT, 640, 480)
    assert palettegen[palettegen.index("-vf") + 1].endswith("stats_mode=diff")
    assert "concat=n=2:v=1[v];[v][1:v]paletteuse" in paletteuse[paletteuse.index("-lavfi") + 1]


def test_boomerang_wins_over_frame_dropping():
    """mpdecimate's uneven timing would break reverse's timestamp renumbering."""
    spec = RenderSpec(video_id="a", boomerang=True, dedupe=True)
    assert spec.dedupe_active() is False
    chain = gif.build_filter_chain(spec, 640, 480)
    assert "mpdecimate" not in chain
    argv = gif.build_paletteuse_args(spec, SRC, PAL, OUT, 640, 480)
    assert "-fps_mode" not in argv


def test_dedupe_still_applies_without_a_boomerang():
    assert RenderSpec(video_id="a", dedupe=True).dedupe_active() is True


def test_loop_once_is_encoded_as_minus_one():
    argv = gif.build_paletteuse_args(RenderSpec(video_id="a", loop_forever=False), SRC, PAL, OUT, 640, 480)
    assert argv[argv.index("-loop") + 1] == "-1"


# ── the size model must know about the new options ───────────────────────

def test_speed_shortens_the_clip_and_the_frame_count():
    assert estimate.frame_count(4.0, 10, speed=2.0) == 20
    assert estimate.frame_count(4.0, 10, speed=0.5) == 80


def test_boomerang_almost_doubles_the_frame_count():
    plain = estimate.frame_count(2.0, 10)
    assert estimate.frame_count(2.0, 10, boomerang=True) == plain * 2 - 1


def test_sharpening_raises_the_predicted_size():
    plain = estimate.bits_per_pixel("bayer", 3, 200, 40)
    sharp = estimate.bits_per_pixel("bayer", 3, 200, 40, sharpen=1.0)
    assert sharp > plain


def test_for_spec_accounts_for_speed_and_boomerang():
    base = RenderSpec(video_id="a", duration=2.0, fps=10, width=100, height=100)
    boomed = base.model_copy(update={"boomerang": True})
    assert estimate.for_spec(boomed)["frames"] > estimate.for_spec(base)["frames"]
    assert estimate.for_spec(boomed)["bytes"] > estimate.for_spec(base)["bytes"]


# ── real renders ─────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def out_dir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("effects")


def render(spec, source, out_dir):
    return gif.render(spec, source, out_dir, 320, 240)


def test_boomerang_really_produces_a_there_and_back_gif(synthetic_video, out_dir):
    common = dict(video_id=SYNTHETIC_ID, start=0.5, duration=1.0, fps=10, width=96, height=72)
    plain = render(RenderSpec(**common), synthetic_video, out_dir)
    boomed = render(RenderSpec(**common, boomerang=True), synthetic_video, out_dir)
    assert boomed.frames == plain.frames * 2 - 1, (plain.frames, boomed.frames)


def test_doubling_the_speed_halves_the_frames(synthetic_video, out_dir):
    common = dict(video_id=SYNTHETIC_ID, start=0.5, duration=2.0, fps=10, width=96, height=72)
    normal = render(RenderSpec(**common), synthetic_video, out_dir)
    fast = render(RenderSpec(**common, speed=2.0), synthetic_video, out_dir)
    assert fast.frames == pytest.approx(normal.frames / 2, abs=2)


def test_sharpening_renders_and_changes_the_bytes(synthetic_video, out_dir):
    common = dict(video_id=SYNTHETIC_ID, start=0.5, duration=0.8, fps=10, width=96, height=72)
    plain = render(RenderSpec(**common), synthetic_video, out_dir)
    sharp = render(RenderSpec(**common, sharpen=1.2), synthetic_video, out_dir)
    assert sharp.bytes > 0
    assert (out_dir / sharp.name).read_bytes() != (out_dir / plain.name).read_bytes()


def test_a_render_with_every_option_at_once_still_works(synthetic_video, out_dir):
    spec = RenderSpec(
        video_id=SYNTHETIC_ID, start=0.4, duration=1.2, fps=12, width=128, height=96,
        speed=1.5, boomerang=True, sharpen=0.6, preset="small", loop_forever=False,
    )
    result = render(spec, synthetic_video, out_dir)
    assert result.bytes > 0
    assert (result.width, result.height) == (128, 96)
    assert result.frames > 1
