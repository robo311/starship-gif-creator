"""HTTP surface. Routing and I/O only — the real work lives in the sibling modules."""

from __future__ import annotations

import mimetypes
import os
import re
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import estimate, fit, gif, media, youtube
from .jobs import registry
from .models import PRESETS, RenderResult, RenderSpec, VideoMeta

BASE_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = Path(os.environ.get("STARSHIP_CACHE_DIR") or BASE_DIR / "cache")
VIDEO_DIR = CACHE_DIR / "videos"
GIF_DIR = CACHE_DIR / "gifs"
WEB_DIR = BASE_DIR / "web"

REQUIRED_BINARIES = ["ffmpeg", "ffprobe", "yt-dlp", "gifsicle"]
CHUNK = 512 * 1024

app = FastAPI(
    title="Starship API",
    description="Local YouTube-to-GIF rendering and optimization API.",
    docs_url="/api/docs",
)

# Measured bits-per-pixel ratio per video, so size estimates learn from real
# renders instead of trusting a content-blind constant forever.
_calibration: dict[str, float] = {}
_probe_cache: dict[str, media.Probe] = {}


# --------------------------------------------------------------------------- #
# helpers


def _source_for(video_id: str) -> tuple[Path, media.Probe]:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", video_id):
        raise HTTPException(400, "Malformed video id.")
    path = youtube.cached_video(VIDEO_DIR, video_id)
    if path is None:
        raise HTTPException(404, "That video is not loaded. Load the URL first.")
    if video_id not in _probe_cache:
        _probe_cache[video_id] = media.probe(path)
    return path, _probe_cache[video_id]


def _spawn(target, job_id: str) -> None:
    def runner() -> None:
        try:
            target()
        except Exception as exc:  # surfaced verbatim to the UI
            registry.fail(job_id, str(exc))

    threading.Thread(target=runner, daemon=True).start()


