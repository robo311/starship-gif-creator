// Wiring: DOM events in, state changes out, then one render pass over the DOM.

import {
  awaitJob, cancelJob, estimateSize, health, startFit, startLoad, startRender, startTrack,
} from './api.js';
import { initCrop } from './crop.js';
import { bitsPerPixel, estimateBytes, formatBytes, frameCount, useModel } from './estimate.js';
import {
  MP4_LEVELS, clipSeconds, cropAt, cropPixels, editorFields, renderSpec, setState, settings, shareCropSize,
  sourceAspect, state, subscribe,
} from './state.js';
import { initTimeline } from './timeline.js';

const el = (id) => document.getElementById(id);

const dom = {
  form: el('load-form'), url: el('url'), pasteUrl: el('paste-url'),
  qualityPicks: el('quality-picks'), load: el('load'),
  banner: el('banner'), placeholder: el('placeholder'),
  stepLink: el('step-link'), stepEdit: el('step-edit'), stepExport: el('step-export'),
  studioTitle: el('studio-title'), themeToggle: el('theme-toggle'),
  themeColor: el('theme-color'),
  viewport: el('viewport'), video: el('video'), overlay: el('overlay'),
  stageScroll: el('stage-scroll'), stage: el('stage'), viewZoom: el('view-zoom'),
  viewZoomIn: el('view-zoom-in'), viewZoomOut: el('view-zoom-out'), viewZoomReset: el('view-zoom-reset'),
  veil: el('veil'), veilText: el('veil-text'), veilFill: el('veil-fill'), cancelJob: el('cancel-job'),
  play: el('play'), now: el('now'), total: el('total'), volume: el('volume'), loop: el('loop'),
  stepBack: el('step-back'), stepFwd: el('step-fwd'),
  markIn: el('mark-in'), markOut: el('mark-out'),
  zoomFit: el('zoom-fit'), viewLabel: el('view-label'), renderSelection: el('render-selection'),
  track: el('track'), sel: el('sel'), hIn: el('h-in'), hOut: el('h-out'),
  playhead: el('playhead'), scrub: el('scrub'), partMarks: el('part-marks'),
  rIn: el('r-in'), rOut: el('r-out'), rLen: el('r-len'), rFrames: el('r-frames'),
  rPlays: el('r-plays'),
  cropX: el('crop-x'), cropY: el('crop-y'), cropW: el('crop-w'), cropH: el('crop-h'),
  cropReset: el('crop-reset'), cropAspects: el('crop-aspects'), cropHint: el('crop-hint'),
  follow: el('follow'), guide: el('guide'), unfollow: el('unfollow'), followHint: el('follow-hint'),
  cropSummary: el('crop-summary'), sizeSummary: el('size-summary'), motionSummary: el('motion-summary'),
  outW: el('out-w'), outH: el('out-h'), linkAspect: el('link-aspect'), aspectNote: el('aspect-note'),
  sizePresets: el('size-presets'), scaleHint: el('scale-hint'),
  fps: el('fps'), fpsOut: el('fps-out'), fpsHint: el('fps-hint'),
  speed: el('speed'), speedOut: el('speed-out'), previewSpeed: el('preview-speed'),
  zoomModes: el('zoom-modes'), zoomAmount: el('zoom-amount'), zoomAmountOut: el('zoom-amount-out'),
  zoomAmountRow: el('zoom-amount-row'), zoomScope: el('zoom-scope'),
  boomerang: el('boomerang'), loopForever: el('loop-forever'),
  sharpen: el('sharpen'), sharpenOut: el('sharpen-out'),
  targetMB: el('target-mb'), fitBtn: el('fit'), fitNote: el('fit-note'), fitRestore: el('fit-restore'),
  presets: el('presets'), presetNote: el('preset-note'), qualitySummary: el('quality-summary'),
  dedupe: el('dedupe'), dedupeHint: el('dedupe-hint'),
  colors: el('colors'), colorsOut: el('colors-out'),
  lossy: el('lossy'), lossyOut: el('lossy-out'),
  dither: el('dither'), bayer: el('bayer'), bayerOut: el('bayer-out'),
  advancedReset: el('advanced-reset'),
  estSize: el('est-size'), estNote: el('est-note'), weightFill: el('weight-fill'),
  actualBlock: el('actual-block'), actSize: el('act-size'), actNote: el('act-note'),
  actualFill: el('actual-fill'),
  resultTitle: el('export-title'),
  renderBtn: el('render'), renderLabel: document.querySelector('#render .button-label'),
  download: el('download'), copyGif: el('copy-gif'),
  preview: el('preview'), gif: el('gif'), mp4: el('mp4'), gifMeta: el('gif-meta'),
  formatPicks: el('format-picks'), gifGroup: el('gif-group'), mp4Group: el('mp4-group'),
  mp4Levels: el('mp4-levels'), mp4LevelNote: el('mp4-level-note'), mp4Summary: el('mp4-summary'), mp4Source: el('mp4-source'), sourceQualityRow: el('source-quality-row'),
  panelStatus: el('panel-status'), videoMeta: el('video-meta'),
  metaProvider: el('meta-provider'), metaTitle: el('meta-title'), metaDims: el('meta-dims'),
  downloadVideo: el('download-video'), downloadVideoNote: el('download-video-note'),
  sourceCue: el('source-cue'),
  addPart: el('add-part'), donePart: el('done-part'),
  renderPart: el('render-part'), downloadPart: el('download-part'),
  playSequence: el('play-sequence'), clearParts: el('clear-parts'),
  parts: el('parts'), sequenceSummary: el('sequence-summary'), sequenceHint: el('sequence-hint'),
};

// ── small helpers ────────────────────────────────────────────────────────

const evenize = (value) => Math.max(16, Math.round(value / 2) * 2);

/** The requested download quality, from the segmented control. */
const sourceHeight = () => Number(dom.qualityPicks.querySelector('input:checked')?.value || 1080);

/** Write a formatted size as a big number with a small unit beside it.
 *  Anything that is not "<number> <unit>" is left as plain text. */
function writeSize(node, text) {
  const parts = String(text).split(' ');
  const measured = parts.length === 2 && Number.isFinite(Number(parts[0]));
  node.textContent = measured ? parts[0] : text;
  node.classList.toggle('plain', !measured);
  if (!measured) return;
  const unit = document.createElement('i');
  unit.textContent = parts[1];
  node.append(unit);
}

/** Fill a weight meter, measuring the size against the user's own limit. */
const NO_LIMIT_SCALE_MB = 10;

function setMeter(node, ratio, canOverflow = true) {
  node.style.setProperty('--fill', `${Math.max(4, Math.min(100, ratio * 100))}%`);
  node.classList.toggle('over', canOverflow && ratio > 1);
}

/** Paint the travelled part of every slider: the browser will not do it alone. */
function syncRanges() {
  for (const range of document.querySelectorAll('input[type=range]')) {
    const min = Number(range.min || 0);
    const max = Number(range.max || 100);
    const span = max - min;
    range.style.setProperty('--pct', `${span > 0 ? ((Number(range.value) - min) / span) * 100 : 0}%`);
  }
}

function setTheme(theme, remember = true) {
  const light = theme === 'light';
  document.documentElement.dataset.theme = light ? 'light' : 'dark';
  dom.themeToggle.setAttribute('aria-pressed', String(light));
  dom.themeToggle.setAttribute('aria-label', light ? 'Switch to dark mode' : 'Switch to light mode');
  dom.themeToggle.title = light ? 'Switch to dark mode' : 'Switch to light mode';
  dom.themeColor.content = light ? '#f1efec' : '#0c0c0d';
  if (remember) {
    try {
      localStorage.setItem('starship-theme', light ? 'light' : 'dark');
    } catch {}
  }
}

setTheme(document.documentElement.dataset.theme, false);
dom.themeToggle.addEventListener('click', () => {
  setTheme(document.documentElement.dataset.theme === 'light' ? 'dark' : 'light');
});

function formatTime(seconds) {
  if (!isFinite(seconds)) return '0:00.00';
  const minutes = Math.floor(seconds / 60);
  const rest = seconds - minutes * 60;
  return `${minutes}:${rest.toFixed(2).padStart(5, '0')}`;
}

function notify(message, kind = 'error') {
  dom.banner.textContent = message;
  dom.banner.className = `banner${kind === 'info' ? ' info' : ''}`;
  dom.banner.hidden = !message;
}

function publicError(error) {
  const message = String(error?.message || error);
  if (message.toLowerCase().includes('[instagram]')
      && message.toLowerCase().includes('no video formats found')) {
    return 'That Instagram post does not contain a downloadable video. '
      + 'Paste a Reel or a post that includes a video.';
  }
  return message;
}

function veil(percent, message) {
  dom.veil.hidden = false;
  dom.veilFill.style.width = `${Math.max(2, percent)}%`;
  dom.veilText.textContent = message || 'Working…';
}

const hideVeil = () => { dom.veil.hidden = true; dom.veilFill.style.width = '0%'; };

// ── cancelling ───────────────────────────────────────────────────────────

/** Remember which server job is running, so it can be cancelled. */
async function started(request) {
  const { job_id: jobId } = await request;
  dom.cancelJob.disabled = false;
  setState({ jobId });
  return jobId;
}

async function cancelRunningJob() {
  const jobId = state.jobId;
  if (!jobId) return;
  dom.veilText.textContent = 'Cancelling…';
  dom.cancelJob.disabled = true;
  // The poll in awaitJob sees the cancelled state and unwinds the caller.
  await cancelJob(jobId).catch(() => {});
}

dom.cancelJob.addEventListener('click', cancelRunningJob);

/** Report a failed job, or quietly note one the user cancelled. */
function jobFailed(error, what = 'Rendering') {
  if (error?.cancelled) notify(`${what} cancelled.`, 'info');
  else notify(publicError(error));
}

