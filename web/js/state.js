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
  autoSize: false,      // keep output at the source/crop region's native resolution
  crop: null,           // normalised {x, y, w, h} in 0..1, or null for full frame
  cropAspect: null,     // number to constrain crop drags to, or null for free
  linkAspect: true,     // output follows the crop's aspect ratio
  preset: 'balanced',
  dedupe: false,
  speed: 1,
  previewSpeed: false,  // play the source in the player at `speed`
  boomerang: false,
  sharpen: 0,
  loopForever: true,
  targetMB: 2,          // null when the user wants no size limit
  fitNotes: null,
  fitBase: null,        // the user's own settings from before the last fit
  overrides: {},        // {colors, dither, bayerScale, lossy} once touched
  presets: {},          // served by /api/health
  calibration: null,    // measured bpp ratio for the loaded video
  format: 'gif',        // what Render produces: 'gif' or 'mp4'
  mp4Bpp: null,
  mp4Bitrate: null,
  mp4Source: false,     // MP4 at the source's resolution, frame rate and near-lossless quality
  mp4Level: 'high',     // MP4 quality when not Source: 'high' | 'standard' | 'small'
  mp4SourceBase: null,  // the size and frame rate from before Source quality was switched on     // kbit/s a fit settled on for MP4; null renders by quality         // bits per pixel measured on the last MP4 render, for its estimate
  busy: false,
  jobId: null,          // the server job running now, which can be cancelled
  guiding: false,       // the crop is being steered by the pointer
  result: null,
  renderedSpec: null,    // JSON snapshot used to flag a preview after settings change
  resultLabel: null,     // what a partial render shows, e.g. 'part 2'; null for the whole GIF
  loop: true,
  zoom: null,           // {direction: 'in'|'out', amount} for an animated zoom, or null
  track: [],            // [{t, x, y}]: source seconds and crop centre in 0..1, when following
  viewZoom: 1,          // magnification of the player, for precise framing
  // A sequence: parts of the source joined in list order. Each part keeps its
  // own range, crop position, zoom and tracked path; the crop *size* is shared
  // so every part fills the GIF the same way. While a part is being edited, the
  // editor fields above are mirrored into it, and the unfinished next part is
  // parked in `draft`.
  segments: [],         // [{id, start, duration, crop, zoom, track}]
  activeSegment: null,  // id of the part being edited, or null for the draft
  draft: null,          // the editor fields as they were before a part was opened
  playingPart: null,    // index of the part "Play sequence" is on, or null
};

/** MP4 quality levels below Source, as x264 constant-quality values. */
export const MP4_LEVELS = {
  high: { crf: 18, note: 'Indistinguishable from the source for nearly all footage, at a fraction of its size.' },
  standard: { crf: 23, note: 'What sharing sites use: clean, and comfortably small.' },
  small: { crf: 28, note: 'Lightest. Soft in fine detail and fast motion, fine for chat.' },
};

/** What a part is made of; the editor holds one set of these at a time. */
export const PART_FIELDS = ['start', 'duration', 'crop', 'zoom', 'track'];

export const editorFields = () => Object.fromEntries(PART_FIELDS.map((key) => [key, state[key]]));

const subscribers = new Set();

const sameSize = (a, b) => Math.abs(a.w - b.w) < 1e-6 && Math.abs(a.h - b.h) < 1e-6;

/** The same region, resized about its own centre and kept inside the frame. */
function resized(crop, { w, h }) {
  const base = crop ?? { x: 0, y: 0, w: 1, h: 1 };
  const cx = base.x + base.w / 2;
  const cy = base.y + base.h / 2;
  return {
    x: Math.min(Math.max(cx - w / 2, 0), 1 - w),
    y: Math.min(Math.max(cy - h / 2, 0), 1 - h),
    w,
    h,
  };
}

/**
 * Give every part, and the parked draft, the editor's crop size.
 *
 * Parts share one crop size so they all fill the GIF the same way; each keeps
 * its own position, resized about its centre. It runs once a crop gesture is
 * finished rather than on every change, so the intermediate sizes of a drag —
 * or a click that clears the crop — never reach the other parts. Clearing the
 * crop is not spread at all, so a stray click cannot wipe every part's framing.
 */
export function shareCropSize() {
  if (!state.crop || !state.segments.length) return;
  const size = { w: state.crop.w, h: state.crop.h };
  const segments = state.segments.map((part) =>
    (part.id === state.activeSegment || (part.crop && sameSize(part.crop, size))
      ? part
      : { ...part, crop: resized(part.crop, size) }));
  const draft = state.draft?.crop && !sameSize(state.draft.crop, size)
    ? { ...state.draft, crop: resized(state.draft.crop, size) }
    : state.draft;
  if (segments.some((part, i) => part !== state.segments[i]) || draft !== state.draft) {
    setState({ segments, draft });
  }
}

