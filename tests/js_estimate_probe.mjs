// Test fixture: evaluates web/js/estimate.js so the Python suite can compare
// the browser mirror against server/estimate.py. Reads a JSON array of cases on
// stdin, writes a JSON array of results on stdout.

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const { frameCount, bitsPerPixel, estimateBytes } = await import(
  join(here, '..', 'web', 'js', 'estimate.js')
);

const cases = JSON.parse(readFileSync(0, 'utf8'));

const results = cases.map((c) => {
  const frames = frameCount(c.duration, c.fps, c.speed ?? 1, c.boomerang ?? false);
  const bpp = bitsPerPixel(c);
  return {
    frames,
    bpp,
    bytes: estimateBytes(c.width, c.height, frames, bpp, c.calibration),
  };
});

process.stdout.write(JSON.stringify(results));
