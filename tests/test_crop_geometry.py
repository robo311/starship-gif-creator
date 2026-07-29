"""The crop rectangle maths, exercised headlessly through web/js/rect.js.

These live here rather than in a browser test because the bug they pin is pure
arithmetic: a drag that leaves the frame used to *slide* the whole rectangle
inwards, so the region encoded was not the region drawn.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

PROBE = Path(__file__).parent / "js_rect_probe.mjs"

# A 3:2 source inside a 16:9 element, which is what produced the original report.
WIDE_BOX = {"boxWidth": 1218, "boxHeight": 684, "videoWidth": 1620, "videoHeight": 1080}
# The same source in an element shaped to match it: no bars at all.
SNUG_BOX = {"boxWidth": 628, "boxHeight": 418, "videoWidth": 1620, "videoHeight": 1080}


def run_probe(cases: list[dict]) -> list[dict]:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed, cannot exercise the browser module")
    proc = subprocess.run(
        [node, str(PROBE)],
        input=json.dumps(cases),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def one(case: dict) -> dict:
    return run_probe([case])[0]


def content(box: dict) -> dict:
    return one({"op": "contentBox", **box})


# --------------------------------------------------------------------------- #
# the content box


def test_a_wider_element_pillarboxes_the_frame():
    box = content(WIDE_BOX)
    assert box["height"] == 684
    assert box["width"] == pytest.approx(1026)     # 684 * 3/2
    assert box["left"] == pytest.approx(96)
    assert box["top"] == 0


def test_a_taller_element_letterboxes_the_frame():
    box = content({"boxWidth": 600, "boxHeight": 600, "videoWidth": 1620, "videoHeight": 1080})
    assert box["width"] == 600
    assert box["height"] == pytest.approx(400)
    assert box["left"] == 0
    assert box["top"] == pytest.approx(100)


def test_a_matching_element_leaves_no_bars():
    box = content(SNUG_BOX)
    assert box["left"] == pytest.approx(0.5, abs=0.5)
    assert box["top"] == 0


def test_an_unloaded_video_falls_back_to_the_whole_element():
    box = content({"boxWidth": 400, "boxHeight": 300, "videoWidth": 0, "videoHeight": 0})
    assert (box["left"], box["top"], box["width"], box["height"]) == (0, 0, 400, 300)


# --------------------------------------------------------------------------- #
# the regression: a drag that leaves the frame


def test_a_drag_past_the_edge_is_truncated_not_slid():
    """The whole point. Dragging out past the right edge must keep the left edge
    where it was pressed and stop at the frame, not shift the region sideways."""
    box = content(WIDE_BOX)
    drawn = one({
        "op": "draw",
        "from": {"x": 1000, "y": 100},
        "to": {"x": 1250, "y": 500},        # 128 px beyond the frame's right edge
        "box": box,
    })
    assert drawn["x"] == pytest.approx(1000), "the pressed edge moved"
    assert drawn["x"] + drawn["w"] == pytest.approx(box["left"] + box["width"]), \
        "the rectangle did not stop at the frame edge"


def test_a_truncated_drag_reaches_the_frames_last_pixel_in_source_terms():
    box = content(WIDE_BOX)
    drawn = one({
        "op": "draw",
        "from": {"x": 1000, "y": 100},
        "to": {"x": 1250, "y": 500},
        "box": box,
    })
    crop = one({
        "op": "toSource", "rect": drawn, "box": box,
        "frame": {"width": 1620, "height": 1080},
    })
    assert crop["x"] + crop["width"] == 1620
    assert crop["x"] == 1427          # (1000 - 96) / 1026 * 1620, to the nearest pixel


def test_every_edge_truncates():
    box = content(WIDE_BOX)
    right, bottom = box["left"] + box["width"], box["top"] + box["height"]
    cases = [
        ({"x": -400, "y": 100}, {"x": 300, "y": 400}, "left"),
        ({"x": 300, "y": -400}, {"x": 600, "y": 400}, "top"),
        ({"x": 900, "y": 100}, {"x": 5000, "y": 400}, "right"),
        ({"x": 300, "y": 400}, {"x": 600, "y": 5000}, "bottom"),
    ]
    for start, end, edge in cases:
        drawn = one({"op": "draw", "from": start, "to": end, "box": box})
        assert drawn["x"] >= box["left"] - 1e-9, edge
        assert drawn["y"] >= box["top"] - 1e-9, edge
        assert drawn["x"] + drawn["w"] <= right + 1e-9, edge
        assert drawn["y"] + drawn["h"] <= bottom + 1e-9, edge


def test_pressing_in_a_letterbox_bar_anchors_at_the_frame_edge():
    box = content(WIDE_BOX)
    drawn = one({
        "op": "draw",
        "from": {"x": 20, "y": 200},       # inside the left bar, outside the picture
        "to": {"x": 400, "y": 500},
        "box": box,
    })
    assert drawn["x"] == pytest.approx(box["left"])


def test_a_drag_wholly_outside_the_frame_stays_inside_it():
    box = content(WIDE_BOX)
    drawn = one({"op": "draw", "from": {"x": 1150, "y": 200}, "to": {"x": 1210, "y": 400}, "box": box})
    assert drawn["x"] + drawn["w"] <= box["left"] + box["width"] + 1e-9
    assert drawn["w"] >= 8      # never collapses to nothing


# --------------------------------------------------------------------------- #
# moving is the one case that *should* slide


def test_moving_a_crop_keeps_its_size_and_stays_inside():
    box = content(WIDE_BOX)
    moved = one({
        "op": "slide",
        "rect": {"x": 1000, "y": 600, "w": 300, "h": 200},   # pushed off two edges
        "box": box,
    })
    assert (moved["w"], moved["h"]) == (300, 200), "a move must not resize"
    assert moved["x"] + moved["w"] == pytest.approx(box["left"] + box["width"])
    assert moved["y"] + moved["h"] == pytest.approx(box["top"] + box["height"])


def test_a_crop_larger_than_the_frame_is_reduced_to_the_frame():
    box = content(WIDE_BOX)
    moved = one({"op": "slide", "rect": {"x": -50, "y": -50, "w": 5000, "h": 5000}, "box": box})
    assert moved["w"] == pytest.approx(box["width"])
    assert moved["h"] == pytest.approx(box["height"])


# --------------------------------------------------------------------------- #
# ratio locking


@pytest.mark.parametrize("aspect", [1.0, 16 / 9, 9 / 16, 4 / 5])
def test_a_locked_ratio_is_honoured_exactly(aspect):
    got = one({
        "op": "applyAspect",
        "rect": {"x": 100, "y": 100, "w": 400, "h": 400},
        "aspect": aspect,
        "anchor": "se",
    })
    assert got["w"] / got["h"] == pytest.approx(aspect)


def test_locking_a_ratio_only_ever_shrinks():
    original = {"x": 100, "y": 100, "w": 400, "h": 400}
    for anchor in ("nw", "n", "ne", "e", "se", "s", "sw", "w"):
        got = one({"op": "applyAspect", "rect": original, "aspect": 16 / 9, "anchor": anchor})
        assert got["w"] <= original["w"] + 1e-9, anchor
        assert got["h"] <= original["h"] + 1e-9, anchor


def test_a_ratio_locked_drag_past_the_edge_still_fits_inside():
    box = content(WIDE_BOX)
    confined = one({
        "op": "fitDrawn",
        "rect": {"x": 900, "y": 100, "w": 400, "h": 225},
        "box": box,
    })
    assert confined["x"] + confined["w"] <= box["left"] + box["width"] + 1e-9


# --------------------------------------------------------------------------- #
# round tripping to source pixels


def test_a_full_frame_selection_maps_to_the_whole_source():
    box = content(SNUG_BOX)
    crop = one({
        "op": "toSource",
        "rect": {"x": box["left"], "y": box["top"], "w": box["width"], "h": box["height"]},
        "box": box,
        "frame": {"width": 1620, "height": 1080},
    })
    assert (crop["x"], crop["y"], crop["width"], crop["height"]) == (0, 0, 1620, 1080)


def test_a_centred_half_size_selection_maps_to_the_middle_of_the_source():
    box = content(SNUG_BOX)
    crop = one({
        "op": "toSource",
        "rect": {
            "x": box["left"] + box["width"] / 4,
            "y": box["top"] + box["height"] / 4,
            "w": box["width"] / 2,
            "h": box["height"] / 2,
        },
        "box": box,
        "frame": {"width": 1620, "height": 1080},
    })
    assert crop["x"] == pytest.approx(405, abs=1)
    assert crop["y"] == pytest.approx(270, abs=1)
    assert crop["width"] == pytest.approx(810, abs=1)
    assert crop["height"] == pytest.approx(540, abs=1)