/** While a part is open, whatever the editor changes is that part's change. */
function syncParts() {
  if (state.activeSegment === null) return;
  const index = state.segments.findIndex((part) => part.id === state.activeSegment);
  if (index < 0) {
    state.activeSegment = null;
    return;
  }
  const part = state.segments[index];
  if (PART_FIELDS.every((key) => part[key] === state[key])) return;
  state.segments = state.segments.map((candidate, at) =>
    (at === index ? { ...candidate, ...editorFields() } : candidate));
}

export function setState(patch) {
  Object.assign(state, patch);
  syncParts();
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

/** A normalised crop in source-video pixels, or null for the whole frame. */
export function cropPixels(crop = state.crop) {
  if (!crop || !state.video) return null;
  const { width, height } = state.video;
  const { x, y, w, h } = crop;
  return {
    x: Math.round(x * width),
    y: Math.round(y * height),
    width: Math.max(2, Math.round(w * width)),
    height: Math.max(2, Math.round(h * height)),
  };
}

/** Where a followed crop's centre is at `time`, by straight lines between points. */
export function trackCentre(track, time) {
  if (!track?.length) return null;
  if (time <= track[0].t) return track[0];
  const last = track[track.length - 1];
  if (time >= last.t) return last;
  let i = 1;
  while (track[i].t < time) i += 1;
  const a = track[i - 1];
  const b = track[i];
  const k = (time - a.t) / (b.t - a.t || 1);
  return { t: time, x: a.x + (b.x - a.x) * k, y: a.y + (b.y - a.y) * k };
}

/** The crop as it sits at `time`: moved along the tracked path when following. */
export function cropAt(time, crop = state.crop, track = state.track) {
  const centre = crop && track?.length >= 2 ? trackCentre(track, time) : null;
  if (!centre) return crop;
  return {
    ...crop,
    x: Math.min(Math.max(centre.x - crop.w / 2, 0), 1 - crop.w),
    y: Math.min(Math.max(centre.y - crop.h / 2, 0), 1 - crop.h),
  };
}

/** Width divided by height of whatever region will be encoded. */
export function sourceAspect() {
  if (!state.video) return 16 / 9;
  const crop = cropPixels();
  if (crop) return crop.width / crop.height;
  return state.video.width / state.video.height;
}

/** Seconds of source the GIF is made from: the sequence, or the one selection. */
export function clipSeconds() {
  if (!state.segments.length) return state.duration;
  return state.segments.reduce((sum, part) => sum + part.duration, 0);
}

const round3 = (value) => Number(value.toFixed(3));

/** One clip as the backend's Segment: range, crop in pixels, zoom, path. */
function clipSpec({ start, duration, crop, zoom, track }) {
  return {
    start: round3(start),
    duration: round3(duration),
    crop: cropPixels(crop),
    zoom: zoom ? { direction: zoom.direction, amount: Number(zoom.amount.toFixed(2)) } : null,
    // A path only means something with a crop to move.
    track: crop && track?.length >= 2 ? track : [],
  };
}

/** The RenderSpec the backend expects: the whole sequence, or the selection. */
export function renderSpec(parts = state.segments) {
  const s = settings();
  const clips = parts.map(clipSpec);
  const single = clips.length ? clips[0] : clipSpec(editorFields());
  return {
    video_id: state.video?.id,
    format: state.format,
    bitrate_kbps: state.format === 'mp4' ? state.mp4Bitrate : null,
    mp4_quality: state.format === 'mp4' && state.mp4Source ? 'source' : 'ladder',
    mp4_crf: state.format === 'mp4' && !state.mp4Source ? MP4_LEVELS[state.mp4Level].crf : null,
    start: single.start,
    duration: round3(clips.length ? clips.reduce((sum, clip) => sum + clip.duration, 0) : single.duration),
    segments: clips,
    crop: single.crop,
    zoom: single.zoom,
    track: single.track,
    fps: state.fps,
    width: state.outWidth,
    height: state.outHeight,
    preset: state.preset,
    dedupe: state.format === 'gif' && state.dedupe,
    speed: Number(state.speed.toFixed(2)),
    boomerang: state.boomerang,
    sharpen: state.format === 'gif' ? Number(state.sharpen.toFixed(2)) : 0,
    loop_forever: state.loopForever,
    colors: s.colors,
    dither: s.dither,
    bayer_scale: s.bayerScale,
    lossy: s.lossy,
  };
}
