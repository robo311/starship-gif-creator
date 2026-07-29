// The clip selector: a scrub lane and two draggable handles over a zoomable track.
//
// Zooming matters more than it sounds. On a three-minute video a three-second
// clip occupies 1.4% of the track, so both handles land on the same pixel and
// precise selection is impossible. The track therefore shows a *window* of the
// video, which the wheel zooms and shift-wheel pans.
//
// Five gestures, decided by where the press lands:
//
//   scrub lane / bare track  -> drag the playhead to preview
//   playhead knob            -> the same, from wherever the playhead already is
//   selection body           -> slide the whole clip, keeping its length
//   either handle            -> trim that end
//   wheel                    -> zoom, or pan with shift

const MIN_CLIP = 0.1;
const MAX_CLIP = 60;        // matches RenderSpec's upper bound
const MIN_VIEW = 0.5;       // never zoom in tighter than half a second
const FIT_MARGIN = 0.35;    // context to keep around the clip when fitting

export function initTimeline({
  track, sel, handleIn, handleOut, playhead, scrub, viewLabel,
  getDuration, getRange, setRange, seek,
}) {
  let dragging = null;
  let grabOffset = 0;        // seconds from the clip's start to where it was grabbed
  let view = { start: 0, duration: 0 };   // duration 0 means "the whole video"

  const total = () => getDuration() || 0;
  const viewDuration = () => (view.duration > 0 ? view.duration : total());
  const viewStart = () => (view.duration > 0 ? view.start : 0);

  /** Fraction 0..1 across the track for a pointer event. */
  const fractionAt = (event) => {
    const bounds = track.getBoundingClientRect();
    return Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width));
  };

  const timeAt = (event) => viewStart() + fractionAt(event) * viewDuration();
  const fractionOf = (time) => (time - viewStart()) / (viewDuration() || 1);

  function setView(start, duration) {
    const whole = total();
    if (!whole) return;
    const clampedDuration = Math.max(MIN_VIEW, Math.min(duration, whole));
    view = {
      duration: clampedDuration >= whole ? 0 : clampedDuration,
      start: Math.max(0, Math.min(start, whole - clampedDuration)),
    };
    render();
  }

  function commit(start, end) {
    const whole = total();
    const clampedStart = Math.max(0, Math.min(start, Math.max(0, whole - MIN_CLIP)));
    const clampedEnd = Math.min(whole, Math.max(end, clampedStart + MIN_CLIP));
    setRange(clampedStart, Math.min(clampedEnd, clampedStart + MAX_CLIP));
  }

  /** Move the clip bodily, preserving its length. */
  function slideClip(time) {
    const whole = total();
    const { start, end } = getRange();
    const length = end - start;
    const next = Math.max(0, Math.min(time - grabOffset, Math.max(0, whole - length)));
    commit(next, next + length);
  }

  // ── dragging ──────────────────────────────────────────────────────────

  /** `jump` is false for the playhead's own knob, which is already where the
   *  user wants it — seeking to the press point would nudge it sideways. */
  const beginDrag = (which, jump = true) => (event) => {
    if (!total()) return;
    event.preventDefault();
    event.stopPropagation();
    dragging = which;
    if (which === 'shift') grabOffset = timeAt(event) - getRange().start;
    if (which === 'seek' && jump) seek(timeAt(event));
    track.setPointerCapture(event.pointerId);
  };

  handleIn.addEventListener('pointerdown', beginDrag('in'));
  handleOut.addEventListener('pointerdown', beginDrag('out'));
  sel.addEventListener('pointerdown', beginDrag('shift'));
  scrub.addEventListener('pointerdown', beginDrag('seek'));
  playhead.querySelector('.knob')?.addEventListener('pointerdown', beginDrag('seek', false));

  // Bare track: everything the more specific handlers above did not claim.
  track.addEventListener('pointerdown', beginDrag('seek'));

  track.addEventListener('pointermove', (event) => {
    if (!dragging) return;
    const { start, end } = getRange();
    const time = timeAt(event);
    if (dragging === 'in') commit(Math.min(time, end - MIN_CLIP), end);
    else if (dragging === 'out') commit(start, Math.max(time, start + MIN_CLIP));
    else if (dragging === 'shift') slideClip(time);
    else seek(time);
  });

  const stop = (event) => {
    if (!dragging) return;
    dragging = null;
    if (track.hasPointerCapture(event.pointerId)) track.releasePointerCapture(event.pointerId);
  };

  track.addEventListener('pointerup', stop);
  track.addEventListener('pointercancel', stop);

  // ── zoom and pan ──────────────────────────────────────────────────────

  track.addEventListener('wheel', (event) => {
    if (!total()) return;
    event.preventDefault();

    if (event.shiftKey) {
      setView(viewStart() + (event.deltaY / 400) * viewDuration(), viewDuration());
      return;
    }

    // Zoom about the cursor, so the frame under the pointer stays put.
    const anchor = timeAt(event);
    const factor = Math.exp(event.deltaY * 0.0018);
    const nextDuration = Math.max(MIN_VIEW, Math.min(viewDuration() * factor, total()));
    setView(anchor - fractionAt(event) * nextDuration, nextDuration);
  }, { passive: false });

  /** Zoom to the current selection, keeping a little context either side. */
  function fitClip() {
    const { start, end } = getRange();
    const length = Math.max(MIN_CLIP, end - start);
    const margin = length * FIT_MARGIN;
    setView(start - margin, length + margin * 2);
  }

  const showWhole = () => setView(0, total());

  // ── keyboard nudging, because dragging cannot hit an exact frame ───────

  for (const [element, which] of [[handleIn, 'in'], [handleOut, 'out']]) {
    element.addEventListener('keydown', (event) => {
      const step = event.shiftKey ? 1 : 0.1;
      const delta = event.key === 'ArrowLeft' ? -step : event.key === 'ArrowRight' ? step : 0;
      if (!delta) return;
      event.preventDefault();
      const { start, end } = getRange();
      if (which === 'in') commit(Math.min(start + delta, end - MIN_CLIP), end);
      else commit(start, Math.max(end + delta, start + MIN_CLIP));
    });
  }

  // ── drawing ───────────────────────────────────────────────────────────

  const percent = (value) => `${Math.max(-2, Math.min(102, value * 100))}%`;

  function render(currentTime = 0) {
    const whole = total();
    if (!whole) return;
    const { start, end } = getRange();

    const left = fractionOf(start);
    const right = fractionOf(end);
    sel.style.left = percent(left);
    sel.style.width = `${Math.max(0, Math.min(right, 1.02) - Math.max(left, -0.02)) * 100}%`;
    handleIn.style.left = percent(left);
    handleOut.style.left = percent(right);

    // Narrow the grips on a short selection. At full zoom a three-second clip is
    // under twenty pixels wide, and two full-width handles would cover all of it,
    // leaving no body to grab in order to move the clip.
    const selPixels = (right - left) * track.clientWidth;
    const grip = Math.max(5, Math.min(12, selPixels / 3));
    for (const element of [handleIn, handleOut]) {
      element.style.width = `${grip}px`;
      element.style.marginLeft = `${-grip / 2}px`;
    }

    playhead.style.left = percent(fractionOf(currentTime));

    if (viewLabel) {
      viewLabel.textContent = view.duration > 0
        ? `showing ${viewStart().toFixed(1)}–${(viewStart() + viewDuration()).toFixed(1)} s of ${whole.toFixed(1)} s · scroll to zoom, shift-scroll to pan`
        : `showing the whole ${whole.toFixed(1)} s · scroll to zoom`;
    }
  }

  return { render, fitClip, showWhole, MIN_CLIP, MAX_CLIP };
}
