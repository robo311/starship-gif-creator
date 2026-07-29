"""The browser mirrors the size arithmetic for instant slider feedback.

Two implementations of the same formula will drift apart unless something pins
them together. That is this test's only job.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from server import estimate

PROBE = Path(__file__).parent / "js_estimate_probe.mjs"

CASES = [
    {"duration": 3.0, "fps": 15, "width": 480, "height": 270,
     "dither": "bayer", "bayerScale": 3, "colors": 200, "lossy": 40},
    {"duration": 18.25, "fps": 2, "width": 320, "height": 240,
     "dither": "bayer", "bayerScale": 5, "colors": 64, "lossy": 120},
    {"duration": 0.5, "fps": 30, "width": 800, "height": 450,
     "dither": "sierra2_4a", "bayerScale": 3, "colors": 256, "lossy": 0},
    {"duration": 7.4, "fps": 12, "width": 240, "height": 240,
     "dither": "none", "bayerScale": 0, "colors": 128, "lossy": 80},
    {"duration": 2.5, "fps": 15, "width": 640, "height": 360,
     "dither": "floyd_steinberg", "bayerScale": 2, "colors": 96, "lossy": 25},
    {"duration": 4.0, "fps": 20, "width": 500, "height": 280,
     "dither": "bayer", "bayerScale": 4, "colors": 200, "lossy": 40,
     "calibration": 1.85},
    # speed, boomerang and sharpening all feed the same arithmetic
    {"duration": 6.0, "fps": 15, "width": 400, "height": 400,
     "dither": "bayer", "bayerScale": 3, "colors": 200, "lossy": 40,
     "speed": 2.0, "boomerang": True, "sharpen": 1.2},
    {"duration": 1.75, "fps": 24, "width": 320, "height": 180,
     "dither": "sierra2_4a", "bayerScale": 0, "colors": 256, "lossy": 0,
     "speed": 0.5, "boomerang": False, "sharpen": 0.4, "calibration": 0.6},
    {"duration": 3.0, "fps": 12, "width": 240, "height": 320,
     "dither": "none", "bayerScale": 0, "colors": 64, "lossy": 150,
     "speed": 1.25, "boomerang": True, "sharpen": 2.0},
]


@pytest.fixture(scope="module")
def js_results() -> list[dict]:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed, cannot verify the browser mirror")
    proc = subprocess.run(
        [node, str(PROBE)],
        input=json.dumps(CASES),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_frame_counts_agree(js_results):
    for case, js in zip(CASES, js_results):
        python = estimate.frame_count(
            case["duration"], case["fps"], case.get("speed", 1.0), case.get("boomerang", False)
        )
        assert python == js["frames"], case


def test_bits_per_pixel_agree(js_results):
    for case, js in zip(CASES, js_results):
        python = estimate.bits_per_pixel(
            case["dither"], case["bayerScale"], case["colors"], case["lossy"],
            case.get("sharpen", 0.0),
        )
        assert abs(python - js["bpp"]) < 1e-9, case


def test_estimated_bytes_agree(js_results):
    for case, js in zip(CASES, js_results):
        frames = estimate.frame_count(
            case["duration"], case["fps"], case.get("speed", 1.0), case.get("boomerang", False)
        )
        bpp = estimate.bits_per_pixel(
            case["dither"], case["bayerScale"], case["colors"], case["lossy"],
            case.get("sharpen", 0.0),
        )
        python = estimate.estimate_bytes(
            case["width"], case["height"], frames, bpp, case.get("calibration")
        )
        assert python == js["bytes"], case
