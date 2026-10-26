"""Target following: a bright square crosses a dark frame and the crop keeps up."""

import subprocess
from pathlib import Path

import pytest

from server import gif, track
from server.models import Crop, RenderSpec

from .conftest import _ffmpeg

pytest.importorskip("cv2")


@pytest.fixture(scope="module")
def moving_square(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("track") / "move.mp4"
    subprocess.run([
        _ffmpeg(), "-hide_banner", "-v", "error",
        "-f", "lavfi", "-i", "color=0x202020:s=320x240:r=25:d=4",
        "-f", "lavfi", "-i", "color=white:s=40x40:r=25:d=4",
        "-filter_complex", "[0][1]overlay=x='20+t*60':y='60+t*20',noise=alls=10:allf=t",
        "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", "-y", str(path),
    ], check=True, capture_output=True)
    return path


def test_the_path_follows_the_moving_square(moving_square):
    points = track.follow(moving_square, 0.0, 3.0, Crop(x=0, y=40, width=80, height=80))
    assert points[0].t == 0.0 and points[-1].t > 2.5
    for point in points:
        # The square's centre is at (40 + 60t, 80 + 20t) pixels.
        assert abs(point.x * 320 - (40 + 60 * point.t)) < 16
        assert abs(point.y * 240 - (80 + 20 * point.t)) < 16


def test_a_followed_crop_keeps_the_square_in_the_middle(moving_square, tmp_path):
    points = track.follow(moving_square, 0.0, 3.0, Crop(x=0, y=40, width=80, height=80))
    spec = RenderSpec(video_id="mv", fps=10, width=80, height=80, preset="max", duration=3,
                      crop=Crop(x=0, y=40, width=80, height=80), track=points)
    result = gif.render(spec, moving_square, tmp_path, 320, 240)
    raw = subprocess.run(
        [_ffmpeg(), "-v", "error", "-i", str(tmp_path / result.name), "-fps_mode", "passthrough",
         "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True, check=True,
    ).stdout
    side = 80 * 80
    for k in range(len(raw) // side):
        frame = raw[k * side:(k + 1) * side]
        lit = [i for i, value in enumerate(frame) if value > 200]
        assert lit, f"square left the crop in frame {k}"
        assert abs(sum(i % 80 for i in lit) / len(lit) - 40) < 16
        assert abs(sum(i // 80 for i in lit) / len(lit) - 40) < 16


def test_smoothing_does_not_drag_a_steady_motion_behind():
    line = [float(i) for i in range(20)]
    assert track.smooth(line, 7) == line


def test_thinning_keeps_both_ends():
    times = [i / 15 for i in range(46)]
    points = track.thin(times, [0.5] * 46, [0.5] * 46, 5)
    assert points[0].t == 0 and points[-1].t == round(times[-1], 3)
    assert len(points) <= 17


def test_framing_mid_clip_tracks_both_ways(moving_square):
    # At 1.5 s the square's centre is at (130, 110).
    points = track.follow(moving_square, 0.0, 3.0, Crop(x=90, y=70, width=80, height=80), anchor=1.5)
    assert points[0].t < 0.2 and points[-1].t > 2.5
    for point in points:
        assert abs(point.x * 320 - (40 + 60 * point.t)) < 16
        assert abs(point.y * 240 - (80 + 20 * point.t)) < 16