/** Size of the region that will actually be encoded, in source pixels. */
function region() {
  const crop = cropPixels();
  if (crop) return { width: crop.width, height: crop.height };
  if (state.video) return { width: state.video.width, height: state.video.height };
  return { width: 1920, height: 1080 };
}

const videoDuration = () => state.video?.duration || dom.video.duration || 0;
const clipEnd = () => state.start + state.duration;
const sourceFps = () => Math.max(1, Math.round(state.video?.fps || 25));

/** How long the finished GIF will actually play for.
 *  Derived from the frame count, so it matches the boomerang's dropped frame. */
const playbackSeconds = () =>
  frameCount(clipSeconds(), state.fps, state.speed, state.boomerang) / state.fps;

// ── the selection, and the sequence part it may be editing ───────────────

const MAX_SECONDS = 60;   // matches RenderSpec's limit on the whole GIF
const MAX_PARTS = 20;

const partsSeconds = (except = null) =>
  state.segments.reduce((sum, part) => (part.id === except ? sum : sum + part.duration), 0);

/** Move the selection. While a part is being edited, the part moves with it. */
function setClip(start, duration) {
  if (state.activeSegment === null) {
    setState({ start, duration });
    return;
  }
  // The whole sequence must still fit the limit, so the other parts cap this one.
  const room = Math.max(0.1, MAX_SECONDS - partsSeconds(state.activeSegment));
  setState({ start, duration: Math.min(duration, room) });
}

// ── size handling ────────────────────────────────────────────────────────

/**
 * Recompute the output size from the user's request and what the crop allows.
 *
 * The requested size is remembered even when the current crop cannot supply it,
 * so enlarging the crop restores the size the user actually asked for instead of
 * leaving it stuck at whatever the smallest intermediate crop permitted.
 */
function applySize({ width, height } = {}) {
  const { width: maxW, height: maxH } = region();
  const desiredWidth = width ?? (state.autoSize ? maxW : state.desiredWidth);
  const desiredHeight = height ?? (state.autoSize ? maxH : state.desiredHeight);

  const outWidth = evenize(Math.min(desiredWidth, maxW));
  const outHeight = state.linkAspect
    ? evenize(outWidth / sourceAspect())
    : evenize(Math.min(desiredHeight, maxH));

  setState({ desiredWidth, desiredHeight, outWidth, outHeight });
}

// ── crop overlay ─────────────────────────────────────────────────────────

const clamp01 = (value) => Math.min(1, Math.max(0, value));

/** The crop to draw: the playing part's while a sequence plays, else the editor's,
 *  in both cases where a followed target has taken it at this moment. */
function shownCrop() {
  if (guide && state.crop) {
    const { w, h } = state.crop;
    return {
      ...state.crop,
      x: Math.min(Math.max(guide.centre.x - w / 2, 0), 1 - w),
      y: Math.min(Math.max(guide.centre.y - h / 2, 0), 1 - h),
    };
  }
  const time = playheadTime();
  const playing = state.playingPart !== null ? state.segments[state.playingPart] : null;
  return playing ? cropAt(time, playing.crop, playing.track) : cropAt(time);
}

/**
 * Change the crop from the overlay or the fields.
 *
 * While following a target, moving the crop shifts the whole path by the same
 * amount — a way to reframe without tracking again — and resizing keeps the
 * path. Drawing a fresh crop means a new subject, so it drops the path.
 */
function applyCrop(next, mode) {
  if (!next || mode === 'new') {
    setState({ crop: next, track: [] });
  } else if (state.crop && state.track.length >= 2) {
    const shown = cropAt(playheadTime());
    const moved = Math.abs(next.w - shown.w) < 1e-6 && Math.abs(next.h - shown.h) < 1e-6;
    const dx = moved ? next.x - shown.x : 0;
    const dy = moved ? next.y - shown.y : 0;
    setState({
      crop: next,
      track: moved
        ? state.track.map((point) => ({ ...point, x: clamp01(point.x + dx), y: clamp01(point.y + dy) }))
        : state.track,
    });
  } else {
    setState({ crop: next });
  }
  if (state.linkAspect || state.autoSize) applySize({});
}

const crop = initCrop({
  canvas: dom.overlay,
  video: dom.video,
  getCrop: shownCrop,
  setCrop: applyCrop,
  // Only a finished gesture resizes the other parts, never every step of a drag.
  onCommit: shareCropSize,
  getLockRatio: () => state.cropAspect !== null,
  getTargetAspect: () => state.cropAspect,
});

// ── timeline ─────────────────────────────────────────────────────────────

const timeline = initTimeline({
  track: dom.track, sel: dom.sel, handleIn: dom.hIn, handleOut: dom.hOut,
  playhead: dom.playhead, scrub: dom.scrub, viewLabel: dom.viewLabel, marks: dom.partMarks,
  getDuration: videoDuration,
  getMarks: () => state.segments.map((part, index) => ({
    start: part.start,
    end: part.start + part.duration,
    label: String(index + 1),
    active: part.id === state.activeSegment,
  })),
  getRange: () => ({ start: state.start, end: clipEnd() }),
  setRange: (start, end, edge) => {
    // Seek first, so the repaint below already draws the playhead in place.
    previewSeek(edge === 'end' ? Math.max(0, end - 0.05) : start);
    setClip(start, Math.max(0.1, end - start));
  },
  seek: (time) => {
    previewSeek(time);
    timeline.render(playheadTime());
  },
});

// Dragging asks for a new frame on every pointer move, far faster than the
// decoder can deliver. Piling seeks up makes the preview stutter and lag, so
// only one runs at a time and anything asked for meanwhile collapses into the
// latest request, applied as soon as the current seek lands.
let queuedSeek = null;

function previewSeek(time) {
  if (dom.video.seeking) queuedSeek = time;
  else dom.video.currentTime = time;
}

/** Where the playhead should be drawn: the newest requested position. */
const playheadTime = () => queuedSeek ?? dom.video.currentTime;

dom.video.addEventListener('seeked', () => {
  if (queuedSeek === null) return;
  const time = queuedSeek;
  queuedSeek = null;
  dom.video.currentTime = time;
});

// ── load ─────────────────────────────────────────────────────────────────

dom.pasteUrl.addEventListener('click', async () => {
  try {
    const text = (await navigator.clipboard.readText()).trim();
    if (!text) {
      notify('Your clipboard is empty.', 'info');
      return;
    }
    dom.url.value = text;
    dom.url.focus();
    notify('');
  } catch {
    notify('Clipboard access is unavailable here. Paste the link into the field directly.', 'info');
    dom.url.focus();
  }
});

dom.form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const url = dom.url.value.trim();
  if (!url) return;

  notify('');
  setState({ busy: true, result: null, renderedSpec: null, calibration: null });
  veil(2, 'Reading source');

  try {
    const meta = await awaitJob(await started(startLoad(url, sourceHeight())), veil);

    dom.video.src = meta.stream_url;
    dom.video.load();

    // Shape the viewport to the source, so the frame fills it exactly. Bars
    // around a letterboxed frame are dead area a crop drag can wander into.
    if (meta.width && meta.height) {
      dom.viewport.style.setProperty('--source-aspect', `${meta.width} / ${meta.height}`);
    }

    setState({
      video: meta,
      crop: null,
      start: 0,
      duration: Math.min(3, Math.max(0.5, meta.duration || 3)),
      // Asking for more frames than the source has only duplicates them; Source
      // quality asks for exactly the source's rate, as far as MP4 allows.
      fps: state.format === 'mp4' && state.mp4Source
        ? Math.min(MP4_MAX_FPS, Math.max(1, Math.round(meta.fps || 25)))
        : Math.min(state.fps, GIF_MAX_FPS, Math.max(1, Math.round(meta.fps || 25))),
      result: null,
      fitNotes: null,
      fitBase: null,
      segments: [],
      activeSegment: null,
      draft: null,
      playingPart: null,
      zoom: null,
      track: [],
      resultLabel: null,
    });
    setViewZoom(1);
    applySize(state.autoSize ? {} : { width: Math.min(480, meta.width) });

    // The server may already have measured this clip in an earlier session, so
    // adopt that calibration rather than opening with an uninformed guess.
    const known = await estimateSize(renderSpec()).catch(() => null);
    if (known?.calibration) setState({ calibration: known.calibration });

    notify('');
  } catch (error) {
    jobFailed(error, 'Loading');
  } finally {
    setState({ busy: false, jobId: null });
    hideVeil();
  }
});

// ── playback ─────────────────────────────────────────────────────────────

dom.play.addEventListener('click', () => {
  if (dom.video.paused) {
    const time = dom.video.currentTime;
    if (state.loop && (time < state.start || time > clipEnd())) dom.video.currentTime = state.start;
    dom.video.play().catch(() => {});
  } else {
    dom.video.pause();
  }
});

dom.video.addEventListener('timeupdate', () => {
  // Only the upper bound is enforced, so a deliberate seek outside the
  // selection is not immediately yanked back.
  if (!guide && state.playingPart === null && state.loop && !dom.video.paused && dom.video.currentTime > clipEnd()) {
    dom.video.currentTime = state.start;
  }
  dom.now.textContent = formatTime(dom.video.currentTime);
  if (state.track.length >= 2) syncCropFields();
  timeline.render(playheadTime());
});

dom.video.addEventListener('loadedmetadata', () => {
  dom.total.textContent = formatTime(videoDuration());
  crop.draw();
  timeline.render(playheadTime());
});

dom.video.addEventListener('play', () => { dom.play.classList.add('is-playing'); });
dom.video.addEventListener('pause', () => {
  dom.play.classList.remove('is-playing');
  if (state.playingPart !== null) setState({ playingPart: null });
});
dom.video.addEventListener('error', () => {
  if (dom.video.src) notify('The browser could not play the downloaded file. Try loading it again at a lower quality.');
});

