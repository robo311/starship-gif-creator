"""Request/response shapes shared by the API and the render pipeline."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# max_colors, dither, bayer_scale, gifsicle lossy level
PRESETS: dict[str, dict] = {
    "max": {"colors": 256, "dither": "sierra2_4a", "bayer_scale": 3, "lossy": 0},
    "high": {"colors": 224, "dither": "sierra2_4a", "bayer_scale": 3, "lossy": 20},
    "balanced": {"colors": 200, "dither": "bayer", "bayer_scale": 3, "lossy": 40},
    "small": {"colors": 128, "dither": "bayer", "bayer_scale": 4, "lossy": 80},
    "tiny": {"colors": 64, "dither": "bayer", "bayer_scale": 5, "lossy": 120},
}

DITHERS = ("none", "bayer", "sierra2", "sierra2_4a", "floyd_steinberg")

# The same quality ladder for MP4, as x264 constant-quality levels: lower is
# better and bigger. 18 is visually lossless for most footage.
MP4_CRF: dict[str, int] = {"max": 18, "high": 21, "balanced": 24, "small": 28, "tiny": 32}
# "Same as the source": low enough that the re-encode is indistinguishable,
# with x264's slower search spending time instead of bits.
MP4_SOURCE_CRF = 15

MAX_SECONDS = 60
MAX_SEGMENTS = 20
MAX_TRACK_POINTS = 600


class Crop(BaseModel):
    """A rectangle in source-video pixel coordinates."""

    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)

    def clamped(self, frame_width: int, frame_height: int) -> "Crop":
        """Fit inside the frame, keeping the rectangle non-empty."""
        x = min(self.x, max(0, frame_width - 2))
        y = min(self.y, max(0, frame_height - 2))
        return Crop(
            x=x,
            y=y,
            width=max(2, min(self.width, frame_width - x)),
            height=max(2, min(self.height, frame_height - y)),
        )


class Zoom(BaseModel):
    """A steady push in or pull out across a clip, centred on the framed region."""

    direction: Literal["in", "out"] = "in"
    amount: float = Field(default=1.3, ge=1.05, le=3.0)


class TrackPoint(BaseModel):
    """Where a followed target was: source seconds, and the crop's centre in 0..1."""

    t: float = Field(ge=0)
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class Segment(BaseModel):
    """One stretch of the source, in seconds. A sequence plays them in list order.

    Each part frames its own region of the source (no crop means the whole
    frame), may zoom, and may move its crop along a followed target's path.
    """

    start: float = Field(ge=0)
    duration: float = Field(gt=0, le=MAX_SECONDS)
    crop: Crop | None = None
    zoom: Zoom | None = None
    track: list[TrackPoint] = Field(default_factory=list, max_length=MAX_TRACK_POINTS)


class VideoMeta(BaseModel):
    id: str
    provider: str = ""
    title: str = ""
    url: str = ""
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    thumbnail: str = ""
    stream_url: str = ""


