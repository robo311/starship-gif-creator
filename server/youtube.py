"""Turning a YouTube URL into a locally cached, browser-playable MP4.

The browser cannot read pixels out of YouTube's iframe player, so the video has
to exist locally before any cropping or encoding is possible. Downloads are
cached by video id, and a small JSON sidecar keeps the title so a cached video
reloads instantly without touching the network.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from . import media
from .models import VideoMeta

# youtube.com/watch?v=ID, youtu.be/ID, /shorts/ID, /embed/ID, /live/ID
ID_PATTERNS = [
    re.compile(r"[?&]v=([A-Za-z0-9_-]{11})"),
    re.compile(r"youtu\.be/([A-Za-z0-9_-]{11})"),
    re.compile(r"/(?:shorts|embed|live|v)/([A-Za-z0-9_-]{11})"),
]

PROGRESS_TAG = "GYE|"


class DownloadError(RuntimeError):
    pass


def parse_video_id(url: str) -> str | None:
    """Best-effort id extraction so cached videos reload without a network call."""
    for pattern in ID_PATTERNS:
        match = pattern.search(url)
        if match:
            return match.group(1)
    # A bare id pasted on its own.
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url.strip()):
        return url.strip()
    return None


def format_selector(max_height: int) -> str:
    """Prefer H.264 in MP4 — VP9/AV1 will not play in every browser."""
    return (
        f"bv*[vcodec^=avc1][height<=?{max_height}]+ba[ext=m4a]/"
        f"b[vcodec^=avc1][height<=?{max_height}]/"
        f"bv*[height<=?{max_height}]+ba/"
        f"b[height<=?{max_height}]/b"
    )


def cached_video(cache_dir: Path, video_id: str) -> Path | None:
    for path in sorted(cache_dir.glob(f"{video_id}.*")):
        if path.suffix.lower() in (".mp4", ".mkv", ".webm") and path.stat().st_size > 0:
            return path
    return None


def _sidecar(cache_dir: Path, video_id: str) -> Path:
    return cache_dir / f"{video_id}.json"


def _read_sidecar(cache_dir: Path, video_id: str) -> dict:
    path = _sidecar(cache_dir, video_id)
    if path.is_file():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def fetch_metadata(url: str) -> dict:
    """Cheap metadata-only extraction, before committing to a download."""
    raw = media.run([
        media.find_binary("yt-dlp"),
        "--dump-single-json",
        "--no-playlist",
        "--no-warnings",
        "--no-color",
        url,
    ], timeout=120)
    return json.loads(raw)


def _meta_from(cache_dir: Path, video_id: str, path: Path, extra: dict) -> VideoMeta:
    info = media.probe(path)
    stored = _read_sidecar(cache_dir, video_id)
    return VideoMeta(
        id=video_id,
        title=extra.get("title") or stored.get("title") or video_id,
        url=extra.get("webpage_url") or stored.get("url") or "",
        duration=info.duration or float(extra.get("duration") or stored.get("duration") or 0.0),
        width=info.width,
        height=info.height,
        fps=round(info.fps, 3),
        thumbnail=extra.get("thumbnail") or stored.get("thumbnail") or "",
        stream_url=f"/api/video/{video_id}",
    )


def load(
    url: str,
    cache_dir: Path,
    max_height: int = 1080,
    on_progress=None,
) -> VideoMeta:
    """Return a cached local MP4 for `url`, downloading it only if needed."""
    cache_dir.mkdir(parents=True, exist_ok=True)

    def progress(percent: float, message: str) -> None:
        if on_progress:
            on_progress(percent, message)

    video_id = parse_video_id(url)
    if video_id:
        existing = cached_video(cache_dir, video_id)
        if existing:
            progress(100, "Loaded from cache")
            return _meta_from(cache_dir, video_id, existing, {})

    progress(3, "Reading video details")
    info = fetch_metadata(url)
    video_id = info.get("id") or video_id
    if not video_id:
        raise DownloadError("Could not determine a video id from that URL.")

    existing = cached_video(cache_dir, video_id)
    if existing:
        progress(100, "Loaded from cache")
        return _meta_from(cache_dir, video_id, existing, info)

    _sidecar(cache_dir, video_id).write_text(json.dumps({
        "title": info.get("title") or video_id,
        "url": info.get("webpage_url") or url,
        "duration": info.get("duration") or 0,
        "thumbnail": info.get("thumbnail") or "",
    }))

    title = info.get("title") or video_id
    progress(5, f"Downloading “{title}”")
    _download(url, cache_dir, video_id, max_height, progress)

    path = cached_video(cache_dir, video_id)
    if not path:
        raise DownloadError("yt-dlp finished but no video file appeared in the cache.")

    progress(97, "Inspecting video")
    return _meta_from(cache_dir, video_id, path, info)


def _download(url: str, cache_dir: Path, video_id: str, max_height: int, progress) -> None:
    argv = [
        media.find_binary("yt-dlp"),
        "-f", format_selector(max_height),
        "--merge-output-format", "mp4",
        "--no-playlist",
        "--no-warnings",
        "--no-color",
        "--no-mtime",
        "--newline",
        "--progress-template",
        PROGRESS_TAG + "%(progress._percent_str)s|%(progress._total_bytes_estimate_str)s|%(progress._speed_str)s",
        "-o", str(cache_dir / f"{video_id}.%(ext)s"),
        url,
    ]

    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    tail: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip()
        if line.startswith(PROGRESS_TAG):
            percent, total, speed = (line[len(PROGRESS_TAG):].split("|") + ["", "", ""])[:3]
            fraction = _parse_percent(percent)
            detail = " · ".join(part.strip() for part in (total, speed) if part.strip() not in ("", "NA"))
            # Downloading occupies 5-95% of the job; probing finishes it off.
            progress(5 + fraction * 0.9, f"Downloading {percent.strip()}" + (f" · {detail}" if detail else ""))
        elif line:
            tail.append(line)
            if len(tail) > 40:
                tail.pop(0)
            if "[Merger]" in line or "Merging" in line:
                progress(95, "Merging audio and video")

    if proc.wait() != 0:
        raise DownloadError("\n".join(tail[-12:]) or "yt-dlp failed with no output.")


def _parse_percent(text: str) -> float:
    try:
        return max(0.0, min(100.0, float(text.strip().rstrip("%")))) / 100.0
    except ValueError:
        return 0.0
