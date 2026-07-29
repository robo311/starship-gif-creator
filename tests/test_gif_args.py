"""The ffmpeg invocations are the product here, so they are asserted directly."""

from pathlib import Path

from server import gif
from server.models import Crop, RenderSpec

SRC = Path("/tmp/in.mp4")
PAL = Path("/tmp/p.png")
OUT = Path("/tmp/out.gif")


def test_filter_order_is_fps_then_crop_then_scale():
    spec = RenderSpec(
        video_id="abc", fps=12, width=200, height=100,
        crop=Crop(x=10, y=20, width=640, height=320),
    )
    chain = gif.build_filter_chain(spec, 1280, 720)
    assert chain == "fps=12,crop=640:320:10:20,scale=200:100:flags=lanczos"


def test_crop_precedes_scale_so_the_kept_region_uses_the_full_output():
    spec = RenderSpec(video_id="abc", crop=Crop(x=0, y=0, width=100, height=100))
    chain = gif.build_filter_chain(spec, 1280, 720)
    assert chain.index("crop=") < chain.index("scale=")


def test_no_crop_filter_when_no_region_was_drawn():
    chain = gif.build_filter_chain(RenderSpec(video_id="abc"), 1280, 720)
    assert "crop=" not in chain
    assert chain.startswith("fps=")


def test_lanczos_is_always_used_for_scaling():
    chain = gif.build_filter_chain(RenderSpec(video_id="abc"), 1280, 720)
    assert "flags=lanczos" in chain


def test_out_of_bounds_crop_is_clamped_in_the_filter():
    spec = RenderSpec(video_id="abc", crop=Crop(x=1200, y=700, width=800, height=800))
    chain = gif.build_filter_chain(spec, 1280, 720)
    crop_args = [part for part in chain.split(",") if part.startswith("crop=")][0]
    width, height, x, y = (int(value) for value in crop_args.removeprefix("crop=").split(":"))
    assert x + width <= 1280
    assert y + height <= 720


def test_dedupe_adds_mpdecimate_and_variable_frame_timing():
    spec = RenderSpec(video_id="abc", dedupe=True)
    chain = gif.build_filter_chain(spec, 640, 480)
    assert "mpdecimate" in chain
    argv = gif.build_paletteuse_args(spec, SRC, PAL, OUT, 640, 480)
    assert "-fps_mode" in argv and argv[argv.index("-fps_mode") + 1] == "vfr"


def test_without_dedupe_frame_timing_stays_constant():
    argv = gif.build_paletteuse_args(RenderSpec(video_id="abc"), SRC, PAL, OUT, 640, 480)
    assert "-fps_mode" not in argv


def test_palettegen_uses_diff_stats_and_the_colour_budget():
    spec = RenderSpec(video_id="abc", colors=96)
    argv = gif.build_palettegen_args(spec, SRC, PAL, 640, 480)
    filters = argv[argv.index("-vf") + 1]
    assert "palettegen=max_colors=96:stats_mode=diff" in filters


def test_both_passes_see_an_identical_frame_pipeline():
    """A palette built from different frames than it is applied to shifts colours."""
    spec = RenderSpec(video_id="abc", fps=20, width=300, height=200,
                      crop=Crop(x=4, y=8, width=600, height=400), dedupe=True)
    chain = gif.build_filter_chain(spec, 1280, 720)
    pass1 = gif.build_palettegen_args(spec, SRC, PAL, 1280, 720)[
        gif.build_palettegen_args(spec, SRC, PAL, 1280, 720).index("-vf") + 1]
    pass2 = gif.build_paletteuse_args(spec, SRC, PAL, OUT, 1280, 720)[
        gif.build_paletteuse_args(spec, SRC, PAL, OUT, 1280, 720).index("-lavfi") + 1]
    assert pass1.startswith(chain)
    assert pass2.startswith(chain)


def test_paletteuse_carries_dither_and_rectangle_diffing():
    spec = RenderSpec(video_id="abc", dither="bayer", bayer_scale=4)
    argv = gif.build_paletteuse_args(spec, SRC, PAL, OUT, 640, 480)
    filters = argv[argv.index("-lavfi") + 1]
    assert "paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle" in filters


def test_bayer_scale_is_omitted_for_non_bayer_dithers():
    spec = RenderSpec(video_id="abc", dither="sierra2_4a", bayer_scale=4)
    filters = gif.build_paletteuse_args(spec, SRC, PAL, OUT, 640, 480)[
        gif.build_paletteuse_args(spec, SRC, PAL, OUT, 640, 480).index("-lavfi") + 1]
    assert "bayer_scale" not in filters
    assert "dither=sierra2_4a" in filters


def test_gif_loops_forever_by_default():
    argv = gif.build_paletteuse_args(RenderSpec(video_id="abc"), SRC, PAL, OUT, 640, 480)
    assert argv[argv.index("-loop") + 1] == "0"


def test_seek_and_trim_are_passed_before_the_input_for_a_fast_seek():
    spec = RenderSpec(video_id="abc", start=12.5, duration=2.25)
    argv = gif.build_palettegen_args(spec, SRC, PAL, 640, 480)
    assert argv.index("-ss") < argv.index("-i")
    assert argv[argv.index("-ss") + 1] == "12.500"
    assert argv[argv.index("-t") + 1] == "2.250"


def test_specs_that_differ_get_different_output_names():
    """Colliding names let a stale GIF stay visible at an unchanged URL."""
    base = RenderSpec(video_id="abc", start=1.0, duration=2.0, fps=15, width=200, height=200)
    variants = [
        base.model_copy(update={"crop": Crop(x=0, y=0, width=100, height=100)}),
        base.model_copy(update={"width": 300}),
        base.model_copy(update={"preset": "tiny"}),
        base.model_copy(update={"lossy": 200}),
        base.model_copy(update={"dedupe": True}),
    ]
    prints = {gif.fingerprint(spec) for spec in [base, *variants]}
    assert len(prints) == len(variants) + 1


def test_an_identical_spec_reuses_the_same_name():
    a = RenderSpec(video_id="abc", start=1.0, duration=2.0, fps=15)
    b = RenderSpec(video_id="abc", start=1.0, duration=2.0, fps=15)
    assert gif.fingerprint(a) == gif.fingerprint(b)


def test_gifsicle_optimises_losslessly_when_lossy_is_zero():
    argv = gif.build_gifsicle_args(0, OUT, OUT)
    assert "-O3" in argv
    assert not any(arg.startswith("--lossy") for arg in argv)


def test_gifsicle_receives_the_lossy_level():
    argv = gif.build_gifsicle_args(80, OUT, OUT)
    assert "--lossy=80" in argv
