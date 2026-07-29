# The size model, and the measurements behind it

`server/estimate.py` predicts a GIF's size before encoding it, so the UI can show
a number that responds to a dragged slider. Every constant in it was fitted to
real renders rather than guessed. This file records that data, because a fitted
constant with no recorded measurement is indistinguishable from a made-up one.

## Method

One clip — a 3 s, 350×350 crop at 10 fps of a busy music video (fast cuts, film
grain, saturated colour) — rendered repeatedly while varying one setting at a
time. Size is normalised to **bits per pixel**:

```
bpp = (bytes - HEADER_BYTES) * 8 / (width * height * frames)
```

## Lossy compression

`gifsicle --lossy`, at 200 colours and `bayer_scale=3`:

| lossy | bpp | relative |
|---:|---:|---:|
| 0 | 2.931 | 1.000 |
| 20 | 2.471 | 0.843 |
| 40 | 2.338 | 0.798 |
| 80 | 2.209 | 0.754 |
| 120 | 2.132 | 0.727 |
| 160 | 2.066 | 0.705 |
| 200 | 2.018 | 0.689 |
| 260 | 1.951 | 0.666 |
| 300 | 1.918 | 0.654 |

**This was the model's biggest error.** The original guess, `1/(1 + lossy/55)`,
predicted a 0.155 multiplier at lossy=300 — the reality is 0.654. The parameter
is far weaker than its numbers suggest, and most of what it does arrive by 40.

Fitted: `(1 + lossy/5)^-0.103`, which reproduces every row above to within 1%.

Because the range is so narrow, a calibration measured at a high lossy setting
used to over-correct wildly when applied at a low one — an 84% estimate error
observed in practice. That is what motivated this measurement.

## Palette size

At lossy=40, `bayer_scale=3`:

| colours | bpp | relative to 256 | `log2(c)/8` | fitted |
|---:|---:|---:|---:|---:|
| 32 | 1.270 | 0.512 | 0.625 | 0.528 |
| 64 | 1.655 | 0.667 | 0.750 | 0.676 |
| 96 | 1.899 | 0.766 | 0.823 | 0.771 |
| 128 | 2.066 | 0.833 | 0.875 | 0.834 |
| 160 | 2.208 | 0.890 | 0.915 | 0.885 |
| 200 | 2.338 | 0.943 | 0.955 | 0.939 |
| 256 | 2.480 | 1.000 | 1.000 | 1.000 |

A plain bits-per-index model runs consistently high: shrinking the palette also
flattens the dither pattern, so the saving beats the index arithmetic. Raising
the term to `^1.36` fits to within 3%.

## Dither mode

At 200 colours, lossy=40. The third column rescales to a 256-colour, lossy-0
base; the fourth is what the code stores, anchored at `bayer_scale=3 → 2.50`.

| dither | bpp | base | stored |
|---|---:|---:|---:|
| none | 1.938 | 2.59 | 2.07 |
| bayer 0 | 3.294 | 4.40 | 3.52 |
| bayer 1 | 2.968 | 3.96 | 3.17 |
| bayer 2 | 2.601 | 3.47 | 2.78 |
| bayer 3 | 2.338 | 3.12 | 2.50 |
| bayer 4 | 2.127 | 2.84 | 2.27 |
| bayer 5 | 2.020 | 2.70 | 2.16 |
| sierra2 | 2.442 | 3.26 | 2.61 |
| sierra2_4a | 2.803 | 3.74 | 3.00 |
| floyd–steinberg | 2.670 | 3.56 | 2.86 |

Ordered dithering does compress better than error diffusion, but by far less
than first assumed: `sierra2_4a` costs 20% more than `bayer_scale=3`, not 88%.

The absolute anchor is deliberately below this clip's measured 3.12, because this
clip is unusually busy. It is a starting point for the first estimate only;
`calibration_ratio` replaces it with a measured value after one render.

## Sharpening

At 200 colours, lossy=40, `bayer_scale=3`:

| sharpen | bpp | relative |
|---:|---:|---:|
| 0.0 | 2.338 | 1.000 |
| 0.4 | 2.388 | 1.021 |
| 0.8 | 2.428 | 1.039 |
| 1.2 | 2.515 | 1.076 |
| 2.0 | 2.566 | 1.098 |

Roughly linear at +5% per unit, so `SHARPEN_PENALTY = 0.05`.

## Frame dropping is deliberately not modelled

`mpdecimate` on this clip changed **nothing** — identical byte count, identical
frame count. Every frame of fast-cut footage differs, so there are no duplicates
to drop. On a static shot it would help substantially.

The saving is therefore a property of the *footage*, not of any setting, and no
constant can represent it. Predicting a flat 20% reduction made estimates worse
on exactly the material where people reach for the option. It is left to
`calibration_ratio`, which measures what actually happened.

## Content variation

The same settings at three points in the same video:

| start | bpp |
|---:|---:|
| 0.5 s | 2.352 |
| 100 s | 3.224 |
| 180 s | 2.714 |

A ±20% spread *within a single video*, which is why calibration is per-clip and
why the first estimate is labelled a guess in the UI. `calibration_ratio` is
clamped to [0.25, 4.0] so one unusual render cannot poison later estimates.

## Validation

The real question is not how well the constants fit the data they came from, but
whether a calibration measured at *one* setting predicts sizes at *others*. The
model was calibrated once on the anchor settings, then estimates were compared
against real renders across a deliberately wide spread, with no recalibration:

| case | estimate | actual | error |
|---|---:|---:|---:|
| lossy 0 | 1,347,765 | 1,347,064 | +0.1% |
| lossy 300 | 882,338 | 881,646 | +0.1% |
| 64 colours | 772,560 | 761,073 | +1.5% |
| 256 colours | 1,125,110 | 1,140,214 | −1.3% |
| sierra2_4a | 1,285,984 | 1,288,583 | −0.2% |
| no dither | 889,370 | 891,243 | −0.2% |
| bayer 5 | 929,957 | 928,702 | +0.1% |
| sharpen 2.0 | 1,182,156 | 1,179,657 | +0.2% |
| 20 fps | 2,144,176 | 2,124,626 | +0.9% |
| 480 px wide | 1,998,063 | 1,803,719 | +10.8% |
| tiny preset | 537,119 | 617,560 | −13.0% |
| max preset | 1,765,570 | 1,619,590 | +9.0% |

**Mean absolute error 3.1%, worst case 13.0%.** For comparison, the pre-fit model
was 84% out after a single change of setting.

The two remaining outliers are both understood. Enlarging the frame does not cost
strictly proportional bits — bigger images compress slightly better per pixel —
and the extreme presets stack several reductions at once, where a purely
multiplicative model drifts. Neither is worth another term: both stay inside the
band where the displayed figure is still a useful decision aid, and the exact
number is one render away.

## Reproducing

With the app running and a video loaded, render the same clip while varying one
setting, and compute bpp from the `bytes`, `width`, `height` and `frames` fields
that `/api/render` returns.
