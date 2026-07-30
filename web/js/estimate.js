// Browser mirror of server/estimate.py, so the size readout tracks a dragged
// slider without a round trip per pixel. Only the formula lives here — the
// constants arrive from /api/health, and tests/test_estimate_parity.py pins the
// two implementations together.

let model = {
  ditherBpp: { none: 2.07, sierra2: 2.61, sierra2_4a: 3.0, floyd_steinberg: 2.86, bayer: 3.52 },
  bayerScaleBpp: { 0: 3.52, 1: 3.17, 2: 2.78, 3: 2.5, 4: 2.27, 5: 2.16 },
  headerBytes: 800,
  colorExponent: 1.36,
  lossyScale: 5.0,
  lossyExponent: 0.103,
  sharpenPenalty: 0.05,
};

export function useModel(served) {
  if (served) model = served;
}

export function frameCount(duration, fps, speed = 1, boomerang = false) {
  const rate = speed > 0 ? speed : 1;
  let frames = Math.max(1, Math.floor((duration / rate) * fps + 0.5));
  if (boomerang) frames = Math.max(1, frames * 2 - 1);
  return frames;
}

export function bitsPerPixel({ dither, bayerScale, colors, lossy, sharpen = 0 }) {
  const base = dither === 'bayer'
    ? (model.bayerScaleBpp[bayerScale] ?? model.bayerScaleBpp[3])
    : (model.ditherBpp[dither] ?? model.ditherBpp.bayer);

  const colorFactor = (Math.log2(Math.max(2, colors)) / 8) ** model.colorExponent;
  const lossyFactor = (1 + Math.max(0, lossy) / model.lossyScale) ** -model.lossyExponent;

  let bpp = base * colorFactor * lossyFactor;
  if (sharpen > 0) bpp *= 1 + model.sharpenPenalty * sharpen;
  return bpp;
}

export function estimateBytes(width, height, frames, bpp, calibration) {
  if (width <= 0 || height <= 0 || frames <= 0) return 0;
  const effective = bpp * (calibration > 0 ? calibration : 1);
  return Math.floor(model.headerBytes + (width * height * frames * effective) / 8);
}

export function formatBytes(bytes) {
  if (!bytes) return 'Not ready';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}