function syncVolume() {
  const percent = Math.round((dom.video.muted ? 0 : dom.video.volume) * 100);
  dom.volume.value = String(percent);
  dom.volume.setAttribute('aria-valuetext', `${percent} percent`);
  dom.volume.closest('.volume-control').classList.toggle('is-muted', percent === 0);
  syncRanges();
}

dom.volume.addEventListener('input', () => {
  dom.video.muted = false;
  dom.video.volume = Number(dom.volume.value) / 100;
});
dom.video.addEventListener('volumechange', syncVolume);

dom.loop.addEventListener('change', () => setState({ loop: dom.loop.checked }));

dom.markIn.addEventListener('click', () => {
  const end = clipEnd();
  const start = Math.max(0, end - 60, Math.min(dom.video.currentTime, end - 0.1));
  setClip(start, end - start);
});

dom.markOut.addEventListener('click', () => {
  const end = Math.max(dom.video.currentTime, state.start + 0.1);
  setClip(state.start, Math.min(60, end - state.start));
});

const stepFrame = (direction) => {
  dom.video.pause();
  const step = direction / sourceFps();
  dom.video.currentTime = Math.max(0, Math.min(videoDuration(), dom.video.currentTime + step));
};

dom.stepBack.addEventListener('click', () => stepFrame(-1));
dom.stepFwd.addEventListener('click', () => stepFrame(1));
dom.zoomFit.addEventListener('click', () => timeline.fitClip());

// Shortcuts, but never while the user is typing into a field, or working the
// rendered MP4's own controls, where Space and the arrows belong to that video.
document.addEventListener('keydown', (event) => {
  const target = event.target;
  if (target instanceof HTMLInputElement || target instanceof HTMLSelectElement) return;
  if (target === dom.mp4) return;
  if (event.metaKey || event.ctrlKey || event.altKey) return;

  const actions = {
    ' ': () => dom.play.click(),
    i: () => dom.markIn.click(),
    o: () => dom.markOut.click(),
    f: () => timeline.fitClip(),
    a: () => addPart(),
    r: () => renderSelection(),
    '+': () => setViewZoom(state.viewZoom * VIEW_ZOOM_STEP),
    '=': () => setViewZoom(state.viewZoom * VIEW_ZOOM_STEP),
    '-': () => setViewZoom(state.viewZoom / VIEW_ZOOM_STEP),
    0: () => setViewZoom(1),
    ArrowLeft: () => (event.shiftKey ? (dom.video.currentTime -= 1) : stepFrame(-1)),
    ArrowRight: () => (event.shiftKey ? (dom.video.currentTime += 1) : stepFrame(1)),
  };

  if (event.key === 'Escape' && (guide || state.jobId)) {
    event.preventDefault();
    if (guide) finishGuide(false);
    else cancelRunningJob();
    return;
  }

  const action = actions[event.key] || actions[event.key.toLowerCase()];
  if (!action || !state.video) return;
  event.preventDefault();
  action();
});

// ── crop inputs ──────────────────────────────────────────────────────────

function cropFromInputs() {
  if (!state.video) return;
  const { width, height } = state.video;
  const x = Math.max(0, Math.min(Number(dom.cropX.value) || 0, width - 2));
  const y = Math.max(0, Math.min(Number(dom.cropY.value) || 0, height - 2));
  const w = Math.max(2, Math.min(Number(dom.cropW.value) || width, width - x));
  const h = Math.max(2, Math.min(Number(dom.cropH.value) || height, height - y));
  applyCrop({ x: x / width, y: y / height, w: w / width, h: h / height });
  shareCropSize();
  crop.draw();
}

for (const input of [dom.cropX, dom.cropY, dom.cropW, dom.cropH]) {
  input.addEventListener('change', cropFromInputs);
}

dom.cropReset.addEventListener('click', () => {
  applyCrop(null);
  crop.draw();
});

dom.cropAspects.addEventListener('click', (event) => {
  // The button holds a label and a ratio; a click usually lands on one of those.
  const choice = event.target.closest('[data-aspect]')?.dataset.aspect;
  if (!choice) return;
  if (choice === 'free') {
    setState({ cropAspect: null });
    return;
  }
  const [w, h] = choice.split(':').map(Number);
  const aspect = w / h;
  setState({ cropAspect: aspect });

  // Reshape the crop immediately, so the choice is visible at once. Without a
  // crop yet, start from the whole frame: that yields the largest region of the
  // chosen shape, centred.
  if (state.video) {
    const { width: vw, height: vh } = state.video;
    const pixels = cropPixels() ?? { x: 0, y: 0, width: vw, height: vh };
    // Keep the area roughly constant while adopting the new ratio.
    const side = Math.sqrt(pixels.width * pixels.height);
    let w2 = Math.min(vw, side * Math.sqrt(aspect));
    let h2 = w2 / aspect;
    if (h2 > vh) { h2 = vh; w2 = h2 * aspect; }
    const cx = pixels.x + pixels.width / 2;
    const cy = pixels.y + pixels.height / 2;
    const x = Math.max(0, Math.min(cx - w2 / 2, vw - w2));
    const y = Math.max(0, Math.min(cy - h2 / 2, vh - h2));
    applyCrop({ x: x / vw, y: y / vh, w: w2 / vw, h: h2 / vh });
    shareCropSize();
  }
  if (state.linkAspect || state.autoSize) applySize({});
  crop.draw();
});

// ── follow a target ──────────────────────────────────────────────────────

// A followed crop moves while the video plays, so the overlay is redrawn
// every frame during playback rather than only when the crop is edited.
function overlayLoop() {
  crop.draw();
  if (!dom.video.paused) requestAnimationFrame(overlayLoop);
}
dom.video.addEventListener('play', () => requestAnimationFrame(overlayLoop));
dom.video.addEventListener('seeked', () => {
  crop.draw();
  if (state.track.length >= 2) syncCropFields();
});

/** A followed crop moves, so its X and Y fields are refreshed as time passes. */
function syncCropFields() {
  const pixels = cropPixels(cropAt(playheadTime()));
  if (!pixels) return;
  for (const [input, value] of [[dom.cropX, pixels.x], [dom.cropY, pixels.y]]) {
    if (document.activeElement !== input) input.value = value;
  }
}

dom.follow.addEventListener('click', async () => {
  if (!state.video || !state.crop || state.busy) return;
  const anchor = Math.min(Math.max(playheadTime(), state.start), clipEnd());
  // Track from the crop as it is shown now: the user framed the target here.
  const framed = cropPixels(cropAt(anchor));
  const range = { start: state.start, duration: state.duration };
  dom.video.pause();
  notify('');
  setState({ busy: true });
  veil(2, 'Following the target');
  try {
    const jobId = await started(startTrack({ video_id: state.video.id, ...range, crop: framed, anchor }));
    const { points } = await awaitJob(jobId, veil);
    if (!points || points.length < 2) throw new Error('The target could not be followed in this clip.');
    setState({ track: points });
    previewSeek(state.start);
    notify('Following the target. Play the clip to check it, drag the crop to nudge the whole path.', 'info');
  } catch (error) {
    jobFailed(error, 'Following');
  } finally {
    setState({ busy: false, jobId: null });
    hideVeil();
    crop.draw();
  }
});

dom.unfollow.addEventListener('click', () => {
  // Keep the crop where it is on screen right now, just stop it moving.
  setState({ crop: cropAt(playheadTime()), track: [] });
  crop.draw();
});

// ── guide the crop with the pointer ──────────────────────────────────────

// The manual way to follow something: the clip plays at half speed while the
// crop sits wherever the pointer is, and the recorded path is smoothed so the
// hand's wobble does not become camera shake. No tracker, nothing to lose.
//
// Starting is two steps. The button only arms it: the clip waits paused at its
// start with the crop already on the pointer, so the user can get into
// position. Recording begins when they press play.

const GUIDE_RATE = 0.5;
const GUIDE_SMOOTH_SECONDS = 0.4;
const GUIDE_POINTS_PER_SECOND = 5;

let guide = null;   // {centre, points, recording} while guiding

function startGuide() {
  // The same button backs out of an armed guide that has not started yet.
  if (guide) {
    finishGuide(false);
    return;
  }
  if (!state.video || !state.crop || state.busy) return;
  const shown = cropAt(playheadTime());
  guide = {
    centre: { x: shown.x + shown.w / 2, y: shown.y + shown.h / 2 },
    points: [],
    recording: false,
  };
  dom.video.pause();
  dom.viewport.classList.add('is-guiding');
  dom.overlay.style.pointerEvents = 'none';
  setState({ guiding: true });
  dom.video.currentTime = state.start;
  dom.video.playbackRate = GUIDE_RATE;
  notify('Ready to guide. Put the pointer on the target, then press play (or Space) to start recording. '
    + 'The clip plays at half speed. Esc cancels.', 'info');
  crop.draw();
}

// Pressing play is what starts the recording.
dom.video.addEventListener('play', () => {
  if (!guide || guide.recording) return;
  guide.recording = true;
  dom.video.playbackRate = GUIDE_RATE;
  notify('Recording. Keep the pointer on the target until the clip ends; pause to stop early.', 'info');
  requestAnimationFrame(guideTick);
});

dom.stageScroll.addEventListener('pointermove', (event) => {
  if (!guide) return;
  const bounds = dom.overlay.getBoundingClientRect();
  guide.centre = {
    x: clamp01((event.clientX - bounds.left) / bounds.width),
    y: clamp01((event.clientY - bounds.top) / bounds.height),
  };
  // While armed nothing is ticking, so the crop is moved to the pointer here.
  if (!guide.recording) crop.draw();
});

