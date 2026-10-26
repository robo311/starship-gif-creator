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


# ── sequences ─────────────────────────────────────────────────────────────

def _sequence(*ranges, **extra):
    return RenderSpec(
        video_id="abc",
        segments=[{"start": start, "duration": duration} for start, duration in ranges],
        **extra,
    )


def test_each_part_of_a_sequence_gets_its_own_seeked_input():
    argv = gif.build_palettegen_args(_sequence((40.0, 1.5), (2.0, 2.0)), SRC, PAL, 640, 480)
    seeks = [argv[i + 1] for i, arg in enumerate(argv) if arg == "-ss"]
    assert seeks == ["40.000", "2.000"]
    assert argv.count("-i") == 2


def test_parts_are_joined_in_list_order():
    spec = _sequence((40.0, 1.5), (2.0, 2.0))
    graph = gif.build_graph(spec, 640, 480)
    assert "[c0][c1]concat=n=2:v=1:a=0" in graph


def test_each_part_is_cropped_to_its_own_region_before_the_join():
    spec = RenderSpec(video_id="abc", width=200, height=200, segments=[
        {"start": 0, "duration": 1, "crop": {"x": 10, "y": 0, "width": 300, "height": 300}},
        {"start": 5, "duration": 1, "crop": {"x": 300, "y": 100, "width": 300, "height": 300}},
    ])
    graph = gif.build_graph(spec, 640, 480)
    first, second = graph.split(";")[:2]
    assert "crop=300:300:10:0" in first and first.endswith("[c0]")
    assert "crop=300:300:300:100" in second and second.endswith("[c1]")


def test_parts_of_a_different_shape_are_padded_not_stretched():
    graph = gif.build_graph(_sequence((0, 1), (5, 1), width=200, height=100), 640, 480)
    assert "force_original_aspect_ratio=decrease" in graph
    assert "pad=200:100" in graph


def test_a_single_part_keeps_its_own_crop():
    spec = RenderSpec(video_id="abc", segments=[
        {"start": 1, "duration": 1, "crop": {"x": 8, "y": 4, "width": 100, "height": 50}},
    ])
    assert "crop=100:50:8:4" in gif.build_filter_chain(spec, 640, 480)


def test_the_palette_is_the_input_after_the_last_part():
    argv = gif.build_paletteuse_args(_sequence((0, 1), (5, 1), (9, 1)), SRC, PAL, OUT, 640, 480)
    assert "[v][3:v]paletteuse" in argv[argv.index("-lavfi") + 1]


def test_a_single_part_sequence_stays_a_simple_chain():
    argv = gif.build_palettegen_args(_sequence((7.0, 2.0)), SRC, PAL, 640, 480)
    assert "-vf" in argv and "concat" not in argv[argv.index("-vf") + 1]
    assert argv[argv.index("-ss") + 1] == "7.000"


def test_a_sequence_reports_its_total_length_as_the_duration():
    spec = _sequence((40.0, 1.5), (2.0, 2.0))
    assert spec.duration == 3.5
    assert spec.start == 40.0


def test_reordering_parts_changes_the_output_name():
    a = _sequence((0, 1), (5, 1))
    b = _sequence((5, 1), (0, 1))
    assert gif.fingerprint(a) != gif.fingerprint(b)


def test_a_tracked_crop_moves_along_its_path_in_output_time():
    spec = RenderSpec(
        video_id="abc", start=10, duration=2, speed=2.0,
        crop=Crop(x=0, y=0, width=100, height=100),
        track=[{"t": 10, "x": 0.25, "y": 0.5}, {"t": 12, "x": 0.75, "y": 0.5}],
    )
    chain = gif.build_filter_chain(spec, 400, 200)
    # Centre 100 -> 300 px, so x 50 -> 250 over source 10-12 s = output 0-1 s.
    assert "crop=100:100:x='50.00+200.0000*clip(t-0.0000,0,1.0000)'" in chain
    assert ":y='50.00'" in chain


def test_zoom_in_grows_from_one_to_the_amount_over_the_clip():
    spec = RenderSpec(video_id="abc", duration=1, fps=10, zoom={"direction": "in", "amount": 1.5})
    chain = gif.build_filter_chain(spec, 640, 480)
    assert "zoompan=z='1+0.5000*on/9'" in chain
    assert chain.index("zoompan") < chain.index("scale=")


def test_zoom_out_starts_close_and_ends_wide():
    spec = RenderSpec(video_id="abc", duration=1, fps=10, zoom={"direction": "out", "amount": 2})
    assert "z='2.0000-1.0000*on/9'" in gif.build_filter_chain(spec, 640, 480)


