// Wiring: DOM events in, state changes out, then one render pass over the DOM.

import { awaitJob, estimateSize, health, startFit, startLoad, startRender } from './api.js';
import { initCrop } from './crop.js';
import { bitsPerPixel, estimateBytes, formatBytes, frameCount, useModel } from './estimate.js';
import { cropPixels, renderSpec, setState, settings, sourceAspect, state, subscribe } from './state.js';
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
  veil: el('veil'), veilText: el('veil-text'), veilFill: el('veil-fill'),
  play: el('play'), now: el('now'), total: el('total'), loop: el('loop'),
  stepBack: el('step-back'), stepFwd: el('step-fwd'),
  markIn: el('mark-in'), markOut: el('mark-out'),
  zoomFit: el('zoom-fit'), viewLabel: el('view-label'),
  track: el('track'), sel: el('sel'), hIn: el('h-in'), hOut: el('h-out'),
  playhead: el('playhead'), scrub: el('scrub'),
  rIn: el('r-in'), rOut: el('r-out'), rLen: el('r-len'), rFrames: el('r-frames'),
  rPlays: el('r-plays'),
  cropX: el('crop-x'), cropY: el('crop-y'), cropW: el('crop-w'), cropH: el('crop-h'),
  cropReset: el('crop-reset'), cropAspects: el('crop-aspects'), cropHint: el('crop-hint'),
  cropSummary: el('crop-summary'), sizeSummary: el('size-summary'), motionSummary: el('motion-summary'),
  outW: el('out-w'), outH: el('out-h'), linkAspect: el('link-aspect'), aspectNote: el('aspect-note'),
  sizePresets: el('size-presets'), scaleHint: el('scale-hint'),
  fps: el('fps'), fpsOut: el('fps-out'), fpsHint: el('fps-hint'),
  speed: el('speed'), speedOut: el('speed-out'),
  boomerang: el('boomerang'), loopForever: el('loop-forever'),
  sharpen: el('sharpen'), sharpenOut: el('sharpen-out'),
  targetMB: el('target-mb'), fitBtn: el('fit'), fitNote: el('fit-note'),
  presets: el('presets'), qualitySummary: el('quality-summary'),
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
  preview: el('preview'), gif: el('gif'), gifMeta: el('gif-meta'),
  panelStatus: el('panel-status'), videoMeta: el('video-meta'),
  metaTitle: el('meta-title'), metaDims: el('meta-dims'),
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
function setMeter(node, ratio) {
  node.style.setProperty('--fill', `${Math.max(4, Math.min(100, ratio * 100))}%`);
  node.classList.toggle('over', ratio > 1);
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

function veil(percent, message) {
  dom.veil.hidden = false;
  dom.veilFill.style.width = `${Math.max(2, percent)}%`;
  dom.veilText.textContent = message || 'Working…';
}

const hideVeil = () => { dom.veil.hidden = true; dom.veilFill.style.width = '0%'; };

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
  frameCount(state.duration, state.fps, state.speed, state.boomerang) / state.fps;

// ── size handling ────────────────────────────────────────────────────────

/**
 * Recompute the output size from the user's request and what the crop allows.
 *
 * The requested size is remembered even when the current crop cannot supply it,
 * so enlarging the crop restores the size the user actually asked for instead of
 * leaving it stuck at whatever the smallest intermediate crop permitted.
 */
function applySize({ width, height } = {}) {
  const desiredWidth = width ?? state.desiredWidth;
  const desiredHeight = height ?? state.desiredHeight;
  const { width: maxW, height: maxH } = region();

  const outWidth = evenize(Math.min(desiredWidth, maxW));
  const outHeight = state.linkAspect
    ? evenize(outWidth / sourceAspect())
    : evenize(Math.min(desiredHeight, maxH));

  setState({ desiredWidth, desiredHeight, outWidth, outHeight });
}

// ── crop overlay ─────────────────────────────────────────────────────────

const crop = initCrop({
  canvas: dom.overlay,
  video: dom.video,
  getCrop: () => state.crop,
  setCrop: (next) => {
    setState({ crop: next });
    if (state.linkAspect) applySize({});
  },
  getLockRatio: () => state.cropAspect !== null,
  getTargetAspect: () => state.cropAspect,
});

// ── timeline ─────────────────────────────────────────────────────────────

const timeline = initTimeline({
  track: dom.track, sel: dom.sel, handleIn: dom.hIn, handleOut: dom.hOut,
  playhead: dom.playhead, scrub: dom.scrub, viewLabel: dom.viewLabel,
  getDuration: videoDuration,
  getRange: () => ({ start: state.start, end: clipEnd() }),
  setRange: (start, end) => {
    const movedIn = Math.abs(start - state.start) > 1e-6;
    setState({ start, duration: Math.max(0.1, end - start) });
    dom.video.currentTime = movedIn ? start : Math.max(0, end - 0.05);
  },
  seek: (time) => { dom.video.currentTime = time; },
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
  veil(2, 'Contacting YouTube');

  try {
    const { job_id } = await startLoad(url, sourceHeight());
    const meta = await awaitJob(job_id, veil);

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
      // Asking for more frames than the source has only duplicates them.
      fps: Math.min(state.fps, 30, Math.max(1, Math.round(meta.fps || 25))),
      result: null,
      fitNotes: null,
    });
    applySize({ width: Math.min(480, meta.width) });

    // The server may already have measured this clip in an earlier session, so
    // adopt that calibration rather than opening with an uninformed guess.
    const known = await estimateSize(renderSpec()).catch(() => null);
    if (known?.calibration) setState({ calibration: known.calibration });

    notify('');
  } catch (error) {
    notify(String(error.message || error));
  } finally {
    setState({ busy: false });
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
  if (state.loop && !dom.video.paused && dom.video.currentTime > clipEnd()) {
    dom.video.currentTime = state.start;
  }
  dom.now.textContent = formatTime(dom.video.currentTime);
  timeline.render(dom.video.currentTime);
});