function guideTick() {
  if (!guide) return;
  const time = dom.video.currentTime;
  const last = guide.points[guide.points.length - 1];
  // The display refreshes faster than video frames advance; keep one per moment.
  if (!last || time > last.t) guide.points.push({ t: time, ...guide.centre });
  crop.draw();
  if (time >= clipEnd() || dom.video.ended) {
    finishGuide(true);
    return;
  }
  // A pause by the user ends the pass but keeps what was steered so far.
  if (dom.video.paused) {
    finishGuide(true);
    return;
  }
  requestAnimationFrame(guideTick);
}

/** Centred moving average that narrows at the ends, so it never lags the hand. */
function smoothPath(values, window) {
  const half = Math.floor(window / 2);
  return values.map((_, i) => {
    const reach = Math.min(half, i, values.length - 1 - i);
    let sum = 0;
    for (let j = i - reach; j <= i + reach; j += 1) sum += values[j];
    return sum / (2 * reach + 1);
  });
}

function finishGuide(keep) {
  if (!guide) return;
  const { points } = guide;
  guide = null;
  dom.video.pause();
  dom.viewport.classList.remove('is-guiding');
  dom.overlay.style.pointerEvents = '';
  // Paint restores the rate the speed toggle asks for.
  dom.video.playbackRate = 1;

  if (!keep || points.length < 2) {
    setState({ guiding: false });
    notify(keep ? 'Nothing was recorded. Move the pointer over the video while it plays.' : 'Guiding cancelled.', 'info');
    crop.draw();
    return;
  }
  const span = points[points.length - 1].t - points[0].t || 1;
  const window = Math.max(1, Math.round((points.length / span) * GUIDE_SMOOTH_SECONDS));
  const xs = smoothPath(points.map((point) => point.x), window);
  const ys = smoothPath(points.map((point) => point.y), window);
  const track = [];
  points.forEach((point, i) => {
    const previous = track[track.length - 1];
    const isLast = i === points.length - 1;
    if (!previous || isLast || point.t - previous.t >= 1 / GUIDE_POINTS_PER_SECOND) {
      track.push({ t: Number(point.t.toFixed(3)), x: Number(xs[i].toFixed(5)), y: Number(ys[i].toFixed(5)) });
    }
  });
  setState({ guiding: false, track });
  previewSeek(state.start);
  notify('The crop now follows the path you steered. Play the clip to check it, or guide again.', 'info');
  crop.draw();
}

dom.guide.addEventListener('click', startGuide);

// ── zoom effect ──────────────────────────────────────────────────────────

const DEFAULT_ZOOM_AMOUNT = 1.3;

dom.zoomModes.addEventListener('click', (event) => {
  const mode = event.target.closest('button[data-zoom]')?.dataset.zoom;
  if (!mode) return;
  setState({
    zoom: mode === 'none'
      ? null
      : { direction: mode, amount: state.zoom?.amount ?? DEFAULT_ZOOM_AMOUNT },
  });
});

dom.zoomAmount.addEventListener('input', () => {
  if (!state.zoom) return;
  setState({ zoom: { ...state.zoom, amount: Number(dom.zoomAmount.value) } });
});

// ── magnifying the player ────────────────────────────────────────────────

const VIEW_ZOOM_MAX = 4;
const VIEW_ZOOM_STEP = 1.5;

/** Magnify the player, keeping the point under `anchor` (viewport px) in place. */
function setViewZoom(level, anchor) {
  const next = Math.min(VIEW_ZOOM_MAX, Math.max(1, level));
  const before = state.viewZoom;
  const scroller = dom.stageScroll;
  const ax = anchor?.x ?? scroller.clientWidth / 2;
  const ay = anchor?.y ?? scroller.clientHeight / 2;
  // The content point under the anchor, as a share of the whole stage.
  const fx = (scroller.scrollLeft + ax) / (scroller.clientWidth * before);
  const fy = (scroller.scrollTop + ay) / (scroller.clientHeight * before);
  setState({ viewZoom: next });
  dom.stage.style.setProperty('--view-zoom', String(next));
  scroller.classList.toggle('is-zoomed', next > 1);
  scroller.scrollLeft = fx * scroller.clientWidth * next - ax;
  scroller.scrollTop = fy * scroller.clientHeight * next - ay;
  crop.draw();
}

dom.viewZoomIn.addEventListener('click', () => setViewZoom(state.viewZoom * VIEW_ZOOM_STEP));
dom.viewZoomOut.addEventListener('click', () => setViewZoom(state.viewZoom / VIEW_ZOOM_STEP));
dom.viewZoomReset.addEventListener('click', () => setViewZoom(1));

// Ctrl- or cmd-scroll zooms about the pointer; a plain scroll pans when zoomed.
dom.stageScroll.addEventListener('wheel', (event) => {
  if (!state.video || !(event.ctrlKey || event.metaKey)) return;
  event.preventDefault();
  const bounds = dom.stageScroll.getBoundingClientRect();
  setViewZoom(state.viewZoom * Math.exp(-event.deltaY * 0.0025), {
    x: event.clientX - bounds.left,
    y: event.clientY - bounds.top,
  });
}, { passive: false });

// ── output size inputs ───────────────────────────────────────────────────

dom.outW.addEventListener('change', () => {
  setState({ autoSize: false });
  applySize({ width: Number(dom.outW.value) });
});
dom.outH.addEventListener('change', () => {
  setState({ autoSize: false });
  applySize({ height: Number(dom.outH.value) });
});

dom.linkAspect.addEventListener('change', () => {
  setState({ linkAspect: dom.linkAspect.checked });
  applySize({});
});

dom.sizePresets.addEventListener('click', (event) => {
  const button = event.target.closest('button');
  if (!button) return;
  if (button.dataset.size === 'auto') {
    if (!state.video) return;
    setState({ autoSize: true });
    applySize({});
    return;
  }
  const width = button.dataset.w;
  if (width) {
    setState({ autoSize: false });
    applySize({ width: Number(width) });
  }
});

// ── frame rate, quality ──────────────────────────────────────────────────

dom.fps.addEventListener('input', () => setState({ fps: Number(dom.fps.value) }));
dom.speed.addEventListener('input', () => setState({ speed: Number(dom.speed.value) }));
dom.previewSpeed.addEventListener('change', () => setState({ previewSpeed: dom.previewSpeed.checked }));
dom.sharpen.addEventListener('input', () => setState({ sharpen: Number(dom.sharpen.value) }));
dom.boomerang.addEventListener('change', () => setState({ boomerang: dom.boomerang.checked }));
dom.loopForever.addEventListener('change', () => setState({ loopForever: dom.loopForever.checked }));
// An empty box means "no limit": whatever a fit gave up is handed back, so the
// next render uses the user's own settings again.
dom.targetMB.addEventListener('change', () => {
  const value = Number(dom.targetMB.value);
  if (dom.targetMB.value.trim() === '' || !(value > 0)) {
    setState({ targetMB: null });
    restoreFitBase();
  } else {
    setState({ targetMB: value });
  }
});

// The quality presets are a ladder, lightest first, drawn as a slider.
const PRESET_LEVELS = ['tiny', 'small', 'balanced', 'high', 'max'];

/** Move to a preset. Choosing one discards manual tweaks, as "preset" implies. */
const pickPreset = (preset) => setState({ preset, overrides: {} });

dom.presets.addEventListener('click', (event) => {
  const preset = event.target.closest('button[data-preset]')?.dataset.preset;
  if (preset) pickPreset(preset);
});

// Drag the handle along the rail. The dots sit at the centre of their columns,
// so the rail itself spans level 0 to level 4 and the pointer maps onto it
// directly — snapping to the nearest level rather than landing between two.
const levelAt = (clientX) => {
  const rail = dom.presets.querySelector('.levels-rail').getBoundingClientRect();
  const last = PRESET_LEVELS.length - 1;
  const ratio = rail.width ? (clientX - rail.left) / rail.width : 0;
  return Math.min(last, Math.max(0, Math.round(ratio * last)));
};

/** Snap to whichever level the pointer is nearest, if that is a move at all. */
const dragTo = (clientX) => {
  const preset = PRESET_LEVELS[levelAt(clientX)];
  if (preset !== state.preset) pickPreset(preset);
};

let dragging = false;

dom.presets.addEventListener('pointerdown', (event) => {
  if (event.button !== 0) return;
  dragging = true;
  // Capture on the row, so the drag survives leaving it — a pointer that slips
  // off the panel should still be steering the handle.
  dom.presets.setPointerCapture(event.pointerId);
  dom.presets.classList.add('dragging');
  dragTo(event.clientX);
});

dom.presets.addEventListener('pointermove', (event) => {
  if (dragging) dragTo(event.clientX);
});

const endDrag = () => {
  if (!dragging) return;
  dragging = false;
  dom.presets.classList.remove('dragging');
  // Hand focus to where the drag left off, so arrow keys carry on from there.
  dom.presets.querySelector(`[data-preset="${state.preset}"]`)?.focus();
};

dom.presets.addEventListener('pointerup', endDrag);
dom.presets.addEventListener('pointercancel', endDrag);
// Insurance: if capture is torn away without a pointerup, the row must not be
// left thinking a finger is still on it. Re-entry after pointerup is a no-op.
dom.presets.addEventListener('lostpointercapture', endDrag);

// Arrow keys walk the ladder, which is what a slider-shaped control promises.
dom.presets.addEventListener('keydown', (event) => {
  const step = { ArrowLeft: -1, ArrowDown: -1, ArrowRight: 1, ArrowUp: 1 }[event.key];
  const last = PRESET_LEVELS.length - 1;
  const at = PRESET_LEVELS.indexOf(state.preset);
  let next = null;
  if (step) next = Math.min(last, Math.max(0, at + step));
  else if (event.key === 'Home') next = 0;
  else if (event.key === 'End') next = last;
  if (next === null) return;
  event.preventDefault();
  pickPreset(PRESET_LEVELS[next]);
  dom.presets.querySelector(`[data-preset="${PRESET_LEVELS[next]}"]`).focus();
});