class RenderSpec(BaseModel):
    """Everything needed to turn a cached video into one GIF."""

    video_id: str
    format: Literal["gif", "mp4"] = "gif"
    start: float = Field(default=0.0, ge=0)
    duration: float = Field(default=3.0, gt=0, le=MAX_SECONDS)
    fps: int = Field(default=15, ge=1, le=60)
    # Up to 4K, so a 1440p (or larger) source can be exported at full size.
    width: int = Field(default=480, ge=16, le=3840)
    height: int = Field(default=270, ge=16, le=3840)
    crop: Crop | None = None
    zoom: Zoom | None = None
    track: list[TrackPoint] = Field(default_factory=list, max_length=MAX_TRACK_POINTS)
    preset: str = "balanced"
    dedupe: bool = False
    speed: float = Field(default=1.0, ge=0.25, le=4.0)
    boomerang: bool = False
    sharpen: float = Field(default=0.0, ge=0.0, le=2.0)
    loop_forever: bool = True
    # Advanced overrides; fall back to the preset when omitted.
    colors: int | None = Field(default=None, ge=2, le=256)
    dither: str | None = None
    bayer_scale: int | None = Field(default=None, ge=0, le=5)
    lossy: int | None = Field(default=None, ge=0, le=300)
    # MP4 only: a fixed video bitrate, used to hit a file size; quality-based otherwise.
    bitrate_kbps: int | None = Field(default=None, ge=50, le=100_000)
    # MP4 only: "source" encodes near-lossless instead of following the preset.
    mp4_quality: Literal["ladder", "source"] = "ladder"
    # MP4 only: an explicit x264 quality level, chosen in the page's MP4 group;
    # when absent, the GIF preset ladder is mapped onto MP4_CRF instead.
    mp4_crf: int | None = Field(default=None, ge=0, le=51)
    # Filled in by the server from the source file, never by the page: the
    # source's true frame rate, which "source" quality keeps exactly.
    source_fps: float | None = Field(default=None, gt=0, le=1000)
    # Several parts of the source joined in this order. Empty means the single
    # clip described by `start` and `duration`.
    segments: list[Segment] = Field(default_factory=list, max_length=MAX_SEGMENTS)

    @model_validator(mode="after")
    def _sequence_sets_the_clip(self) -> "RenderSpec":
        """With a sequence, `start` and `duration` describe it as a whole.

        Everything downstream that only reads `duration` — the size estimate,
        the fit search — then sees the real length of the joined GIF.
        """
        if self.segments:
            total = sum(segment.duration for segment in self.segments)
            if total > MAX_SECONDS:
                raise ValueError(f"the parts add up to {total:.1f} s; the limit is {MAX_SECONDS} s")
            self.start = self.segments[0].start
            self.duration = round(total, 3)
            if len(self.segments) == 1:
                # A lone part is an ordinary clip, framed the way that part is.
                only = self.segments[0]
                self.crop, self.zoom, self.track = only.crop, only.zoom, only.track
        return self

    def clips(self) -> list[Segment]:
        """The source ranges to encode, in playback order."""
        return self.segments or [Segment(
            start=self.start, duration=self.duration,
            crop=self.crop, zoom=self.zoom, track=self.track,
        )]

    @field_validator("preset")
    @classmethod
    def _known_preset(cls, value: str) -> str:
        if value not in PRESETS:
            raise ValueError(f"unknown preset {value!r}; expected one of {sorted(PRESETS)}")
        return value

    @field_validator("dither")
    @classmethod
    def _known_dither(cls, value: str | None) -> str | None:
        if value is not None and value not in DITHERS:
            raise ValueError(f"unknown dither {value!r}; expected one of {list(DITHERS)}")
        return value

    def resolved(self) -> dict:
        """Preset values with any advanced override applied."""
        base = dict(PRESETS[self.preset])
        if self.colors is not None:
            base["colors"] = self.colors
        if self.dither is not None:
            base["dither"] = self.dither
        if self.bayer_scale is not None:
            base["bayer_scale"] = self.bayer_scale
        if self.lossy is not None:
            base["lossy"] = self.lossy
        return base

    def even_size(self) -> tuple[int, int]:
        """GIF is fine with odd sizes but several filters are not."""
        return self.width - (self.width % 2), self.height - (self.height % 2)

    def dedupe_active(self) -> bool:
        """Frame dropping is skipped for boomerangs.

        `reverse` needs an evenly-timed stream to renumber timestamps against;
        `mpdecimate` deliberately produces an unevenly-timed one. Asked for both,
        the boomerang wins, because it is the more visible of the two effects.
        """
        return self.dedupe and not self.boomerang


class RenderResult(BaseModel):
    name: str
    format: str = "gif"
    gif_url: str
    bytes: int
    width: int
    height: int
    frames: int
    fps: int
    duration: float
    elapsed_ms: int
    colors: int
    bytes_before_optimize: int
    measured_bpp: float
    commands: list[str] = []