def _ranged_file(path: Path, request: Request) -> Response:
    """Serve a file honouring HTTP Range, which `<video>` needs in order to seek."""
    size = path.stat().st_size
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    header = request.headers.get("range")

    if not header:
        return FileResponse(
            path,
            media_type=media_type,
            headers={"Accept-Ranges": "bytes", "Cache-Control": "no-cache"},
        )

    match = re.match(r"bytes=(\d*)-(\d*)", header.strip())
    if not match:
        raise HTTPException(416, "Malformed Range header.")

    raw_start, raw_end = match.groups()
    if raw_start == "":
        # A suffix range: the last N bytes.
        length = min(int(raw_end or 0), size)
        start, end = size - length, size - 1
    else:
        start = int(raw_start)
        end = int(raw_end) if raw_end else size - 1

    start = max(0, min(start, size - 1))
    end = max(start, min(end, size - 1))
    length = end - start + 1

    def stream():
        with path.open("rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                block = handle.read(min(CHUNK, remaining))
                if not block:
                    break
                remaining -= len(block)
                yield block

    return StreamingResponse(
        stream(),
        status_code=206,
        media_type=media_type,
        headers={
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Content-Length": str(length),
            "Accept-Ranges": "bytes",
            "Cache-Control": "no-cache",
        },
    )


# --------------------------------------------------------------------------- #
# API


class LoadRequest(BaseModel):
    url: str
    max_height: int = Field(default=1080, ge=144, le=2160)


class EstimateRequest(BaseModel):
    spec: RenderSpec


class FitRequest(BaseModel):
    spec: RenderSpec
    target_bytes: int = Field(ge=10_000, le=200_000_000)
    max_renders: int = Field(default=4, ge=1, le=8)


@app.get("/api/health")
def health() -> dict:
    """Startup check plus the size model's constants.

    The browser mirrors the estimate arithmetic for instant feedback while
    dragging sliders; serving the constants from here keeps a single source of
    truth, leaving only the formula itself duplicated (pinned by a parity test).
    """
    missing = media.missing_binaries(REQUIRED_BINARIES)
    return {
        "ok": not missing,
        "missing": missing,
        "hints": [media.INSTALL_HINT.get(name, "") for name in missing],
        "presets": PRESETS,
        "model": {
            "ditherBpp": estimate.DITHER_BPP,
            "bayerScaleBpp": estimate.BAYER_SCALE_BPP,
            "headerBytes": estimate.HEADER_BYTES,
            "colorExponent": estimate.COLOR_EXPONENT,
            "lossyScale": estimate.LOSSY_SCALE,
            "lossyExponent": estimate.LOSSY_EXPONENT,
            "sharpenPenalty": estimate.SHARPEN_PENALTY,
        },
    }


@app.post("/api/load")
def load(body: LoadRequest) -> dict:
    url = body.url.strip()
    if not url:
        raise HTTPException(400, "Paste a YouTube URL first.")

    job_id = registry.create("load")
    registry.update(job_id, state="running", message="Starting")

    def work() -> None:
        def on_progress(percent: float, message: str) -> None:
            registry.update(job_id, percent=round(percent, 1), message=message)

        meta: VideoMeta = youtube.load(url, VIDEO_DIR, body.max_height, on_progress)
        _probe_cache.pop(meta.id, None)
        registry.finish(job_id, meta.model_dump())

    _spawn(work, job_id)
    return {"job_id": job_id}


@app.get("/api/job/{job_id}")
def job(job_id: str) -> dict:
    found = registry.get(job_id)
    if not found:
        raise HTTPException(404, "Unknown job.")
    return found


@app.post("/api/estimate")
def estimate_size(body: EstimateRequest) -> dict:
    spec = body.spec
    calibration = _calibration.get(spec.video_id)
    predicted = estimate.for_spec(spec, calibration)
    return {
        "bytes": predicted["bytes"],
        "frames": predicted["frames"],
        "bpp": round(predicted["bpp"], 4),
        "calibration": calibration,
        "calibrated": calibration is not None,
    }


def _remember_calibration(spec: RenderSpec, result: RenderResult) -> float:
    """Record how compressible this clip turned out, for later estimates."""
    ratio = estimate.calibration_ratio(
        result.bytes,
        result.width,
        result.height,
        max(1, result.frames),
        estimate.for_spec(spec)["bpp"],
    )
    _calibration[spec.video_id] = ratio
    return ratio


@app.post("/api/render")
def render(spec: RenderSpec) -> dict:
    source, info = _source_for(spec.video_id)
    if spec.start >= info.duration > 0:
        raise HTTPException(400, "The clip starts after the end of the video.")

    job_id = registry.create("render")
    registry.update(job_id, state="running", message="Queued", percent=2)

    def work() -> None:
        def on_progress(percent: float, message: str) -> None:
            registry.update(job_id, percent=round(percent, 1), message=message)

        result: RenderResult = gif.render(
            spec, source, GIF_DIR, info.width, info.height, on_progress
        )
        payload = result.model_dump()
        payload["calibration"] = _remember_calibration(spec, result)
        registry.finish(job_id, payload)

    _spawn(work, job_id)
    return {"job_id": job_id}


@app.post("/api/fit")
def fit_to_target(body: FitRequest) -> dict:
    """Render repeatedly, shedding quality only as needed, until it fits."""
    spec = body.spec
    source, info = _source_for(spec.video_id)

    job_id = registry.create("fit")
    registry.update(job_id, state="running", message="Queued", percent=2)

    def work() -> None:
        attempts = max(1, body.max_renders)

        def render_once(candidate: RenderSpec) -> RenderResult:
            return gif.render(candidate, source, GIF_DIR, info.width, info.height)

        def on_step(index: int, message: str) -> None:
            registry.update(
                job_id,
                percent=round(min(95.0, (index / (attempts + 1)) * 100), 1),
                message=f"Attempt {index} of {attempts} · {message}",
            )

        outcome = fit.fit(spec, body.target_bytes, render_once, attempts, on_step)
        _remember_calibration(outcome.spec, outcome.result)

        payload = outcome.result.model_dump()
        payload.update({
            "calibration": _calibration.get(spec.video_id),
            "met": outcome.met,
            "renders": outcome.renders,
            "notes": outcome.notes,
            "spec": outcome.spec.model_dump(),
            "target_bytes": body.target_bytes,
        })
        registry.finish(job_id, payload)

    _spawn(work, job_id)
    return {"job_id": job_id}


@app.get("/api/video/{video_id}")
def video(video_id: str, request: Request) -> Response:
    path, _ = _source_for(video_id)
    return _ranged_file(path, request)


@app.get("/api/gif/{name}")
def get_gif(name: str, request: Request, download: int = 0, filename: str = "") -> Response:
    if not re.fullmatch(r"[A-Za-z0-9_.\-]{1,120}\.gif", name):
        raise HTTPException(400, "Malformed GIF name.")
    path = GIF_DIR / name
    if not path.is_file():
        raise HTTPException(404, "That GIF is no longer in the cache. Render it again.")

    headers = {"Cache-Control": "no-cache"}
    if download:
        # Content-Disposition overrides the anchor's `download` attribute, so the
        # caller's preferred name has to be honoured here or it is lost.
        suggested = filename if re.fullmatch(r"[A-Za-z0-9_.\-]{1,120}\.gif", filename) else name
        headers["Content-Disposition"] = f'attachment; filename="{suggested}"'
    return FileResponse(path, media_type="image/gif", headers=headers)


# --------------------------------------------------------------------------- #
# static UI

@app.middleware("http")
async def revalidate_assets(request: Request, call_next):
    """Keep the browser from serving a stale UI out of its heuristic cache.

    `StaticFiles` sends an ETag but no `Cache-Control`, which leaves the browser
    free to reuse the JS and CSS it already has. `no-cache` still allows a cheap
    304, it just forbids guessing.
    """
    response = await call_next(request)
    if request.url.path.startswith(("/js/", "/css/")):
        response.headers["Cache-Control"] = "no-cache"
    return response


if (WEB_DIR / "js").is_dir():
    app.mount("/js", StaticFiles(directory=WEB_DIR / "js"), name="js")
if (WEB_DIR / "css").is_dir():
    app.mount("/css", StaticFiles(directory=WEB_DIR / "css"), name="css")
if (WEB_DIR / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=WEB_DIR / "assets"), name="assets")


@app.get("/site.webmanifest")
def manifest() -> Response:
    path = WEB_DIR / "site.webmanifest"
    return FileResponse(path, media_type="application/manifest+json")


@app.get("/robots.txt")
def robots() -> Response:
    path = WEB_DIR / "robots.txt"
    return FileResponse(path, media_type="text/plain")


@app.get("/")
def index() -> Response:
    page = WEB_DIR / "index.html"
    if not page.is_file():
        return JSONResponse({"error": "web/index.html is missing"}, status_code=500)
    return FileResponse(page, media_type="text/html", headers={"Cache-Control": "no-cache"})