dom.dedupe.addEventListener('change', () => setState({ dedupe: dom.dedupe.checked }));

const override = (key, element, cast = Number) =>
  element.addEventListener('input', () => setState({ overrides: { ...state.overrides, [key]: cast(element.value) } }));

override('colors', dom.colors);
override('lossy', dom.lossy);
override('bayerScale', dom.bayer);
override('dither', dom.dither, String);

dom.advancedReset.addEventListener('click', () => setState({ overrides: {} }));

// ── render ───────────────────────────────────────────────────────────────

/** Run a render-shaped job, showing progress and storing the result. */
/** `resultLabel` names what a partial render shows, e.g. "part 2"; null for the GIF itself. */
async function runJob(begin, label, spec = renderSpec(), resultLabel = null) {
  if (!state.video) return null;
  const requestedSpec = JSON.stringify(spec);
  notify('');
  setState({ busy: true, result: null, fitNotes: null, resultLabel });
  veil(2, label);

  try {
    const result = await awaitJob(await started(begin()), veil);
    setState({
      result,
      calibration: result.calibration ?? state.calibration,
      renderedSpec: requestedSpec,
      mp4Bpp: result.format === 'mp4' ? result.measured_bpp : state.mp4Bpp,
    });
    return result;
  } catch (error) {
    jobFailed(error);
    return null;
  } finally {
    setState({ busy: false, jobId: null });
    hideVeil();
  }
}

dom.renderBtn.addEventListener('click', () => {
  // While busy the button is the way out, so a wrong setting costs no waiting.
  if (state.busy) {
    cancelRunningJob();
    return;
  }
  runJob(() => startRender(renderSpec()), 'Preparing');
});

/** The selection on the timeline alone, whether or not a sequence exists. */
function renderSelection() {
  if (!state.video || state.busy) return;
  const spec = renderSpec([editorFields()]);
  const editing = state.segments.findIndex((part) => part.id === state.activeSegment);
  const label = !state.segments.length ? null : editing >= 0 ? `part ${editing + 1}` : 'the selection';
  runJob(() => startRender(spec), 'Rendering the selection', spec, label);
}

dom.renderSelection.addEventListener('click', renderSelection);

// GIF frame delays are counted in hundredths of a second, and browsers slow
// anything faster than about 30 fps right down, so only MP4 goes higher.
const GIF_MAX_FPS = 30;
const MP4_MAX_FPS = 60;
const fpsCeiling = () => {
  const cap = state.format === 'mp4' ? MP4_MAX_FPS : GIF_MAX_FPS;
  return state.video ? Math.min(cap, sourceFps()) : cap;
};

dom.formatPicks.addEventListener('click', (event) => {
  const format = event.target.closest('button[data-format]')?.dataset.format;
  if (!format || format === state.format) return;
  setState({ format });
  if (state.fps > fpsCeiling()) setState({ fps: fpsCeiling() });
});

/**
 * Source quality: the encode is near-lossless, and the frame size and rate
 * jump to the source's own. Those two are ordinary settings afterwards, so the
 * user can still trim them; switching off puts back what they were before.
 */
function setSourceQuality(on) {
  if (on === state.mp4Source) return;
  if (on) {
    const { fps, autoSize, desiredWidth, desiredHeight } = state;
    setState({
      mp4Source: true,
      mp4SourceBase: { fps, autoSize, desiredWidth, desiredHeight },
      autoSize: true,
      fps: fpsCeiling(),
      mp4Bitrate: null,
    });
  } else {
    setState({ mp4Source: false, ...(state.mp4SourceBase ?? {}), mp4SourceBase: null });
  }
  applySize({});
}

dom.mp4Source.addEventListener('change', () => setSourceQuality(dom.mp4Source.checked));

// The MP4 group's ladder. Source is the same switch as the one by the render
// button; the other levels are plain quality settings.
dom.mp4Levels.addEventListener('click', (event) => {
  const level = event.target.closest('button[data-level]')?.dataset.level;
  if (!level) return;
  if (level === 'source') {
    setSourceQuality(true);
    return;
  }
  setSourceQuality(false);
  setState({ mp4Level: level, mp4Bitrate: null });
});

dom.copyGif.addEventListener('click', async () => {
  if (!state.result) return;
  try {
    if (!navigator.clipboard?.write || typeof ClipboardItem === 'undefined') {
      throw new Error('unsupported');
    }
    const response = await fetch(state.result.gif_url);
    const blob = await response.blob();
    await navigator.clipboard.write([new ClipboardItem({ [blob.type]: blob })]);
    const previous = dom.copyGif.textContent;
    dom.copyGif.textContent = 'Copied';
    setTimeout(() => { dom.copyGif.textContent = previous; }, 1600);
  } catch {
    notify('This browser cannot copy GIF files directly. Downloading still works.', 'info');
  }
});

// Fitting writes its trade-offs into the panel. The settings the user chose are
// kept aside, so a later fit starts from them rather than from an already
// degraded result, and so they can be restored once no limit is wanted.
const FIT_FIELDS = ['fps', 'autoSize', 'desiredWidth', 'desiredHeight', 'overrides', 'mp4Bitrate'];
const fitSnapshot = () => Object.fromEntries(FIT_FIELDS.map((key) => [key, state[key]]));

dom.fitBtn.addEventListener('click', async () => {
  if (!state.targetMB) return;
  const targetBytes = state.targetMB * 1024 * 1024;
  const chosen = state.fitBase ?? fitSnapshot();
  setState(chosen);
  applySize(); // the crop may have changed since, so derive the output from it
  const result = await runJob(
    () => startFit(renderSpec(), targetBytes),
    `Fitting under ${state.targetMB} MB`,
  );
  if (!result) return;

  // Adopt the settings that actually hit the target, so the panel reflects reality.
  const fitted = result.spec || {};
  setState({
    fps: fitted.fps ?? state.fps,
    autoSize: false,
    desiredWidth: fitted.width ?? state.desiredWidth,
    outWidth: fitted.width ?? state.outWidth,
    outHeight: fitted.height ?? state.outHeight,
    overrides: {
      ...state.overrides,
      colors: fitted.colors ?? state.overrides.colors,
      lossy: fitted.lossy ?? state.overrides.lossy,
    },
    // An MP4 fit is a bitrate; keeping it makes "Render again" reproduce the fit.
    mp4Bitrate: fitted.bitrate_kbps ?? state.mp4Bitrate,
    fitBase: chosen,
    fitNotes: {
      met: result.met,
      renders: result.renders,
      notes: result.notes || [],
    },
  });
  setState({ renderedSpec: JSON.stringify(renderSpec()) });
});

function restoreFitBase() {
  if (!state.fitBase) return;
  setState({ ...state.fitBase, fitBase: null, fitNotes: null });
  applySize();
}

dom.fitRestore.addEventListener('click', restoreFitBase);

function slug(text) {
  return (text || 'clip').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 48) || 'clip';
}

// ── source download cue ──────────────────────────────────────────────────

// The card sits below five settings groups, so the strip under the URL field
// takes the user there and makes the button announce itself on arrival.
let calloutTimer = null;
const stillness = matchMedia('(prefers-reduced-motion: reduce)');

/** Bring the source card into view, wherever the layout keeps it.
 *
 * On the wide layout the panel is its own sticky scroll container, and asking
 * the element to centre itself makes the browser scroll the page as well —
 * which only shifts the sticky panel around. Scrolling the panel by hand keeps
 * the page still and puts the card in the middle of the panel instead of
 * against its bottom edge. Narrow layouts have a static panel, where the
 * ordinary path is the right one. */
function revealSourceCard(behavior) {
  const panel = dom.videoMeta.closest('.panel');
  const overflow = panel ? panel.scrollHeight - panel.clientHeight : 0;
  if (!panel || overflow < 1) {
    dom.videoMeta.scrollIntoView({ behavior, block: 'center' });
    return;
  }
  const card = dom.videoMeta.getBoundingClientRect();
  const within = card.top - panel.getBoundingClientRect().top + panel.scrollTop;
  const centred = within - (panel.clientHeight - card.height) / 2;
  panel.scrollTo({ top: Math.max(0, Math.min(centred, overflow)), behavior });
}

dom.sourceCue.addEventListener('click', () => {
  if (!state.video) return;
  revealSourceCard(stillness.matches ? 'auto' : 'smooth');

  // Light stays on while the eye arrives, then drains through the CSS fade.
  clearTimeout(calloutTimer);
  dom.videoMeta.classList.add('is-lit');
  calloutTimer = setTimeout(() => dom.videoMeta.classList.remove('is-lit'), 3200);

  // Keyboard users land on the button itself; the scroll is already under way.
  dom.downloadVideo.focus({ preventScroll: true });
});

// Once it has been used, the light has nothing left to point at.
dom.downloadVideo.addEventListener('click', () => {
  clearTimeout(calloutTimer);
  dom.videoMeta.classList.remove('is-lit');
});

// ── sequence ─────────────────────────────────────────────────────────────

// The editor (timeline selection, crop, zoom, followed path) works on one
// thing at a time: either the next part, still a draft, or a part opened from
// the list. Opening a part parks the draft and loads that part's own settings;
// "Done editing" puts the draft back. So adding part two can never move part one.

let nextPartId = 1;

/** Redraw what depends on the crop after the editor was swapped wholesale. */
function editorSwapped() {
  if (state.linkAspect || state.autoSize) applySize({});
  crop.draw();
}

