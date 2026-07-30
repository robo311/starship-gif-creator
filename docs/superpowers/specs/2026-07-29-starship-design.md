# Starship — Design

**Date:** 2026-07-29
**Status:** Approved

## Problem

Make GIFs directly from a YouTube link: preview the video, draw the crop region
by hand, set output width/height and frame rate, pick the clip's start and
duration, see the approximate size *before* downloading, then download an
optimised GIF that keeps as much detail as possible at a reasonable size.

## The constraint that shapes the architecture

YouTube's embedded iframe player is cross-origin and DRM-protected. A browser
cannot read its pixels into a `<canvas>`, and it certainly cannot re-encode
them. Therefore any "GIF from a YouTube URL" tool *must*:

1. fetch the actual video stream server-side (`yt-dlp`), and
2. re-encode it server-side (`ffmpeg`).

So this is a **localhost web app** — a small Python backend with a browser UI —
not a pure client-side page. The browser previews the *locally cached* MP4,
which means the crop overlay, scrubbing and frame arithmetic all operate on a
video the page can genuinely see.

## Architecture

```
Browser (web/)                          Python backend (server/)
──────────────                          ────────────────────────
paste URL ───────── POST /api/load ───► youtube.py
                                          yt-dlp → cache/videos/<id>.mp4
                                          ffprobe → duration, w, h, fps
           ◄──── GET /api/load/<job> ──── jobs.py (download progress)

<video src="/api/video/<id>">  ◄──────── range-serving the cached MP4
  + canvas crop overlay
  + timeline in/out markers
  + live size estimate (mirrors estimate.py)

render ──────────── POST /api/render ──► gif.py
                                          pass 1  palettegen
                                          pass 2  paletteuse
                                          gifsicle -O3 --lossy
           ◄──────────────────────────── exact bytes, w, h, frames

<img src="/api/gif/<name>">   ◄───────── serve inline, then download
```

## Modules

Each has one purpose, a narrow interface, and is testable on its own.

| Module | Responsibility | Depends on |
|---|---|---|
| `server/media.py` | `ffprobe`/binary discovery, `run()` helper | ffmpeg suite |
| `server/youtube.py` | URL → cached local MP4 + `VideoMeta` | yt-dlp, media |
| `server/gif.py` | `(source, RenderSpec)` → GIF file + `RenderResult` | ffmpeg, gifsicle |
| `server/estimate.py` | `RenderSpec` (+ calibration) → estimated bytes — **pure arithmetic, no I/O** | — |
| `server/jobs.py` | in-memory progress registry for downloads/renders | — |
| `server/models.py` | `RenderSpec`, `VideoMeta`, `RenderResult` | pydantic |
| `server/app.py` | thin FastAPI routes, no business logic | all of the above |

Frontend, one concern per file:

| File | Responsibility |
|---|---|
| `web/js/api.js` | fetch wrappers, polling |
| `web/js/state.js` | single state object + subscriber notification |
| `web/js/crop.js` | canvas crop overlay: draw, move, 8 resize handles |
| `web/js/timeline.js` | in/out selection over the scrubber |
| `web/js/estimate.js` | client mirror of `estimate.py` for instant feedback |
| `web/js/main.js` | wiring and DOM updates only |

## Quality: keeping detail while keeping size down

The filter chain order matters and is deliberate:

```
fps=FPS → crop=cw:ch:cx:cy → scale=W:H:flags=lanczos
```

* **`fps` first** — dropping frames before the expensive scale saves work.
* **`crop` before `scale`** — scaling then operates only on the pixels being
  kept, so the subject uses the full output resolution instead of being
  downscaled along with discarded surroundings.
* **`lanczos`** — noticeably sharper than the default bilinear on downscale,
  which is exactly where GIF detail is normally lost.

Then a **two-pass palette**, which is the single biggest quality lever:

* Pass 1 `palettegen=max_colors=N:stats_mode=diff` — `diff` weights pixels that
  *change* between frames, so the limited palette is spent on the moving
  subject rather than on a large static background.
* Pass 2 `paletteuse=dither=D:diff_mode=rectangle` — `rectangle` restricts each
  frame's rewrite to the bounding box that actually changed. Large size win at
  zero quality cost.

Two further size levers:

* **`mpdecimate` + `-fps_mode vfr`** (optional toggle) — removes duplicate
  frames. Because GIF supports per-frame delays, timing is preserved: identical
  frames simply become one longer frame.
* **`gifsicle -O3 --lossy=L`** — cross-frame LZW optimisation plus controlled
  lossy quantisation.

### Presets

| Preset | max_colors | dither | gifsicle lossy |
|---|---|---|---|
| Max detail | 256 | `sierra2_4a` | 0 |
| Balanced *(default)* | 200 | `bayer:bayer_scale=3` | 40 |
| Small | 128 | `bayer:bayer_scale=4` | 80 |
| Tiny | 64 | `bayer:bayer_scale=5` | 120 |

Bayer dithering is patterned rather than noisy, so it compresses far better
than error-diffusion; `sierra2_4a` retains the most gradient detail but costs
size. That trade-off is exactly what the preset selects.

## Size estimate before download

Two tiers, because a truly accurate number requires actually encoding:

1. **Instant heuristic** — `bytes ≈ width × height × frames × bpp / 8`, updated
   live on every slider drag. Seeded with per-preset bits-per-pixel constants.
2. **Self-calibrating** — after each real render the actual bits-per-pixel for
   *this clip* is recorded, and later estimates for the same clip are scaled by
   it. Estimates converge to within a few percent once you've rendered once.
3. **Exact** — the render returns the real GIF, displayed inline at 1:1 with
   its exact byte size. The download button then just saves those bytes; it
   never re-encodes, so what you previewed is what you get.

## Error handling

* Missing binary (`ffmpeg`/`yt-dlp`/`gifsicle`) → startup check, clear message
  naming the missing tool and the `brew install` line.
* Invalid/unavailable URL, geo-block, private video → yt-dlp stderr surfaced
  verbatim to the UI rather than a generic failure.
* Crop rectangle clamped to frame bounds; zero-area crop rejected.
* Output dimensions forced to even numbers (some filters require it) and
  clamped to the source size to prevent upscaling blur.
* Render timeout so a pathological clip can't hang the server.

## Testing

* **Unit** — estimate arithmetic; filter-chain construction asserted against
  exact expected ffmpeg argument lists; crop clamping and aspect-ratio maths.
* **Integration** — `gif.py` run against a synthetic clip produced locally by
  `ffmpeg testsrc`, so the suite needs no network.
* **End-to-end** — the real target video
  `https://www.youtube.com/watch?v=zhHB4dZTChw`: load, crop, set parameters,
  render, then verify the resulting GIF's dimensions, frame count and size with
  `ffprobe`/`gifsicle --info`.

## Explicitly out of scope

No accounts, no persistence beyond the on-disk cache, no queue or worker
process, no deployment story. This runs on `localhost` for one person.
