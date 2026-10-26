"""Following a target through a clip, so the crop can move with it.

The user frames the subject; the middle of that frame is what gets tracked,
since a crop drawn around a person is mostly the person near its centre and
mostly background near its edges. OpenCV's CSRT tracker follows it frame by
frame on a downscaled copy of the video, which is plenty for placing a crop.

A raw tracker path wobbles by a pixel or two every frame. In a GIF that reads
as camera shake, so the path is smoothed and thinned before it is returned:
the crop should glide after the subject, not twitch with it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from .jobs import check_cancelled
from .models import Crop, TrackPoint

ANALYSIS_WIDTH = 640     # frames are tracked at this width at most
ANALYSIS_FPS = 15        # frames per second actually fed to the tracker
SMOOTH_SECONDS = 0.6     # width of the moving average over the raw path
POINTS_PER_SECOND = 5    # density of the returned path
TARGET_SHARE = 0.6       # middle share of the crop that is tracked


class TrackingUnavailable(RuntimeError):
    pass


def _tracker():
    try:
        import cv2
    except ImportError as exc:  # surfaced to the user as an install hint
        raise TrackingUnavailable(
            "Target following needs OpenCV. Install it with: "
            "pip install opencv-contrib-python-headless"
        ) from exc
    if hasattr(cv2, "TrackerCSRT"):
        return cv2, cv2.TrackerCSRT.create()
    if hasattr(cv2, "TrackerCSRT_create"):
        return cv2, cv2.TrackerCSRT_create()
    # The base OpenCV wheel ships only MIL, which drifts but still beats nothing.
    return cv2, cv2.TrackerMIL.create()


def smooth(values: list[float], window: int) -> list[float]:
    """Centred moving average that narrows towards the ends.

    The window stays symmetric even there: a one-sided window would average a
    moving subject against where it used to be, leaving the crop trailing it.
    """
    if window <= 1 or len(values) < 3:
        return list(values)
    half = window // 2
    out = []
    for i in range(len(values)):
        reach = min(half, i, len(values) - 1 - i)
        out.append(sum(values[i - reach:i + reach + 1]) / (2 * reach + 1))
    return out


def thin(times: list[float], xs: list[float], ys: list[float], per_second: float) -> list[TrackPoint]:
    """Keep about `per_second` points, always including the first and last."""
    if not times:
        return []
    keep = [0]
    gap = 1.0 / per_second
    for i in range(1, len(times) - 1):
        if times[i] - times[keep[-1]] >= gap:
            keep.append(i)
    if len(times) > 1:
        keep.append(len(times) - 1)
    return [
        TrackPoint(t=round(times[i], 3), x=round(min(1, max(0, xs[i])), 5), y=round(min(1, max(0, ys[i])), 5))
        for i in keep
    ]


def _frames(capture, cv2, start: float, end: float, stride: int):
    """(source seconds, frame) pairs across [start, end], every `stride`th frame."""
    capture.set(cv2.CAP_PROP_POS_MSEC, start * 1000)
    while True:
        ok, frame = capture.read()
        if not ok:
            return
        moment = capture.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
        if moment > end + 0.05:
            return
        yield moment, frame
        # `grab` skips converting frames the tracker will not look at.
        for _ in range(stride - 1):
            if not capture.grab():
                return


def follow(
    source: Path,
    start: float,
    duration: float,
    crop: Crop,
    anchor: float | None = None,
    on_progress: Callable[[float, str], None] | None = None,
) -> list[TrackPoint]:
    """The crop centre's path, in source seconds and 0..1 coordinates.

    `crop` frames the target as it looks at `anchor` — the frame the user was
    on when they framed it. The tracker runs forward from there to the end, and
    a second one backward to the start over frames kept from the way in.
    """
    cv2, _ = _tracker()
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError("Could not open the video for tracking.")

    end = start + duration
    anchor = start if anchor is None else min(max(anchor, start), end)

    try:
        source_fps = capture.get(cv2.CAP_PROP_FPS) or 25.0
        stride = max(1, round(source_fps / ANALYSIS_FPS))
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if not width or not height:
            raise RuntimeError("Could not read the video's frame size.")
        scale = min(1.0, ANALYSIS_WIDTH / width)
        small = (max(1, round(width * scale)), max(1, round(height * scale)))

        def shrink(image):
            return image if scale == 1.0 else cv2.resize(image, small, interpolation=cv2.INTER_AREA)

        clamped = crop.clamped(width, height)
        centre = ((clamped.x + clamped.width / 2) / width, (clamped.y + clamped.height / 2) / height)
        box_w = max(16 / scale, clamped.width * TARGET_SHARE)
        box_h = max(16 / scale, clamped.height * TARGET_SHARE)
        box = (
            int((centre[0] * width - box_w / 2) * scale),
            int((centre[1] * height - box_h / 2) * scale),
            max(8, int(box_w * scale)),
            max(8, int(box_h * scale)),
        )

        def step(tracker, image, last):
            found, (x, y, w, h) = tracker.update(image)
            if not found:
                # Lost: hold still rather than leap, and pick up again if found.
                return last
            return ((x + w / 2) / scale / width, (y + h / 2) / scale / height)

        before: list[tuple[float, object]] = []   # frames ahead of the anchor
        forward: list[tuple[float, tuple[float, float]]] = []
        anchor_image = None
        tracker = None
        half_step = stride / source_fps / 2

        for count, (moment, frame) in enumerate(_frames(capture, cv2, start, end, stride)):
            check_cancelled()
            image = shrink(frame)
            if anchor_image is None and moment < anchor - half_step:
                before.append((moment, image))
                continue
            if anchor_image is None:
                anchor_image = image
                _, tracker = _tracker()
                tracker.init(image, box)
                forward.append((moment, centre))
            else:
                forward.append((moment, step(tracker, image, forward[-1][1])))
            if on_progress and count % 5 == 0:
                done = min(1.0, (moment - start) / duration) if duration else 1.0
                on_progress(5 + done * 85, f"Following the target · {moment - start:.1f} s of {duration:.1f} s")

        if anchor_image is None:
            # The anchor fell past the last readable frame: use the last one.
            if not before:
                raise RuntimeError("Could not read any frames of the clip.")
            moment, anchor_image = before.pop()
            forward.append((moment, centre))

        backward: list[tuple[float, tuple[float, float]]] = []
        if before:
            if on_progress:
                on_progress(92, "Following the target back to the start")
            _, tracker = _tracker()
            tracker.init(anchor_image, box)
            last = centre
            for moment, image in reversed(before):
                check_cancelled()
                last = step(tracker, image, last)
                backward.append((moment, last))
            backward.reverse()
    finally:
        capture.release()

    path = backward + forward
    times = [moment for moment, _ in path]
    window = max(1, round(SMOOTH_SECONDS * ANALYSIS_FPS))
    xs = smooth([point[0] for _, point in path], window)
    ys = smooth([point[1] for _, point in path], window)
    return thin(times, xs, ys, POINTS_PER_SECOND)
