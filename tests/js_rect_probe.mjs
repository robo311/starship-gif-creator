// Test fixture: evaluates web/js/rect.js so the Python suite can check the crop
// rectangle maths without a browser. Reads a JSON array of cases on stdin,
// writes a JSON array of results on stdout.
//
// Each case names an operation and its arguments:
//   {op: "contentBox", boxWidth, boxHeight, videoWidth, videoHeight}
//   {op: "confine"|"slide"|"fitDrawn", rect, box}
//   {op: "insideBox", point, box}
//   {op: "applyAspect", rect, aspect, anchor}
//   {op: "draw", from, to, box}          -- a whole press-drag-release gesture
//   {op: "toSource", rect, box, frame}   -- display pixels -> source pixels

import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { readFileSync } from 'node:fs';

const here = dirname(fileURLToPath(import.meta.url));
const rect = await import(join(here, '..', 'web', 'js', 'rect.js'));

/** Mirrors crop.js's fresh-rectangle gesture: anchor at `from`, drag to `to`. */
function draw(from, to, box) {
  const anchor = rect.insideBox(from, box);
  const drawn = {
    x: Math.min(anchor.x, to.x),
    y: Math.min(anchor.y, to.y),
    w: Math.abs(to.x - anchor.x),
    h: Math.abs(to.y - anchor.y),
  };
  return rect.fitDrawn(rect.confine(drawn, box), box);
}

/** Mirrors state.js's cropPixels(): normalise, then scale to source pixels. */
function toSource(r, box, frame) {
  const n = rect.toNormalised(r, box);
  return {
    x: Math.round(n.x * frame.width),
    y: Math.round(n.y * frame.height),
    width: Math.max(2, Math.round(n.w * frame.width)),
    height: Math.max(2, Math.round(n.h * frame.height)),
  };
}

const results = JSON.parse(readFileSync(0, 'utf8')).map((c) => {
  switch (c.op) {
    case 'contentBox':
      return rect.contentBox(c.boxWidth, c.boxHeight, c.videoWidth, c.videoHeight);
    case 'confine':
      return rect.confine(c.rect, c.box);
    case 'slide':
      return rect.slide(c.rect, c.box);
    case 'fitDrawn':
      return rect.fitDrawn(c.rect, c.box);
    case 'insideBox':
      return rect.insideBox(c.point, c.box);
    case 'applyAspect':
      return rect.applyAspect(c.rect, c.aspect, c.anchor);
    case 'draw':
      return draw(c.from, c.to, c.box);
    case 'toSource':
      return toSource(c.rect, c.box, c.frame);
    default:
      throw new Error(`unknown op ${c.op}`);
  }
});

process.stdout.write(JSON.stringify(results));
