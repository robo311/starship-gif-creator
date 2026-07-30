"""Request/response shapes shared by the API and the render pipeline."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

# max_colors, dither, bayer_scale, gifsicle lossy level
PRESETS: dict[str, dict] = {
    "max": {"colors": 256, "dither": "sierra2_4a", "bayer_scale": 3, "lossy": 0},
    "balanced": {"colors": 200, "dither": "bayer", "bayer_scale": 3, "lossy": 40},
    "small": {"colors": 128, "dither": "bayer", "bayer_scale": 4, "lossy": 80},
    "tiny": {"colors": 64, "dither": "bayer", "bayer_scale": 5, "lossy": 120},
}

DITHERS = ("none", "bayer", "sierra2", "sierra2_4a", "floyd_steinberg")


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
    start: float = Field(default=0.0, ge=0)
    duration: float = Field(default=3.0, gt=0, le=60)
    fps: int = Field(default=15, ge=1, le=50)
    width: int = Field(default=480, ge=16, le=1920)
    height: int = Field(default=270, ge=16, le=1920)
    crop: Crop | None = None
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