function addPart() {
  if (!state.video || state.activeSegment !== null) return;
  if (state.segments.length >= MAX_PARTS) {
    notify(`A sequence holds at most ${MAX_PARTS} parts.`, 'info');
    return;
  }
  const room = MAX_SECONDS - partsSeconds();
  if (state.duration > room + 1e-6) {
    notify(`The GIF is capped at ${MAX_SECONDS} s and ${room.toFixed(1)} s is left. Shorten the selection first.`, 'info');
    return;
  }
  notify('');
  // The editor stays as it is, so the next part starts from this one's framing:
  // move the selection and the crop on to the next moment and add again.
  setState({ segments: [...state.segments, { id: nextPartId++, ...editorFields() }] });
}

function openPart(id) {
  if (state.activeSegment === id) {
    finishEditing();
    return;
  }
  const part = state.segments.find((candidate) => candidate.id === id);
  if (!part) return;
  if (state.playingPart !== null) dom.video.pause();
  const { id: _, ...fields } = part;
  setState({
    ...fields,
    draft: state.activeSegment === null ? editorFields() : state.draft,
    activeSegment: id,
  });
  previewSeek(part.start);
  editorSwapped();
}

function finishEditing() {
  if (state.activeSegment === null) return;
  setState({ ...(state.draft ?? editorFields()), activeSegment: null, draft: null });
  previewSeek(state.start);
  editorSwapped();
}

function movePart(from, to) {
  if (from === to || to < 0 || to >= state.segments.length) return;
  const segments = [...state.segments];
  const [part] = segments.splice(from, 1);
  segments.splice(to, 0, part);
  setState({ segments });
}

function removePart(id) {
  if (state.activeSegment === id) finishEditing();
  setState({ segments: state.segments.filter((part) => part.id !== id) });
}

/** Render one part on its own, to judge it or to keep it as a file of its own. */
function previewPart(id) {
  const index = state.segments.findIndex((part) => part.id === id);
  if (index < 0 || state.busy) return Promise.resolve(null);
  const spec = renderSpec([state.segments[index]]);
  return runJob(() => startRender(spec), `Rendering part ${index + 1}`, spec, `part ${index + 1}`);
}

/** Hand a finished render to the browser as a download. */
function saveResult(result, name) {
  const extension = result.format === 'mp4' ? 'mp4' : 'gif';
  const fileName = `${name}.${extension}`;
  const link = document.createElement('a');
  link.href = `${result.gif_url}?download=1&filename=${encodeURIComponent(fileName)}`;
  link.download = fileName;
  document.body.append(link);
  link.click();
  link.remove();
}

/** Render one part in the chosen format and save it straight away. */
async function downloadPart(id) {
  const index = state.segments.findIndex((part) => part.id === id);
  if (index < 0) return;
  const result = await previewPart(id);
  if (result) saveResult(result, `${slug(state.video?.title)}-part-${index + 1}`);
}

dom.addPart.addEventListener('click', addPart);
dom.donePart.addEventListener('click', finishEditing);
dom.renderPart.addEventListener('click', () => previewPart(state.activeSegment));
dom.downloadPart.addEventListener('click', () => downloadPart(state.activeSegment));
dom.clearParts.addEventListener('click', () => {
  if (state.playingPart !== null) dom.video.pause();
  finishEditing();
  setState({ segments: [], playingPart: null });
});

dom.parts.addEventListener('click', (event) => {
  const card = event.target.closest('.part');
  if (!card) return;
  const id = Number(card.dataset.id);
  const index = state.segments.findIndex((part) => part.id === id);
  const action = event.target.closest('button')?.dataset.act;
  if (action === 'earlier') movePart(index, index - 1);
  else if (action === 'later') movePart(index, index + 1);
  else if (action === 'remove') removePart(id);
  else if (action === 'preview') previewPart(id);
  else if (action === 'download') downloadPart(id);
  else openPart(id);
});

// Drag a card onto another to reorder. The drop lands before or after the
// target depending on which half of it the pointer is over.
let draggedPart = null;

const clearDropMarks = () => {
  for (const card of dom.parts.children) card.classList.remove('drop-before', 'drop-after');
};

const dropsAfter = (card, event) => {
  const bounds = card.getBoundingClientRect();
  return event.clientX > bounds.left + bounds.width / 2;
};

dom.parts.addEventListener('dragstart', (event) => {
  const card = event.target.closest('.part');
  if (!card) return;
  draggedPart = Number(card.dataset.id);
  card.classList.add('dragging');
  event.dataTransfer.effectAllowed = 'move';
  // Firefox will not start a drag that carries no data.
  event.dataTransfer.setData('text/plain', card.dataset.id);
});

dom.parts.addEventListener('dragover', (event) => {
  const card = event.target.closest('.part');
  if (draggedPart === null || !card) return;
  event.preventDefault();
  clearDropMarks();
  if (Number(card.dataset.id) !== draggedPart) {
    card.classList.add(dropsAfter(card, event) ? 'drop-after' : 'drop-before');
  }
});

dom.parts.addEventListener('drop', (event) => {
  const card = event.target.closest('.part');
  if (draggedPart === null || !card) return;
  event.preventDefault();
  const from = state.segments.findIndex((part) => part.id === draggedPart);
  let to = state.segments.findIndex((part) => part.id === Number(card.dataset.id));
  if (dropsAfter(card, event)) to += 1;
  // Taking the dragged card out first shifts everything after it one place left.
  if (from < to) to -= 1;
  movePart(from, to);
});

dom.parts.addEventListener('dragend', () => {
  draggedPart = null;
  clearDropMarks();
  for (const card of dom.parts.children) card.classList.remove('dragging');
});

// Play the parts back to back, the way the GIF will, each with its own framing.
// Timeupdate fires only a few times a second, which would overshoot every cut,
// so this watches per frame.
function watchSequence() {
  const index = state.playingPart;
  if (index === null) return;
  const part = state.segments[index];
  if (!part) {
    dom.video.pause();
    return;
  }
  if (dom.video.currentTime >= part.start + part.duration) {
    let next = index + 1;
    if (next >= state.segments.length) {
      if (!state.loop) {
        dom.video.pause();
        return;
      }
      next = 0;
    }
    dom.video.currentTime = state.segments[next].start;
    setState({ playingPart: next });
  }
  requestAnimationFrame(watchSequence);
}

dom.playSequence.addEventListener('click', () => {
  if (state.playingPart !== null) {
    dom.video.pause();
    return;
  }
  if (!state.segments.length) return;
  dom.video.currentTime = state.segments[0].start;
  setState({ playingPart: 0 });
  dom.video.play()
    .then(() => requestAnimationFrame(watchSequence))
    .catch(() => setState({ playingPart: null }));
});

let paintedParts = '';

function partBadges(part) {
  const badges = [];
  if (part.crop) badges.push('crop');
  if (part.crop && part.track?.length >= 2) badges.push('follow');
  if (part.zoom) badges.push(`zoom ${part.zoom.direction}`);
  return badges.map((badge) => `<i>${badge}</i>`).join('');
}

function paintSequence(loaded) {
  const count = state.segments.length;
  const editing = state.segments.findIndex((part) => part.id === state.activeSegment);
  dom.sequenceSummary.textContent = count
    ? `${count} part${count === 1 ? '' : 's'} · ${partsSeconds().toFixed(2)} s`
    : 'single clip';
  dom.addPart.hidden = editing >= 0;
  dom.addPart.disabled = !loaded || state.busy;
  dom.donePart.hidden = editing < 0;
  // While a part is open, rendering or saving just that part is one click away.
  const formatName = state.format.toUpperCase();
  for (const [button, verb] of [[dom.renderPart, 'Render'], [dom.downloadPart, 'Download']]) {
    button.hidden = editing < 0;
    button.disabled = state.busy;
    button.textContent = `${verb} part ${editing + 1} as ${formatName}`;
  }
  dom.donePart.textContent = editing >= 0 ? `Done editing part ${editing + 1}` : 'Done editing';
  dom.playSequence.disabled = !count;
  dom.playSequence.textContent = state.playingPart !== null ? 'Stop' : 'Play sequence';
  dom.clearParts.hidden = !count;

  dom.sequenceHint.textContent = editing >= 0
    ? `Editing part ${editing + 1}: its range, crop, zoom and followed path are on the video and timeline now. `
      + 'Resizing the crop resizes every part, so they all fill the GIF the same way.'
    : count
      ? 'Set up the next moment and add it. Click a part to edit it, drag it to reorder, or Render it alone.'
      : 'Pick several moments: select and frame one, add it, then move on to the next. '
        + 'The GIF plays them in order, and each part keeps its own crop, zoom and followed path.';

  // Rebuilding mid-drag would pull the card out from under the pointer, so the
  // list is only redrawn when something it shows has changed.
  const key = JSON.stringify([state.segments, state.activeSegment, state.playingPart, state.busy, state.format]);
  if (key === paintedParts) return;
  paintedParts = key;

  dom.parts.replaceChildren(...state.segments.map((part, index) => {
    const card = document.createElement('li');
    card.className = 'part';
    card.classList.toggle('active', part.id === state.activeSegment);
    card.classList.toggle('playing', index === state.playingPart);
    card.draggable = true;
    card.dataset.id = String(part.id);
    card.title = part.id === state.activeSegment
      ? 'Click to finish editing this part.'
      : 'Click to edit this part. Drag to reorder.';
    card.innerHTML = `
      <span class="part-index">${index + 1}</span>
      <span class="part-range">
        <b>${formatTime(part.start)} – ${formatTime(part.start + part.duration)}</b>
        <small>${part.duration.toFixed(2)} s</small>
      </span>
      <span class="part-badges">${partBadges(part)}</span>
      <span class="part-tools">
        <button type="button" data-act="preview" title="Render only this part" ${state.busy ? 'disabled' : ''}>Render</button>
        <button type="button" data-act="download" title="Render only this part and download it" aria-label="Download part ${index + 1}" ${state.busy ? 'disabled' : ''}>↓</button>
        <button type="button" data-act="earlier" aria-label="Move part ${index + 1} earlier" ${index === 0 ? 'disabled' : ''}>←</button>
        <button type="button" data-act="later" aria-label="Move part ${index + 1} later" ${index === count - 1 ? 'disabled' : ''}>→</button>
        <button type="button" data-act="remove" aria-label="Remove part ${index + 1}">✕</button>
      </span>`;
    return card;
  }));
}

