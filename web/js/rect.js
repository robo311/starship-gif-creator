// Rectangle maths for the crop overlay, kept free of the DOM so it can be
// exercised headlessly (see tests/test_crop_geometry.py).
//
// Everything here works in *displayed content pixels* — the box the video frame
// actually occupies inside its element. Because that box is a uniform scale of
// the source frame, a ratio measured in it equals the ratio in source pixels.

export const MIN_SIDE = 8;   // ignore accidental micro-drags

/** Where the frame sits inside `boxWidth` × `boxHeight` under `object-fit: contain`. */
export function contentBox(boxWidth, boxHeight, videoWidth, videoHeight) {
  if (!videoWidth || !videoHeight) {
    return { left: 0, top: 0, width: boxWidth, height: boxHeight };
  }
  const videoRatio = videoWidth / videoHeight;
  const boxRatio = boxWidth / boxHeight;
  const width = videoRatio > boxRatio ? boxWidth : boxHeight * videoRatio;
  const height = videoRatio > boxRatio ? boxWidth / videoRatio : boxHeight;
  return { left: (boxWidth - width) / 2, top: (boxHeight - height) / 2, width, height };
}

/**
 * Cut a rectangle off at the content box's edges.
 *
 * For drawing and resizing, where every edge is under the user's direct
 * control. Sliding the rectangle inwards instead — the obvious thing to write —
 * silently moves the region away from the one that was drawn as soon as the drag
 * crosses the frame edge, so you do not get what you drew.
 */
export function confine(rect, box) {
  const left = Math.max(rect.x, box.left);
  const top = Math.max(rect.y, box.top);
  const right = Math.min(rect.x + rect.w, box.left + box.width);
  const bottom = Math.min(rect.y + rect.h, box.top + box.height);
  return {
    x: left,
    y: top,
    w: Math.max(MIN_SIDE, right - left),
    h: Math.max(MIN_SIDE, bottom - top),
  };
}

/** Push a rectangle inside the box without resizing it — right for a move. */
export function slide(rect, box) {
  const w = Math.min(rect.w, box.width);
  const h = Math.min(rect.h, box.height);
  return {
    x: Math.max(box.left, Math.min(rect.x, box.left + box.width - w)),
    y: Math.max(box.top, Math.min(rect.y, box.top + box.height - h)),
    w,
    h,
  };
}

/** Truncate a drawn rectangle, then rescue the degenerate case where the
 *  minimum size pushed it back out past an edge. */
export const fitDrawn = (rect, box) => slide(confine(rect, box), box);

/** Clamp a point into the content box, so pressing in a letterbox bar anchors
 *  at the nearest frame edge rather than outside the picture. */
export const insideBox = (point, box) => ({
  x: Math.max(box.left, Math.min(point.x, box.left + box.width)),
  y: Math.max(box.top, Math.min(point.y, box.top + box.height)),
});

/**
 * Force `rect` to `aspect` by shrinking whichever dimension is too generous,
 * anchored so the edge the user is dragging stays put.
 */
export function applyAspect(rect, aspect, anchor) {
  if (!aspect || !isFinite(aspect)) return rect;
  let { x, y, w, h } = rect;
  if (w / h > aspect) w = h * aspect;
  else h = w / aspect;

  if (anchor.includes('w')) x = rect.x + rect.w - w;
  if (anchor.includes('n')) y = rect.y + rect.h - h;
  if (anchor === 'n' || anchor === 's') x = rect.x + (rect.w - w) / 2;
  if (anchor === 'e' || anchor === 'w') y = rect.y + (rect.h - h) / 2;
  return { x, y, w, h };
}

/** Normalised 0..1 crop -> display pixels. */
export const toDisplay = (crop, box) => crop && ({
  x: box.left + crop.x * box.width,
  y: box.top + crop.y * box.height,
  w: crop.w * box.width,
  h: crop.h * box.height,
});

/** Display pixels -> the normalised 0..1 crop held in application state. */
export const toNormalised = (rect, box) => ({
  x: (rect.x - box.left) / box.width,
  y: (rect.y - box.top) / box.height,
  w: rect.w / box.width,
  h: rect.h / box.height,
});