# ── MP4 ───────────────────────────────────────────────────────────────────

def test_mp4_is_one_h264_pass_on_the_same_frames():
    spec = RenderSpec(video_id="abc", format="mp4", preset="high", crop=Crop(x=0, y=0, width=200, height=100))
    argv = gif.build_mp4_args(spec, SRC, OUT, 640, 480)
    assert argv[argv.index("-vf") + 1] == gif.build_graph(spec, 640, 480)
    assert argv[argv.index("-c:v") + 1] == "libx264"
    assert argv[argv.index("-crf") + 1] == "21"
    assert argv[argv.index("-pix_fmt") + 1] == "yuv420p"
    assert "-an" in argv and "+faststart" in argv


def test_mp4_uses_a_bitrate_instead_of_quality_when_fitting():
    argv = gif.build_mp4_args(RenderSpec(video_id="abc", format="mp4", bitrate_kbps=800), SRC, OUT, 640, 480)
    assert argv[argv.index("-b:v") + 1] == "800k" and "-crf" not in argv


def test_an_mp4_sequence_joins_its_parts_too():
    spec = RenderSpec(video_id="abc", format="mp4", segments=[{"start": 5, "duration": 1}, {"start": 0, "duration": 1}])
    argv = gif.build_mp4_args(spec, SRC, OUT, 640, 480)
    assert "concat=n=2" in argv[argv.index("-filter_complex") + 1]


def test_gif_and_mp4_of_the_same_clip_get_different_names():
    assert gif.fingerprint(RenderSpec(video_id="abc")) != gif.fingerprint(RenderSpec(video_id="abc", format="mp4"))


def test_source_quality_mp4_is_near_lossless_and_takes_its_time():
    argv = gif.build_mp4_args(RenderSpec(video_id="abc", format="mp4", mp4_quality="source"), SRC, OUT, 640, 480)
    assert argv[argv.index("-crf") + 1] == "15"
    assert argv[argv.index("-preset") + 1] == "slow"


def test_a_1440p_frame_at_60_fps_is_a_valid_spec():
    spec = RenderSpec(video_id="abc", format="mp4", width=2560, height=1440, fps=60)
    assert spec.even_size() == (2560, 1440)


# ── sharpness: no resampling across a rounding gap ────────────────────────

def test_a_crop_a_pixel_off_the_output_is_resized_not_rescaled():
    spec = RenderSpec(video_id="abc", width=1002, height=702, crop=Crop(x=301, y=201, width=1001, height=701))
    chain = gif.build_filter_chain(spec, 1920, 1080)
    assert "crop=1002:702:301:201" in chain
    assert "scale=" not in chain


def test_a_snapped_crop_stays_inside_the_frame():
    spec = RenderSpec(video_id="abc", width=642, height=480, crop=Crop(x=0, y=0, width=641, height=480))
    assert "crop=642:480:0:0" in gif.build_filter_chain(spec, 642, 480)
    edge = RenderSpec(video_id="abc", width=642, height=480, crop=Crop(x=1, y=0, width=641, height=480))
    assert "crop=642:480:0:0" in gif.build_filter_chain(edge, 642, 480)


def test_a_real_size_change_still_scales_with_lanczos():
    spec = RenderSpec(video_id="abc", width=480, height=270, crop=Crop(x=0, y=0, width=1280, height=720))
    assert "scale=480:270:flags=lanczos" in gif.build_filter_chain(spec, 1920, 1080)


def test_full_frame_at_native_size_is_not_scaled():
    assert "scale=" not in gif.build_filter_chain(RenderSpec(video_id="abc", width=1920, height=1080), 1920, 1080)


def test_source_quality_at_the_source_rate_keeps_fractional_timing():
    spec = RenderSpec(video_id="abc", format="mp4", mp4_quality="source", fps=24, source_fps=23.976)
    assert "fps=" not in gif.build_filter_chain(spec, 640, 480)
    # A deliberately different rate, or ordinary quality, still converts.
    assert "fps=12" in gif.build_filter_chain(spec.model_copy(update={"fps": 12}), 640, 480)
    assert "fps=24" in gif.build_filter_chain(spec.model_copy(update={"mp4_quality": "ladder"}), 640, 480)


def test_an_explicit_mp4_level_wins_over_the_gif_ladder():
    spec = RenderSpec(video_id="abc", format="mp4", preset="tiny", mp4_crf=18)
    argv = gif.build_mp4_args(spec, SRC, OUT, 640, 480)
    assert argv[argv.index("-crf") + 1] == "18"
