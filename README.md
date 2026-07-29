# YouTube → GIF

Make a GIF from a YouTube link: preview the video, draw the crop by hand, set the
size and frame rate, pick the clip, see the size before you commit, download.

```bash
./run.sh          # → http://127.0.0.1:8420
```

## Requirements

```bash
brew install ffmpeg yt-dlp gifsicle
```

Python 3.11+ is needed for the server. `run.sh` creates the virtualenv and
installs the Python dependencies on first run, and refuses to start with a clear
message if any of the three binaries is missing.

## Why there is a backend

YouTube's iframe player is cross-origin and DRM-protected, so a browser cannot
read its pixels into a canvas and certainly cannot re-encode them. Making a GIF
from a URL therefore requires fetching the real video (`yt-dlp`) and re-encoding
it (`ffmpeg`) outside the page. The browser then previews the *locally cached*
MP4, which is what makes the crop overlay and frame-accurate scrubbing possible.

Everything runs on localhost. Nothing is uploaded anywhere.

## Using it

1. Paste a link and press **Load**. The video is downloaded once and cached, so
   loading it again is instant.
2. **Drag on the frame** to draw the crop. Drag inside it to move, the edges or
   corners to resize, double-click to clear. The aspect chips (1:1, 4:5, 16:9,
   9:16) constrain the drag; the output size follows the crop's shape. Dragging
   out past the frame stops the rectangle at the edge — what you draw is what
   gets encoded, to the pixel.
3. **Pick the clip** on the track, which reads as two layers:
   - the **hatched strip along the top** scrubs — drag the white marker, or
     anywhere in the strip, to preview the video;
   - the **orange selection** below it is the clip. Drag its body to move the
     whole thing without changing its length, its ends to trim.

   Scroll to zoom and shift-scroll to pan — necessary on a long video, where a
   few seconds is a sliver of the whole timeline. **Fit clip** zooms to the
   selection.
4. Set the size, frame rate and quality, watching the **Estimated** figure.
5. **Render GIF** shows the real thing inline with its exact size, then
   **Download**.

### Keyboard

| Key | Action |
|---|---|
| `space` | play / pause |
| `←` `→` | one frame back / forward |
| `shift` + `←` `→` | one second back / forward |
| `i` / `o` | start / end the clip here |
| `f` | zoom the track to the clip |

Arrow keys on a focused track handle nudge that handle instead, by 0.1 s, or 1 s
with shift.

## How the quality is achieved

The filter order is `fps → crop → scale=lanczos → unsharp`, then a two-pass
palette, then `gifsicle`.

- **Cropping before scaling** means the region you kept uses the full output
  resolution instead of being downscaled along with the parts you discarded.
- **Lanczos** is markedly sharper than the default bilinear on downscale, which
  is exactly where GIF detail is normally lost.
- **`palettegen=stats_mode=diff`** weights pixels that change between frames, so
  a 256-colour budget is spent on the moving subject rather than on a large
  static background.
- **`paletteuse=diff_mode=rectangle`** confines each frame's rewrite to the box
  that actually changed — a large size saving at no cost in quality.
- **`gifsicle -O3`** then optimises across frames losslessly, and `--lossy`
  trades detail for size in a controlled way.

### Size options, in order of what they cost you

**Fit to a size** automates this: give it a number in MB and it renders
repeatedly, spending lossy compression first, then colours, then frame rate, and
shrinking the image only as a last resort — then tells you exactly what it traded.

Manually, the levers are:

| Lever | Effect on size | Effect on the image |
|---|---|---|
| Duration, frame rate | Nearly linear | Fewer or choppier frames |
| Width and height | Roughly quadratic | The main thing you asked for |
| Colours | Substantial | Banding in gradients |
| Dither mode | Substantial | Bayer compresses better; error diffusion holds gradients |
| `--lossy` | Up to about a third | Subtle mush in fine detail |
| Drop duplicate frames | Nothing to large | None — it only removes repeats |

**Drop duplicate frames** deserves a note: it removes frames identical to their
predecessor and lengthens the remaining frame delays to compensate, so timing is
preserved. On a static shot it is close to free size reduction. On fast-cut
footage it does nothing at all, because no two frames are alike.

## The size estimate

The **Estimated** figure updates as you drag sliders, computed in the browser so
there is no round trip per pixel. Before you have rendered anything it is
labelled a guess, because how compressible a particular clip is cannot be known
without encoding it — a still interview and a fast pan differ by several times.

After one render the estimate **calibrates** against what actually came out, and
from then on tracks reality to within a few percent as you change settings. The
arithmetic and the measurements behind its constants are in
[`docs/size-model.md`](docs/size-model.md).

## Layout

```
server/
  app.py         HTTP routes only
  youtube.py     URL → cached local MP4
  gif.py         the ffmpeg and gifsicle pipeline
  fit.py         the target-size search
  estimate.py    size arithmetic, no I/O
  media.py       binary discovery, ffprobe
  models.py      RenderSpec and friends
  jobs.py        progress registry for polling
web/
  index.html
  css/app.css
  js/{api,state,crop,rect,timeline,estimate,main}.js
```

`web/js/estimate.js` mirrors `server/estimate.py` so the readout can update
instantly. The two are pinned together by `tests/test_estimate_parity.py`, which
runs the JavaScript under Node and compares it against the Python.

`web/js/rect.js` holds the crop's rectangle maths with no DOM dependency, so
`tests/test_crop_geometry.py` can exercise it the same way — under Node, with no
browser.

## Tests

```bash
./.venv/bin/python -m pytest tests -q
```

137 tests, no network required — a synthetic `ffmpeg` test pattern stands in for
YouTube. They cover the size arithmetic, the exact ffmpeg argument lists, the
crop geometry (including that a drag off the edge truncates rather than sliding),
HTTP Range handling (which video seeking depends on), the target-size search
against an injected fake renderer, and real end-to-end renders including speed,
sharpening and ping-pong.

## Known limits

- Text captions are not offered: this ffmpeg build has no `drawtext` filter, and
  adding one would mean a freetype-enabled rebuild.
- Clips are capped at 60 seconds. GIF is a poor container beyond that.
- Long videos are downloaded in full before the preview appears; the quality
  selector next to the URL box keeps that reasonable.