// ── one render pass over the DOM ─────────────────────────────────────────

function paint() {
  const loaded = Boolean(state.video);
  const complete = Boolean(state.result);
  // A single part's preview is never "stale": it was not meant to be the GIF.
  const partial = complete && Boolean(state.resultLabel);
  const resultIsStale = complete && !partial && state.renderedSpec !== JSON.stringify(renderSpec());
  const formatName = state.format.toUpperCase();
  const wholeName = state.segments.length ? `${formatName} sequence` : formatName;
  dom.placeholder.hidden = loaded;
  dom.load.disabled = state.busy;
  dom.renderBtn.disabled = !loaded || (state.busy && !state.jobId);
  dom.renderSelection.disabled = !loaded || state.busy || state.guiding;
  dom.cancelJob.hidden = !state.jobId;
  dom.fitBtn.disabled = !loaded || state.busy || !state.targetMB;
  dom.fitRestore.hidden = !state.fitBase;
  dom.fitRestore.disabled = state.busy;
  dom.renderLabel.textContent = state.busy
    ? (state.jobId ? 'Cancel rendering' : 'Rendering…')
    : partial || !complete
      ? `Render ${wholeName}`
      : resultIsStale
        ? `Update ${wholeName}`
        : 'Render again';

  dom.studioTitle.textContent = loaded ? 'Frame it. Trim it. Make it loop.' : 'Catch the part worth replaying.';
  dom.resultTitle.textContent = !loaded
    ? 'Load a video to unlock this.'
    : partial
      ? `Preview of ${state.resultLabel} on its own.`
      : complete && !resultIsStale
        ? 'Your loop is ready.'
        : 'Ready when your loop is.';
  dom.panelStatus.className = `panel-status ${loaded ? 'ready' : 'waiting'}`;
  dom.panelStatus.innerHTML = loaded ? '<i></i> Ready to tune' : '<i></i> Waiting for a video';

  for (const step of [dom.stepLink, dom.stepEdit, dom.stepExport]) {
    step.classList.remove('current', 'done');
  }
  if (!loaded) {
    dom.stepLink.classList.add('current');
  } else if (!complete) {
    dom.stepLink.classList.add('done');
    dom.stepEdit.classList.add('current');
  } else {
    dom.stepLink.classList.add('done');
    dom.stepEdit.classList.add('done');
    dom.stepExport.classList.add('done');
  }

  // clip readout — frames and playback describe the GIF, so a sequence counts whole
  const frames = frameCount(clipSeconds(), state.fps, state.speed, state.boomerang);
  dom.rIn.textContent = formatTime(state.start);
  dom.rOut.textContent = formatTime(clipEnd());
  dom.rLen.textContent = `${state.duration.toFixed(2)} s`;
  dom.rFrames.textContent = String(frames);
  dom.rPlays.textContent = `${playbackSeconds().toFixed(2)} s`;
  timeline.render(playheadTime());

  // crop fields — where a followed crop is right now, not where it started
  const pixels = cropPixels(cropAt(playheadTime()));
  const { width: regionW, height: regionH } = region();
  for (const [input, value] of [
    [dom.cropX, pixels ? pixels.x : 0],
    [dom.cropY, pixels ? pixels.y : 0],
    [dom.cropW, regionW],
    [dom.cropH, regionH],
  ]) {
    input.disabled = !loaded;
    if (document.activeElement !== input) input.value = loaded ? value : '';
  }
  dom.cropHint.textContent = pixels
    ? `Cropping ${pixels.width} × ${pixels.height} from ${state.video.width} × ${state.video.height}. Double-click the frame to clear.`
    : 'Drag on the video to draw a region. Drag inside to move it, grab an edge to resize.';

  // output size
  if (document.activeElement !== dom.outW) dom.outW.value = state.outWidth;
  if (document.activeElement !== dom.outH) dom.outH.value = state.outHeight;
  dom.outH.disabled = state.linkAspect;
  dom.linkAspect.checked = state.linkAspect;
  dom.aspectNote.textContent = state.linkAspect
    ? 'Locked to the crop’s aspect ratio.'
    : 'Width and height move on their own.';
  dom.sizeSummary.textContent = `${state.outWidth}×${state.outHeight}`;
  for (const chip of dom.sizePresets.children) {
    const isAuto = chip.dataset.size === 'auto';
    chip.disabled = isAuto && !loaded;
    chip.classList.toggle('on', isAuto
      ? state.autoSize
      : !state.autoSize && Number(chip.dataset.w) === state.desiredWidth);
  }
  const scale = regionW ? state.outWidth / regionW : 1;
  const capped = state.outWidth < state.desiredWidth;
  dom.scaleHint.textContent = loaded
    ? capped
      ? `Capped at the ${regionW} × ${regionH} region. Upscaling would only blur it.`
      : state.autoSize
        ? `Native resolution of the ${regionW} × ${regionH} source region.`
        : `${Math.round(scale * 100)}% of the ${regionW} × ${regionH} region`
    : '';

  // crop aspect chips
  for (const chip of dom.cropAspects.children) {
    const choice = chip.dataset.aspect;
    const isFree = choice === 'free';
    const [w, h] = isFree ? [0, 0] : choice.split(':').map(Number);
    chip.classList.toggle('on', isFree
      ? state.cropAspect === null
      : Math.abs((state.cropAspect ?? -1) - w / h) < 0.001);
  }

  // The collapsed group headers carry the value, so nothing has to be reopened
  // to see where a dial was left.
  const activeAspect = [...dom.cropAspects.children].find((chip) => chip.classList.contains('on'));
  dom.cropSummary.textContent = pixels
    ? `${pixels.width}×${pixels.height}`
    : activeAspect?.dataset.aspect || 'free';

  // motion — never offer more frames per second than the source contains
  const ceiling = fpsCeiling();
  dom.fps.max = ceiling;
  // The printed ticks are laid out for the usual 5 to 30 range only.
  dom.fps.closest('.slider').nextElementSibling.hidden = ceiling !== GIF_MAX_FPS;
  dom.fps.value = state.fps;
  dom.fpsOut.textContent = `${state.fps} fps`;
  dom.fpsHint.textContent = loaded && state.fps >= ceiling
    ? ceiling < sourceFps()
      ? `GIFs top out at ${GIF_MAX_FPS} fps; switch to MP4 for the source's ${sourceFps()} fps.`
      : `At the source's own ${sourceFps()} fps. Asking for more would only duplicate frames.`
    : 'Higher is smoother but multiplies the file size almost linearly.';

  dom.speed.value = state.speed;
  dom.speedOut.textContent = `${state.speed.toFixed(2)}×`;
  dom.previewSpeed.checked = state.previewSpeed;
  // The default rate too, so the choice survives the player loading a new source.
  const rate = state.guiding ? GUIDE_RATE : state.previewSpeed ? state.speed : 1;
  if (dom.video.playbackRate !== rate) dom.video.playbackRate = rate;
  dom.video.defaultPlaybackRate = rate;
  dom.boomerang.checked = state.boomerang;
  dom.loopForever.checked = state.loopForever;
  dom.motionSummary.textContent = `${state.fps} fps · ${state.speed.toFixed(2)}×`
    + (state.zoom ? ` · zoom ${state.zoom.direction}` : '');

  // zoom effect, which belongs to whichever part is open
  const zoomMode = state.zoom?.direction ?? 'none';
  for (const chip of dom.zoomModes.children) {
    const on = chip.dataset.zoom === zoomMode;
    chip.classList.toggle('on', on);
    chip.setAttribute('aria-checked', String(on));
    chip.disabled = !loaded;
  }
  dom.zoomAmountRow.hidden = !state.zoom;
  dom.zoomAmount.value = state.zoom?.amount ?? DEFAULT_ZOOM_AMOUNT;
  dom.zoomAmountOut.textContent = `${Number(dom.zoomAmount.value).toFixed(2)}×`;
  const editingIndex = state.segments.findIndex((part) => part.id === state.activeSegment);
  dom.zoomScope.textContent = editingIndex >= 0
    ? `part ${editingIndex + 1}`
    : state.segments.length ? 'next part' : 'whole clip';

  // following a target
  const following = Boolean(state.crop) && state.track.length >= 2;
  dom.follow.disabled = !loaded || !state.crop || state.busy || state.guiding;
  dom.guide.disabled = !state.guiding && (!loaded || !state.crop || state.busy);
  dom.guide.textContent = state.guiding ? 'Cancel guiding' : 'Guide with pointer';
  dom.follow.textContent = following ? 'Follow again' : 'Follow target';
  dom.unfollow.hidden = !following;
  dom.followHint.textContent = !state.crop
    ? 'Draw a crop around the person or object first, on any frame of the clip.'
    : following
      ? `The crop follows the target through ${formatTime(state.track[0].t)}–${formatTime(state.track[state.track.length - 1].t)}. `
        + 'Drag it to nudge the whole path; follow again after changing the clip a lot.'
      : 'Frame the target on the frame you are looking at, then the crop moves with it through the clip.';

  // magnifier
  dom.viewZoom.hidden = !loaded;
  dom.viewZoomReset.textContent = `${Math.round(state.viewZoom * 100)}%`;
  dom.viewZoomIn.disabled = state.viewZoom >= VIEW_ZOOM_MAX;
  dom.viewZoomOut.disabled = state.viewZoom <= 1;

  // quality
  const active = settings();
  dom.sharpen.value = state.sharpen;
  dom.sharpenOut.textContent = state.sharpen.toFixed(1);
  dom.dedupe.checked = state.dedupe;
  dom.dedupe.disabled = state.boomerang;
  dom.dedupeHint.textContent = state.boomerang
    ? 'Unavailable with ping-pong: reversing needs evenly timed frames.'
    : 'Removes repeated frames and stretches the remaining delays, so static shots shrink without changing the playback speed.';
  const level = PRESET_LEVELS.indexOf(state.preset);
  dom.presets.dataset.level = level;
  for (const button of dom.presets.querySelectorAll('button[data-preset]')) {
    const at = PRESET_LEVELS.indexOf(button.dataset.preset);
    const chosen = at === level;
    button.classList.toggle('on', chosen);
    button.classList.toggle('past', at < level);
    button.setAttribute('aria-checked', String(chosen));
    // One tab stop for the whole ladder; arrows move within it.
    button.tabIndex = chosen ? 0 : -1;
    if (chosen) dom.presetNote.textContent = button.dataset.note;
  }
  dom.qualitySummary.textContent = state.preset === 'max' ? 'max detail' : state.preset;
  dom.colors.value = active.colors;
  dom.colorsOut.textContent = active.colors;
  dom.lossy.value = active.lossy;
  dom.lossyOut.textContent = active.lossy;
  dom.dither.value = active.dither;
  dom.bayer.value = active.bayerScale;
  dom.bayerOut.textContent = active.bayerScale;
  dom.bayer.disabled = active.dither !== 'bayer';

  // estimate — meaningless until there is a video to measure against
  const bpp = bitsPerPixel({ ...active, sharpen: state.sharpen });
  const mp4 = state.format === 'mp4';
  // An MP4's size depends on its content far more than any formula can say,
  // so it is only predicted once one has been measured for this clip.
  const predicted = mp4
    ? (state.mp4Bpp ? Math.floor((state.outWidth * state.outHeight * frames * state.mp4Bpp) / 8) : 0)
    : estimateBytes(state.outWidth, state.outHeight, frames, bpp, state.calibration);
  // Without a limit the meters still need a scale; they just never read as over.
  const limitBytes = (state.targetMB || NO_LIMIT_SCALE_MB) * 1024 * 1024;
  writeSize(dom.estSize, !loaded ? 'Not ready' : predicted ? formatBytes(predicted) : 'After a render');
  setMeter(dom.weightFill, loaded && predicted ? predicted / limitBytes : 0.08, Boolean(state.targetMB));
  dom.estNote.textContent = !loaded
    ? 'load a video first'
    : mp4
      ? state.mp4Bpp ? 'from your last MP4 render' : state.mp4Source ? 'source quality is large; measured on render' : 'MP4 is usually 5–10× smaller than the GIF'
      : state.calibration
        ? 'calibrated from your last render'
        : 'rough guess, render once to calibrate';

  dom.sourceQualityRow.hidden = state.format !== 'mp4';

  // Look & weight is all GIF machinery: palettes, dithering, lossy LZW, plus
  // sharpening and frame dropping, which only trade fidelity away. An MP4 gets
  // its own, simpler quality group instead.
  dom.gifGroup.hidden = state.format === 'mp4';
  dom.mp4Group.hidden = state.format !== 'mp4';
  const mp4Level = state.mp4Source ? 'source' : state.mp4Level;
  for (const button of dom.mp4Levels.children) {
    const on = button.dataset.level === mp4Level;
    button.classList.toggle('on', on);
    button.setAttribute('aria-checked', String(on));
    button.disabled = state.busy;
  }
  dom.mp4Summary.textContent = state.mp4Bitrate ? `${state.mp4Bitrate} kbit/s` : mp4Level;
  dom.mp4LevelNote.textContent = state.mp4Bitrate
    ? `Fitted to your size limit at ${state.mp4Bitrate} kbit/s. Pick a level to go back to quality-based encoding.`
    : state.mp4Source
      ? "The source's resolution and frame rate, encoded near-lossless. Big files; nothing visible is lost."
      : MP4_LEVELS[state.mp4Level].note;
  dom.mp4Source.checked = state.mp4Source;
  dom.mp4Source.disabled = !loaded || state.busy;

  for (const button of dom.formatPicks.children) {
    const on = button.dataset.format === state.format;
    button.classList.toggle('on', on);
    button.setAttribute('aria-checked', String(on));
    button.disabled = state.busy;
  }

  // target size
  dom.targetMB.value = state.targetMB ?? '';
  if (!state.targetMB) {
    dom.fitNote.textContent = `No limit set. Enter a size in MB to fit the ${formatName} under it.`;
  } else if (state.fitNotes) {
    const { met, renders, notes } = state.fitNotes;
    const detail = notes.length ? `: ${notes.join(', ')}` : '';
    dom.fitNote.textContent = met
      ? `Fitted in ${renders} render${renders === 1 ? '' : 's'}${detail}.`
      : `Could not reach ${state.targetMB} MB after ${renders} renders${detail}.`;
  } else if (mp4) {
    dom.fitNote.textContent = 'Encodes at exactly the bitrate that fills the limit, keeping frame rate and size.';
  } else {
    dom.fitNote.textContent = 'Renders repeatedly, giving up lossy compression first, '
      + 'then colours, then frame rate, and resolution only as a last resort.';
  }

  // result
  const result = state.result;
  dom.actualBlock.hidden = !result;
  dom.preview.hidden = !result;
  dom.download.hidden = !result;
  const resultIsMp4 = result?.format === 'mp4';
  // Clipboards take images, not video files.
  dom.copyGif.hidden = !result || resultIsMp4;
  if (result) {
    writeSize(dom.actSize, formatBytes(result.bytes));
    setMeter(dom.actualFill, result.bytes / limitBytes, Boolean(state.targetMB));
    const saved = result.bytes_before_optimize
      ? Math.round((1 - result.bytes / result.bytes_before_optimize) * 100)
      : 0;
    dom.actNote.textContent = resultIsStale
      ? 'preview uses your previous settings'
      : saved > 0
        ? `optimiser saved ${saved}%`
        : 'already minimal';
    dom.gif.hidden = resultIsMp4;
    dom.mp4.hidden = !resultIsMp4;
    const shown = resultIsMp4 ? dom.mp4 : dom.gif;
    if (shown.getAttribute('src') !== result.gif_url) shown.src = result.gif_url;
    if (resultIsMp4) dom.gif.removeAttribute('src');
    else dom.mp4.removeAttribute('src');
    // Both dimensions, so the box is the right shape before the file decodes.
    shown.width = result.width;
    shown.height = result.height;
    dom.gifMeta.textContent = [
      `${result.width} × ${result.height}`,
      `${result.frames} frames @ ${result.fps} fps`,
      `${result.duration.toFixed(2)} s`,
      resultIsMp4 ? 'H.264, no sound' : `${result.colors} colours`,
      `rendered in ${(result.elapsed_ms / 1000).toFixed(1)} s`,
    ].join('  ·  ');
    dom.download.textContent = resultIsMp4 ? 'Download MP4' : 'Download GIF';
    const niceName = `${slug(state.video?.title)}-${state.start.toFixed(1)}s.${resultIsMp4 ? 'mp4' : 'gif'}`;
    dom.download.href = `${result.gif_url}?download=1&filename=${encodeURIComponent(niceName)}`;
    dom.download.setAttribute('download', niceName);
  }

  paintSequence(loaded);

  // source panel
  dom.videoMeta.hidden = !loaded;
  dom.sourceCue.hidden = !loaded;
  if (loaded) {
    const provider = state.video.provider || 'video';
    const providerNames = { twitter: 'Twitter / X', instagram: 'Instagram', tiktok: 'TikTok' };
    const providerName = providerNames[provider]
      || `${provider.charAt(0).toUpperCase()}${provider.slice(1)}`;
    dom.metaProvider.textContent = `${providerName} source`;
    dom.metaTitle.textContent = state.video.title;
    dom.metaDims.textContent = `${state.video.width} × ${state.video.height} · ${state.video.fps} fps · ${formatTime(state.video.duration)}`;
    const videoName = slug(state.video.title);
    const separator = state.video.stream_url.includes('?') ? '&' : '?';
    dom.downloadVideo.href = `${state.video.stream_url}${separator}download=1&filename=${encodeURIComponent(videoName)}`;
    dom.downloadVideo.setAttribute('download', videoName);
    dom.downloadVideoNote.textContent = state.video.height ? `${state.video.height}p` : '';
  }

  syncRanges();
}

subscribe(paint);

// ── boot ─────────────────────────────────────────────────────────────────

const SERVER_FEATURES = ['segments', 'track', 'zoom', 'cancel', 'mp4', 'source-quality'];

(async function boot() {
  try {
    const info = await health();
    useModel(info.model);
    setState({ presets: info.presets });
    // A server started before an update keeps running the old code while the
    // browser loads the new page; it would silently ignore what it does not
    // know (a sequence renders as one long clip). Say so instead.
    const missingFeatures = SERVER_FEATURES.filter((feature) => !(info.features || []).includes(feature));
    if (missingFeatures.length) {
      notify('The app server is running older code than this page, so sequences, target following, '
        + 'zoom, cancelling and MP4 export will not work. Stop the server and start it again (./run.sh), then reload.');
    }
    if (!info.ok) {
      notify(`Missing required tools: ${info.missing.join(', ')}\nInstall with: ${info.hints.filter(Boolean).join(' && ')}`);
    }
  } catch (error) {
    notify(`Could not reach the backend: ${error.message}`);
  }
  paint();
})();