dom.video.addEventListener('loadedmetadata', () => {
  dom.total.textContent = formatTime(videoDuration());
  crop.draw();
  timeline.render(dom.video.currentTime);
});

dom.video.addEventListener('play', () => { dom.play.classList.add('is-playing'); });
dom.video.addEventListener('pause', () => { dom.play.classList.remove('is-playing'); });
dom.video.addEventListener('error', () => {
  if (dom.video.src) notify('The browser could not play the downloaded file. Try loading it again at a lower quality.');
});

dom.loop.addEventListener('change', () => setState({ loop: dom.loop.checked }));

dom.markIn.addEventListener('click', () => {
  const start = Math.min(dom.video.currentTime, Math.max(0, videoDuration() - 0.1));
  setState({ start, duration: Math.max(0.1, Math.min(state.duration, videoDuration() - start)) });
});

dom.markOut.addEventListener('click', () => {
  const end = Math.max(dom.video.currentTime, state.start + 0.1);
  setState({ duration: Math.min(60, end - state.start) });
});

const stepFrame = (direction) => {
  dom.video.pause();
  const step = direction / sourceFps();
  dom.video.currentTime = Math.max(0, Math.min(videoDuration(), dom.video.currentTime + step));
};

dom.stepBack.addEventListener('click', () => stepFrame(-1));
dom.stepFwd.addEventListener('click', () => stepFrame(1));
dom.zoomFit.addEventListener('click', () => timeline.fitClip());

