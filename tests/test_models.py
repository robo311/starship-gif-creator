import pytest
from pydantic import ValidationError

from server.models import PRESETS, Crop, RenderSpec


def test_crop_is_clamped_into_the_frame():
    clamped = Crop(x=300, y=200, width=500, height=500).clamped(320, 240)
    assert clamped.x + clamped.width <= 320
    assert clamped.y + clamped.height <= 240


def test_clamping_keeps_a_crop_that_starts_beyond_the_frame_usable():
    clamped = Crop(x=5000, y=5000, width=10, height=10).clamped(320, 240)
    assert clamped.width >= 2 and clamped.height >= 2
    assert clamped.x < 320 and clamped.y < 240


def test_a_crop_already_inside_the_frame_is_untouched():
    original = Crop(x=10, y=20, width=100, height=80)
    assert original.clamped(320, 240) == original


def test_zero_sized_crops_are_rejected():
    with pytest.raises(ValidationError):
        Crop(x=0, y=0, width=0, height=10)


def test_even_size_rounds_odd_dimensions_down():
    spec = RenderSpec(video_id="abc", width=481, height=271)
    assert spec.even_size() == (480, 270)


def test_overrides_win_over_the_preset():
    spec = RenderSpec(video_id="abc", preset="tiny", colors=256, lossy=0)
    resolved = spec.resolved()
    assert resolved["colors"] == 256
    assert resolved["lossy"] == 0
    # Untouched keys still come from the preset.
    assert resolved["dither"] == PRESETS["tiny"]["dither"]


def test_unknown_preset_and_dither_are_rejected():
    with pytest.raises(ValidationError):
        RenderSpec(video_id="abc", preset="ultra")
    with pytest.raises(ValidationError):
        RenderSpec(video_id="abc", dither="magic")


def test_presets_get_progressively_smaller():
    order = ["max", "balanced", "small", "tiny"]
    colors = [PRESETS[name]["colors"] for name in order]
    lossy = [PRESETS[name]["lossy"] for name in order]
    assert colors == sorted(colors, reverse=True)
    assert lossy == sorted(lossy)


def test_duration_bounds_are_enforced():
    with pytest.raises(ValidationError):
        RenderSpec(video_id="abc", duration=0)
    with pytest.raises(ValidationError):
        RenderSpec(video_id="abc", duration=61)
