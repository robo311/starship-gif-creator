from server import estimate


def test_frame_count_rounds_half_up_to_match_the_browser_mirror():
    # 18.25 * 2 is exactly 36.5. Python's round() would answer 36 (banker's
    # rounding to even); JavaScript's Math.round answers 37. The mirror in
    # web/js/estimate.js must agree, so half-up is the required behaviour.
    assert estimate.frame_count(18.25, 2) == 37
    assert estimate.frame_count(3.0, 12) == 36
    assert estimate.frame_count(2.5, 15) == 38
    assert estimate.frame_count(0.0, 15) == 1, "a clip always has at least one frame"


def test_bayer_dithering_predicts_smaller_files_than_error_diffusion():
    bayer = estimate.bits_per_pixel("bayer", 4, 256, 0)
    sierra = estimate.bits_per_pixel("sierra2_4a", 4, 256, 0)
    assert bayer < sierra


def test_coarser_bayer_scale_predicts_smaller_files():
    scales = [estimate.bits_per_pixel("bayer", n, 256, 0) for n in range(6)]
    assert scales == sorted(scales, reverse=True)


def test_fewer_colours_and_more_lossy_both_shrink_the_prediction():
    baseline = estimate.bits_per_pixel("bayer", 3, 256, 0)
    assert estimate.bits_per_pixel("bayer", 3, 64, 0) < baseline
    assert estimate.bits_per_pixel("bayer", 3, 256, 100) < baseline


def test_sharpening_raises_the_prediction_only_slightly():
    plain = estimate.bits_per_pixel("bayer", 3, 200, 40)
    sharp = estimate.bits_per_pixel("bayer", 3, 200, 40, sharpen=2.0)
    # Measured at roughly +10% across the full sharpen range.
    assert 1.05 < sharp / plain < 1.15


def test_lossy_zero_leaves_the_base_untouched():
    assert estimate.bits_per_pixel("bayer", 3, 256, 0) == estimate.BAYER_SCALE_BPP[3]


def test_the_lossy_curve_saturates_rather_than_collapsing():
    """Measured: gifsicle --lossy removes about a third at most, not 85%."""
    base = estimate.bits_per_pixel("bayer", 3, 200, 0)
    at_40 = estimate.bits_per_pixel("bayer", 3, 200, 40)
    at_300 = estimate.bits_per_pixel("bayer", 3, 200, 300)
    assert 0.76 < at_40 / base < 0.84
    assert 0.60 < at_300 / base < 0.70
    # Most of the benefit must arrive early.
    assert (base - at_40) > (at_40 - at_300)


def test_estimate_scales_linearly_with_pixels_and_frames():
    small = estimate.estimate_bytes(100, 100, 10, 1.0) - estimate.HEADER_BYTES
    doubled_frames = estimate.estimate_bytes(100, 100, 20, 1.0) - estimate.HEADER_BYTES
    doubled_width = estimate.estimate_bytes(200, 100, 10, 1.0) - estimate.HEADER_BYTES
    assert doubled_frames == small * 2
    assert doubled_width == small * 2


def test_degenerate_dimensions_estimate_zero():
    assert estimate.estimate_bytes(0, 100, 10, 1.0) == 0
    assert estimate.estimate_bytes(100, 100, 0, 1.0) == 0


def test_calibration_multiplies_the_estimate():
    plain = estimate.estimate_bytes(200, 200, 30, 1.0)
    doubled = estimate.estimate_bytes(200, 200, 30, 1.0, calibration=2.0)
    assert (doubled - estimate.HEADER_BYTES) == (plain - estimate.HEADER_BYTES) * 2


def test_measured_bpp_inverts_the_estimate():
    width, height, frames, bpp = 320, 240, 40, 1.35
    size = estimate.estimate_bytes(width, height, frames, bpp)
    recovered = estimate.measured_bpp(size, width, height, frames)
    assert abs(recovered - bpp) < 0.001


def test_calibration_ratio_is_clamped_against_wild_renders():
    # A render 100x larger than predicted must not poison later estimates.
    huge = estimate.calibration_ratio(50_000_000, 100, 100, 10, 0.5)
    tiny = estimate.calibration_ratio(801, 100, 100, 10, 5.0)
    assert huge == 4.0
    assert tiny == 0.25


def test_calibration_ratio_is_one_when_the_model_was_right():
    width, height, frames, bpp = 320, 240, 40, 1.2
    size = estimate.estimate_bytes(width, height, frames, bpp)
    assert abs(estimate.calibration_ratio(size, width, height, frames, bpp) - 1.0) < 0.01
