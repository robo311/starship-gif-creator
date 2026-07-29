// Canvas crop overlay: draw a new region, move it, resize from eight handles.
//
// This module owns the pointer and canvas work; the rectangle maths it leans on
// lives in rect.js, which is DOM-free and therefore testable on its own.

import {
  MIN_SIDE, applyAspect, confine, contentBox, fitDrawn, insideBox, slide,
  toDisplay, toNormalised,
} from './rect.js';

const HIT = 10;           // px radius for grabbing a handle
const HANDLES = ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'];

const CURSORS = {
  nw: 'nwse-resize', se: 'nwse-resize',
  ne: 'nesw-resize', sw: 'nesw-resize',
  n: 'ns-resize', s: 'ns-resize',
  e: 'ew-resize', w: 'ew-resize',
  move: 'move',
};

export function initCrop({ canvas, video, getCrop, setCrop, getLockRatio, getTargetAspect }) {
  const ctx = canvas.getContext('2d');
  let drag = null;
  let hover = null;

  /** The video's visible content box in CSS pixels, accounting for letterboxing. */
  const contentRect = () =>
    contentBox(canvas.clientWidth, canvas.clientHeight, video.videoWidth, video.videoHeight);

  function pointerAt(event) {
    const bounds = canvas.getBoundingClientRect();
    return { x: event.clientX - bounds.left, y: event.clientY - bounds.top };
  }

  function handlePositions(rect) {
    const { x, y, w, h } = rect;
    return {
      nw: [x, y], n: [x + w / 2, y], ne: [x + w, y],
      e: [x + w, y + h / 2], se: [x + w, y + h],
      s: [x + w / 2, y + h], sw: [x, y + h], w: [x, y + h / 2],
    };
  }

  /** Which part of the crop, if any, sits under the pointer. */
  function hitTest(point) {
    const box = contentRect();
    const rect = toDisplay(getCrop(), box);
    if (!rect) return null;

    const positions = handlePositions(rect);
    for (const name of HANDLES) {
      const [hx, hy] = positions[name];
      if (Math.abs(point.x - hx) <= HIT && Math.abs(point.y - hy) <= HIT) return name;
    }
    const inside = point.x >= rect.x && point.x <= rect.x + rect.w
      && point.y >= rect.y && point.y <= rect.y + rect.h;
    return inside ? 'move' : null;
  }

  function resize(mode, origin, point, box) {
    let { x, y, w, h } = origin;
    const right = x + w;
    const bottom = y + h;

    if (mode.includes('w')) { x = Math.min(point.x, right - MIN_SIDE); w = right - x; }
    if (mode.includes('e')) { w = Math.max(MIN_SIDE, point.x - x); }
    if (mode.includes('n')) { y = Math.min(point.y, bottom - MIN_SIDE); h = bottom - y; }
    if (mode.includes('s')) { h = Math.max(MIN_SIDE, point.y - y); }

    // Truncate before locking the ratio, so the ratio is applied to the region
    // that actually survives rather than to one hanging off the frame.
    let rect = confine({ x, y, w, h }, box);
    if (getLockRatio()) rect = applyAspect(rect, getTargetAspect(), mode);
    return fitDrawn(rect, box);
  }

  // ── pointer handling ──────────────────────────────────────────────────

  canvas.addEventListener('pointerdown', (event) => {
    if (!video.videoWidth) return;
    canvas.setPointerCapture(event.pointerId);
    const point = pointerAt(event);
    const box = contentRect();
    const mode = hitTest(point);

    if (mode === 'move' || (mode && HANDLES.includes(mode))) {
      drag = { mode, origin: toDisplay(getCrop(), box), start: point, box };
    } else {
      // Fresh rectangle: anchor at the press point and grow south-east. Pressing
      // in a letterbox bar anchors at the nearest frame edge instead.
      const anchor = insideBox(point, box);
      drag = { mode: 'new', anchor, box };
      setCrop(toNormalised(fitDrawn({ x: anchor.x, y: anchor.y, w: MIN_SIDE, h: MIN_SIDE }, box), box));
    }
    draw();
  });

  canvas.addEventListener('pointermove', (event) => {
    const point = pointerAt(event);

    if (!drag) {
      const mode = hitTest(point);
      if (mode !== hover) {
        hover = mode;
        canvas.style.cursor = CURSORS[mode] || 'crosshair';
      }
      return;
    }

    const box = drag.box;
    if (drag.mode === 'new') {
      let rect = {
        x: Math.min(drag.anchor.x, point.x),
        y: Math.min(drag.anchor.y, point.y),
        w: Math.abs(point.x - drag.anchor.x),
        h: Math.abs(point.y - drag.anchor.y),
      };
      rect = confine(rect, box);
      if (getLockRatio()) {
        const anchor = (point.x < drag.anchor.x ? 'w' : 'e') + (point.y < drag.anchor.y ? 'n' : 's');
        rect = applyAspect(rect, getTargetAspect(), anchor);
      }
      setCrop(toNormalised(fitDrawn(rect, box), box));
    } else if (drag.mode === 'move') {
      const moved = {
        ...drag.origin,
        x: drag.origin.x + (point.x - drag.start.x),
        y: drag.origin.y + (point.y - drag.start.y),
      };
      setCrop(toNormalised(slide(moved, box), box));
    } else {
      setCrop(toNormalised(resize(drag.mode, drag.origin, point, box), box));
    }
    draw();
  });

  function endDrag(event) {
    if (!drag) return;
    if (drag.mode === 'new') {
      const box = drag.box;
      const rect = toDisplay(getCrop(), box);
      // A click rather than a drag means "no crop".
      if (!rect || rect.w <= MIN_SIDE * 1.5 || rect.h <= MIN_SIDE * 1.5) setCrop(null);
    }
    drag = null;
    if (event && canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
    draw();
  }

  canvas.addEventListener('pointerup', endDrag);
  canvas.addEventListener('pointercancel', endDrag);
  canvas.addEventListener('dblclick', () => { setCrop(null); draw(); });

  // ── drawing ───────────────────────────────────────────────────────────

  function draw() {
    const ratio = window.devicePixelRatio || 1;
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
    }
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, width, height);

    const box = contentRect();
    const rect = toDisplay(getCrop(), box);
    if (!rect || !video.videoWidth) return;

    // Dim everything outside the crop.
    ctx.fillStyle = 'rgba(5, 7, 10, 0.62)';
    ctx.beginPath();
    ctx.rect(box.left, box.top, box.width, box.height);
    ctx.rect(rect.x, rect.y, rect.w, rect.h);
    ctx.fill('evenodd');

    // Thirds guides, faint enough to compose against without distraction.
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.16)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let i = 1; i < 3; i += 1) {
      const gx = rect.x + (rect.w * i) / 3;
      const gy = rect.y + (rect.h * i) / 3;
      ctx.moveTo(gx, rect.y); ctx.lineTo(gx, rect.y + rect.h);
      ctx.moveTo(rect.x, gy); ctx.lineTo(rect.x + rect.w, gy);
    }
    ctx.stroke();

    ctx.strokeStyle = '#ff6b4a';
    ctx.lineWidth = 1.5;
    ctx.strokeRect(rect.x + 0.5, rect.y + 0.5, rect.w - 1, rect.h - 1);

    ctx.fillStyle = '#ff6b4a';
    ctx.strokeStyle = '#0a0c0f';
    for (const [px, py] of Object.values(handlePositions(rect))) {
      ctx.beginPath();
      ctx.rect(px - 3.5, py - 3.5, 7, 7);
      ctx.fill();
      ctx.stroke();
    }

    // Size readout in source pixels, flipped inside the rect near the top edge.
    const crop = getCrop();
    const label = `${Math.round(crop.w * video.videoWidth)} × ${Math.round(crop.h * video.videoHeight)}`;
    ctx.font = '600 11px ui-monospace, SFMono-Regular, Menlo, monospace';
    const textWidth = ctx.measureText(label).width;
    const labelY = rect.y > 22 ? rect.y - 20 : rect.y + 4;
    ctx.fillStyle = 'rgba(10, 12, 15, 0.85)';
    ctx.fillRect(rect.x, labelY, textWidth + 12, 16);
    ctx.fillStyle = '#ffd9cf';
    ctx.fillText(label, rect.x + 6, labelY + 12);
  }

  new ResizeObserver(draw).observe(canvas);
  video.addEventListener('loadedmetadata', draw);

  return { draw };
}