// Shortcuts, but never while the user is typing into a field.
document.addEventListener('keydown', (event) => {
  const target = event.target;
  if (target instanceof HTMLInputElement || target instanceof HTMLSelectElement) return;
  if (event.metaKey || event.ctrlKey || event.altKey) return;

  const actions = {
    ' ': () => dom.play.click(),
    i: () => dom.markIn.click(),
    o: () => dom.markOut.click(),
    f: () => timeline.fitClip(),
    ArrowLeft: () => (event.shiftKey ? (dom.video.currentTime -= 1) : stepFrame(-1)),
    ArrowRight: () => (event.shiftKey ? (dom.video.currentTime += 1) : stepFrame(1)),
  };

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
  setState({ crop: { x: x / width, y: y / height, w: w / width, h: h / height } });
  if (state.linkAspect) applySize({});
  crop.draw();
}

for (const input of [dom.cropX, dom.cropY, dom.cropW, dom.cropH]) {
  input.addEventListener('change', cropFromInputs);
}

dom.cropReset.addEventListener('click', () => {
  setState({ crop: null });
  if (state.linkAspect) applySize({});
  crop.draw();
});

dom.cropAspects.addEventListener('click', (event) => {
  const choice = event.target.dataset?.aspect;
  if (!choice) return;
  if (choice === 'free') {
    setState({ cropAspect: null });
    return;
  }
  const [w, h] = choice.split(':').map(Number);
  const aspect = w / h;
  setState({ cropAspect: aspect });

  // Reshape the existing crop immediately, so the choice is visible at once.
  if (state.crop && state.video) {
    const { width: vw, height: vh } = state.video;
    const pixels = cropPixels();
    // Keep the area roughly constant while adopting the new ratio.
    const side = Math.sqrt(pixels.width * pixels.height);
    let w2 = Math.min(vw, side * Math.sqrt(aspect));
    let h2 = w2 / aspect;
    if (h2 > vh) { h2 = vh; w2 = h2 * aspect; }
    const cx = pixels.x + pixels.width / 2;
    const cy = pixels.y + pixels.height / 2;
    const x = Math.max(0, Math.min(cx - w2 / 2, vw - w2));
    const y = Math.max(0, Math.min(cy - h2 / 2, vh - h2));
    setState({ crop: { x: x / vw, y: y / vh, w: w2 / vw, h: h2 / vh } });
  }
  if (state.linkAspect) applySize({});
  crop.draw();
});

// ── output size inputs ───────────────────────────────────────────────────

dom.outW.addEventListener('change', () => applySize({ width: Number(dom.outW.value) }));
dom.outH.addEventListener('change', () => applySize({ height: Number(dom.outH.value) }));

dom.linkAspect.addEventListener('change', () => {
  setState({ linkAspect: dom.linkAspect.checked });
  applySize({});
});

dom.sizePresets.addEventListener('click', (event) => {
  const width = event.target.dataset?.w;
  if (width) applySize({ width: Number(width) });
});

// ── frame rate, quality ──────────────────────────────────────────────────

dom.fps.addEventListener('input', () => setState({ fps: Number(dom.fps.value) }));
dom.speed.addEventListener('input', () => setState({ speed: Number(dom.speed.value) }));
dom.sharpen.addEventListener('input', () => setState({ sharpen: Number(dom.sharpen.value) }));
dom.boomerang.addEventListener('change', () => setState({ boomerang: dom.boomerang.checked }));
dom.loopForever.addEventListener('change', () => setState({ loopForever: dom.loopForever.checked }));
dom.targetMB.addEventListener('change', () => setState({ targetMB: Number(dom.targetMB.value) || 2 }));

dom.presets.addEventListener('click', (event) => {
  const preset = event.target.dataset?.preset;
  // Choosing a preset discards manual tweaks, which is what "preset" implies.
  if (preset) setState({ preset, overrides: {} });
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
async function runJob(begin, label) {
  if (!state.video) return null;
  const requestedSpec = JSON.stringify(renderSpec());
  notify('');
  setState({ busy: true, result: null, fitNotes: null });
  veil(2, label);

  try {
    const { job_id } = await begin();
    const result = await awaitJob(job_id, veil);
    setState({
      result,
      calibration: result.calibration ?? state.calibration,
      renderedSpec: requestedSpec,
    });
    return result;
  } catch (error) {
    notify(String(error.message || error));
    return null;
  } finally {
    setState({ busy: false });
    hideVeil();
  }
}

dom.renderBtn.addEventListener('click', () =>
  runJob(() => startRender(renderSpec()), 'Preparing'));

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

dom.fitBtn.addEventListener('click', async () => {
  const targetBytes = state.targetMB * 1024 * 1024;
  const result = await runJob(
    () => startFit(renderSpec(), targetBytes),
    `Fitting under ${state.targetMB} MB`,
  );
  if (!result) return;

  // Adopt the settings that actually hit the target, so the panel reflects reality.
  const fitted = result.spec || {};
  setState({
    fps: fitted.fps ?? state.fps,
    desiredWidth: fitted.width ?? state.desiredWidth,
    outWidth: fitted.width ?? state.outWidth,
    outHeight: fitted.height ?? state.outHeight,
    overrides: {
      ...state.overrides,
      colors: fitted.colors ?? state.overrides.colors,
      lossy: fitted.lossy ?? state.overrides.lossy,
    },
    fitNotes: {
      met: result.met,
      renders: result.renders,
      notes: result.notes || [],
    },
  });
  setState({ renderedSpec: JSON.stringify(renderSpec()) });
});

function slug(text) {
  return (text || 'clip').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 48) || 'clip';
}

// ── one render pass over the DOM ─────────────────────────────────────────

function paint() {
  const loaded = Boolean(state.video);
  const complete = Boolean(state.result);
  const resultIsStale = complete && state.renderedSpec !== JSON.stringify(renderSpec());
  dom.placeholder.hidden = loaded;
  dom.load.disabled = state.busy;
  dom.renderBtn.disabled = !loaded || state.busy;
  dom.fitBtn.disabled = !loaded || state.busy;
  dom.renderLabel.textContent = state.busy
    ? 'Rendering…'
    : resultIsStale
      ? 'Update GIF'
      : complete
        ? 'Render again'
        : 'Render GIF';

  dom.studioTitle.textContent = loaded ? 'Frame it. Trim it. Make it loop.' : 'Catch the part worth replaying.';
  dom.resultTitle.textContent = !loaded
    ? 'Load a video to unlock this.'
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

  // clip readout
  const frames = frameCount(state.duration, state.fps, state.speed, state.boomerang);
  dom.rIn.textContent = formatTime(state.start);
  dom.rOut.textContent = formatTime(clipEnd());
  dom.rLen.textContent = `${state.duration.toFixed(2)} s`;
  dom.rFrames.textContent = String(frames);
  dom.rPlays.textContent = `${playbackSeconds().toFixed(2)} s`;
  timeline.render(dom.video.currentTime);

  // crop fields
  const pixels = cropPixels();
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
    chip.classList.toggle('on', Number(chip.dataset.w) === state.desiredWidth);
  }
  const scale = regionW ? state.outWidth / regionW : 1;
  const capped = state.outWidth < state.desiredWidth;
  dom.scaleHint.textContent = loaded
    ? capped
      ? `Capped at the ${regionW} × ${regionH} region. Upscaling would only blur it.`
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
  const fpsCeiling = loaded ? Math.min(30, sourceFps()) : 30;
  dom.fps.max = fpsCeiling;
  dom.fps.value = state.fps;
  dom.fpsOut.textContent = `${state.fps} fps`;
  dom.fpsHint.textContent = loaded && state.fps >= fpsCeiling
    ? `At the source's own ${sourceFps()} fps. Asking for more would only duplicate frames.`
    : 'Higher is smoother but multiplies the file size almost linearly.';

  dom.speed.value = state.speed;
  dom.speedOut.textContent = `${state.speed.toFixed(2)}×`;
  dom.boomerang.checked = state.boomerang;
  dom.loopForever.checked = state.loopForever;
  dom.motionSummary.textContent = `${state.fps} fps · ${state.speed.toFixed(2)}×`;

  // quality
  const active = settings();
  dom.sharpen.value = state.sharpen;
  dom.sharpenOut.textContent = state.sharpen.toFixed(1);
  dom.dedupe.checked = state.dedupe;
  dom.dedupe.disabled = state.boomerang;
  dom.dedupeHint.textContent = state.boomerang
    ? 'Unavailable with ping-pong: reversing needs evenly timed frames.'
    : 'Removes repeated frames and stretches the remaining delays, so static shots shrink without changing the playback speed.';
  for (const button of dom.presets.children) {
    button.classList.toggle('on', button.dataset.preset === state.preset);
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
  const predicted = estimateBytes(state.outWidth, state.outHeight, frames, bpp, state.calibration);
  const limitBytes = state.targetMB * 1024 * 1024;
  writeSize(dom.estSize, loaded ? formatBytes(predicted) : 'Not ready');
  setMeter(dom.weightFill, loaded ? predicted / limitBytes : 0.08);
  dom.estNote.textContent = !loaded
    ? 'load a video first'
    : state.calibration
      ? 'calibrated from your last render'
      : 'rough guess, render once to calibrate';

  // target size
  dom.targetMB.value = state.targetMB;
  if (state.fitNotes) {
    const { met, renders, notes } = state.fitNotes;
    const detail = notes.length ? `: ${notes.join(', ')}` : '';
    dom.fitNote.textContent = met
      ? `Fitted in ${renders} render${renders === 1 ? '' : 's'}${detail}.`
      : `Could not reach ${state.targetMB} MB after ${renders} renders${detail}.`;
  } else {
    dom.fitNote.textContent = 'Renders repeatedly, giving up lossy compression first, '
      + 'then colours, then frame rate, and resolution only as a last resort.';
  }

  // result
  const result = state.result;
  dom.actualBlock.hidden = !result;
  dom.preview.hidden = !result;
  dom.download.hidden = !result;
  dom.copyGif.hidden = !result;
  if (result) {
    writeSize(dom.actSize, formatBytes(result.bytes));
    setMeter(dom.actualFill, result.bytes / limitBytes);
    const saved = result.bytes_before_optimize
      ? Math.round((1 - result.bytes / result.bytes_before_optimize) * 100)
      : 0;
    dom.actNote.textContent = resultIsStale
      ? 'preview uses your previous settings'
      : saved > 0
        ? `optimiser saved ${saved}%`
        : 'already minimal';
    dom.gif.src = result.gif_url;
    dom.gif.width = result.width;
    dom.gifMeta.textContent = [
      `${result.width} × ${result.height}`,
      `${result.frames} frames @ ${result.fps} fps`,
      `${result.duration.toFixed(2)} s`,
      `${result.colors} colours`,
      `rendered in ${(result.elapsed_ms / 1000).toFixed(1)} s`,
    ].join('  ·  ');
    const niceName = `${slug(state.video?.title)}-${state.start.toFixed(1)}s.gif`;
    dom.download.href = `${result.gif_url}?download=1&filename=${encodeURIComponent(niceName)}`;
    dom.download.setAttribute('download', niceName);
  }

  // source panel
  dom.videoMeta.hidden = !loaded;
  if (loaded) {
    dom.metaTitle.textContent = state.video.title;
    dom.metaDims.textContent = `${state.video.width} × ${state.video.height} · ${state.video.fps} fps · ${formatTime(state.video.duration)}`;
  }

  syncRanges();
}

subscribe(paint);

// ── boot ─────────────────────────────────────────────────────────────────

(async function boot() {
  try {
    const info = await health();
    useModel(info.model);
    setState({ presets: info.presets });
    if (!info.ok) {
      notify(`Missing required tools: ${info.missing.join(', ')}\nInstall with: ${info.hints.filter(Boolean).join(' && ')}`);
    }
  } catch (error) {
    notify(`Could not reach the backend: ${error.message}`);
  }
  paint();
})();
