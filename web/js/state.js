// One mutable state object plus change notification. Everything the UI renders
// is derived from here, so there is a single place to look when behaviour is
// surprising.

export const state = {
  video: null,          // VideoMeta once loaded
  start: 0,
  duration: 3,
  fps: 15,
  // What the user asked for, kept separately from what is achievable. A crop
  // smaller than the requested output caps outWidth, but must not erase the
  // request — otherwise growing the crop again would never restore it.
  desiredWidth: 480,
  desiredHeight: 270,
  outWidth: 480,
  outHeight: 270,
  crop: null,           // normalised {x, y, w, h} in 0..1, or null for full frame
  cropAspect: null,     // number to constrain crop drags to, or null for free
  linkAspect: true,     // output follows the crop's aspect ratio
  preset: 'balanced',
  dedupe: false,
  speed: 1,
  boomerang: false,
  sharpen: 0,
  loopForever: true,
  targetMB: 2,
  fitNotes: null,
  overrides: {},        // {colors, dither, bayerScale, lossy} once touched
  presets: {},          // served by /api/health
  calibration: null,    // measured bpp ratio for the loaded video
  busy: false,
  result: null,
  renderedSpec: null,    // JSON snapshot used to flag a preview after settings change
  loop: true,
};

const subscribers = new Set();

export function setState(patch) {
  Object.assign(state, patch);
  for (const fn of subscribers) fn(state);
}

export function subscribe(fn) {
  subscribers.add(fn);
  return () => subscribers.delete(fn);
}

/** Encoder settings actually in force: the preset with any override applied. */
export function settings() {
  const base = state.presets[state.preset] || { colors: 200, dither: 'bayer', bayer_scale: 3, lossy: 40 };
  const { colors, dither, bayerScale, lossy } = state.overrides;
  return {
    colors: colors ?? base.colors,
    dither: dither ?? base.dither,
    bayerScale: bayerScale ?? base.bayer_scale,
    lossy: lossy ?? base.lossy,
  };
}

/** Crop in source-video pixels, or null for the whole frame. */
export function cropPixels() {
  if (!state.crop || !state.video) return null;
  const { width, height } = state.video;
  const { x, y, w, h } = state.crop;
  return {
    x: Math.round(x * width),
    y: Math.round(y * height),
    width: Math.max(2, Math.round(w * width)),
    height: Math.max(2, Math.round(h * height)),
  };
}

/** Width divided by height of whatever region will be encoded. */
export function sourceAspect() {
  if (!state.video) return 16 / 9;
  const crop = cropPixels();
  if (crop) return crop.width / crop.height;
  return state.video.width / state.video.height;
}

/** The RenderSpec the backend expects. */
export function renderSpec() {
  const s = settings();
  return {
    video_id: state.video?.id,
    start: Number(state.start.toFixed(3)),
    duration: Number(state.duration.toFixed(3)),
    fps: state.fps,
    width: state.outWidth,
    height: state.outHeight,
    crop: cropPixels(),
    preset: state.preset,
    dedupe: state.dedupe,
    speed: Number(state.speed.toFixed(2)),
    boomerang: state.boomerang,
    sharpen: Number(state.sharpen.toFixed(2)),
    loop_forever: state.loopForever,
    colors: s.colors,
    dither: s.dither,
    bayer_scale: s.bayerScale,
    lossy: s.lossy,
  };
}
