"""Turn supported video-page URLs into locally cached, browser-playable files.

yt-dlp does the provider-specific extraction. This module supplies the stable,
filesystem-safe source IDs that let YouTube, Twitter/X, and other supported
sites share the rest of Starship's editing and rendering pipeline.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from . import media
from .models import VideoMeta

YOUTUBE_ID_PATTERNS = [
    re.compile(r"[?&]v=([A-Za-z0-9_-]{11})"),
    re.compile(r"youtu\.be/([A-Za-z0-9_-]{11})"),
    re.compile(r"/(?:shorts|embed|live|v)/([A-Za-z0-9_-]{11})"),
]
TWITTER_STATUS_PATTERN = re.compile(
    r"https?://(?:www\.|mobile\.)?(?:twitter\.com|x\.com)/"
    r"(?:i/web/|[^/?#]+/)?status(?:es)?/(\d+)",
    re.IGNORECASE,
)
INSTAGRAM_REEL_PATTERN = re.compile(
    r"https?://(?:www\.)?instagram\.com/reel/([A-Za-z0-9_-]+)",
    re.IGNORECASE,
)
TIKTOK_VIDEO_PATTERN = re.compile(
    r"https?://(?:www\.|m\.)?tiktok\.com/@[^/?#]+/video/(\d+)",
    re.IGNORECASE,
)

PROGRESS_TAG = "GYE|"
SOURCE_ID_MAX = 64


class DownloadError(RuntimeError):
    pass


def public_error_message(error: object) -> str:
    """Collapse noisy extractor diagnostics into one user-facing sentence."""
    message = str(error)
    lowered = message.lower()
    if "[instagram]" in lowered and "no video formats found" in lowered:
        return (
            "That Instagram post does not contain a downloadable video. "
            "Paste a Reel or a post that includes a video."
        )
    return message


def parse_source_id(url: str) -> str | None:
    """Extract a stable cache ID without making a network request."""
    value = url.strip()
    parsed_url = urlparse(value)
    host = (parsed_url.hostname or "").lower()
    if host == "youtu.be" or host.endswith(".youtu.be") or host == "youtube.com" or host.endswith(".youtube.com"):
        for pattern in YOUTUBE_ID_PATTERNS:
            match = pattern.search(value)
            if match:
                return match.group(1)

    match = TWITTER_STATUS_PATTERN.search(value)
    if match:
        # Twitter and X are two hostnames for the same provider. Use one prefix
        # so either form of a post URL hits the same cached download.
        return f"twitter_{match.group(1)}"

    match = INSTAGRAM_REEL_PATTERN.search(value)
    if match:
        return f"instagram_{match.group(1)}"

    match = TIKTOK_VIDEO_PATTERN.search(value)
    if match:
        return f"tiktok_{match.group(1)}"

    # Preserve the convenient legacy behaviour for bare YouTube IDs.
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        return value
    return None


def parse_video_id(url: str) -> str | None:
    """Backward-compatible name retained for callers of the YouTube module."""
    return parse_source_id(url)


def _provider(info: dict) -> str:
    extractor = str(info.get("extractor_key") or info.get("extractor") or "video")
    provider = re.sub(r"[^a-z0-9]+", "", extractor.lower())
    if provider.startswith("youtube"):
        return "youtube"
    if provider in {"twitter", "x"}:
        return "twitter"
    return provider or "video"


def _safe_source_id(provider: str, raw_id: object) -> str:
    raw = str(raw_id or "").strip()
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", raw).strip("_")
    if not safe:
        safe = hashlib.sha256(raw.encode()).hexdigest()[:16]

    # Keep existing YouTube cache names and URLs working unchanged.
    candidate = safe if provider == "youtube" else f"{provider}_{safe}"
    if len(candidate) <= SOURCE_ID_MAX:
        return candidate

    digest = hashlib.sha256(candidate.encode()).hexdigest()[:12]
    return f"{candidate[:SOURCE_ID_MAX - len(digest) - 1]}_{digest}"


def source_id(url: str, info: dict) -> str:
    """Build the cache ID for metadata returned by yt-dlp."""
    parsed = parse_source_id(url)
    provider = _provider(info)
    if parsed:
        if provider == "youtube" and not parsed.startswith(("twitter_", "instagram_", "tiktok_")):
            return parsed
        if parsed.startswith(f"{provider}_"):
            return parsed
    raw_id = info.get("id")
    if not raw_id:
        raise DownloadError("Could not determine a video id from that URL.")
    return _safe_source_id(provider, raw_id)


def validate_url(url: str) -> None:
    """Reject accidental non-web inputs before handing them to yt-dlp."""
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url.strip()):
        return
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise DownloadError("Paste a valid video-page URL.")


def format_selector(max_height: int) -> str:
    """Prefer H.264 in MP4 — VP9/AV1 will not play in every browser."""
    return (
        f"bv*[vcodec^=avc1][height<=?{max_height}]+ba[ext=m4a]/"
        f"b[vcodec^=avc1][height<=?{max_height}]/"
        f"bv*[height<=?{max_height}]+ba/"
        f"b[height<=?{max_height}]/b"
    )


def cached_video(cache_dir: Path, source_id: str) -> Path | None:
    for path in sorted(cache_dir.glob(f"{source_id}.*")):
        if path.suffix.lower() in (".mp4", ".mkv", ".webm") and path.stat().st_size > 0:
            return path
    return None


def _sidecar(cache_dir: Path, source_id: str) -> Path:
    return cache_dir / f"{source_id}.json"


def _read_sidecar(cache_dir: Path, source_id: str) -> dict:
    path = _sidecar(cache_dir, source_id)
    if path.is_file():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def fetch_metadata(url: str) -> dict:
    """Cheap metadata-only extraction, before committing to a download."""
    argv = [
        media.find_binary("yt-dlp"),
        "--dump-single-json",
        "--no-playlist",
        "--no-warnings",
        "--no-color",
        *_browser_cookie_args(),
        url,
    ]
    try:
        raw = media.run(argv, timeout=120)
    except media.CommandFailed as exc:
        if _url_provider(url):
            raise DownloadError(_friendly_access_error(url, exc.stderr)) from exc
        raise
    return json.loads(raw)


def _browser_cookie_args() -> list[str]:
    """Optional reuse of an existing local browser session through yt-dlp.

    This is deliberately opt-in: browser cookies are sensitive and Starship
    should never inspect them merely because a public URL was pasted.
    """
    browser = os.environ.get("STARSHIP_COOKIES_FROM_BROWSER", "").strip()
    if not browser:
        return []
    if len(browser) > 200 or any(char in browser for char in "\r\n\0"):
        raise DownloadError("STARSHIP_COOKIES_FROM_BROWSER contains an invalid browser profile.")
    return ["--cookies-from-browser", browser]


def _url_provider(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host == "instagram.com" or host.endswith(".instagram.com"):
        return "Instagram"
    if host == "tiktok.com" or host.endswith(".tiktok.com"):
        return "TikTok"
    return ""


def _friendly_access_error(url: str, details: str = "") -> str:
    provider = _url_provider(url)
    normalized = public_error_message(details)
    if normalized != details:
        return normalized
    if not provider:
        return "Could not read that video page. It may be private, removed, or unsupported."
    if _browser_cookie_args():
        return (
            f"{provider} could not access this video. It may be private or removed; "
            "also make sure the selected browser profile is currently logged in."
        )
    return (
        f"{provider} could not access this video. It may be private, removed, or require login. "
        "You can optionally reuse a logged-in local browser session with "
        "STARSHIP_COOKIES_FROM_BROWSER=chrome (or safari/firefox)."
    )


def _meta_from(cache_dir: Path, source_id: str, path: Path, extra: dict) -> VideoMeta:
    info = media.probe(path)
    stored = _read_sidecar(cache_dir, source_id)
    provider = extra.get("_starship_provider") or stored.get("provider")
    if not provider:
        known_prefix = next(
            (name for name in ("twitter", "instagram", "tiktok") if source_id.startswith(f"{name}_")),
            "",
        )
        if known_prefix:
            provider = known_prefix
        elif re.fullmatch(r"[A-Za-z0-9_-]{11}", source_id):
            provider = "youtube"
        else:
            provider = _provider(extra)
    return VideoMeta(
        id=source_id,
        provider=provider,
        title=extra.get("title") or stored.get("title") or source_id,
        url=extra.get("webpage_url") or stored.get("url") or "",
        duration=info.duration or float(extra.get("duration") or stored.get("duration") or 0.0),
        width=info.width,
        height=info.height,
        fps=round(info.fps, 3),
        thumbnail=extra.get("thumbnail") or stored.get("thumbnail") or "",
        stream_url=f"/api/video/{source_id}",
    )


def load(
    url: str,
    cache_dir: Path,
    max_height: int = 1080,
    on_progress=None,
) -> VideoMeta:
    """Return a cached local video for a supported URL, downloading if needed."""
    validate_url(url)
    cache_dir.mkdir(parents=True, exist_ok=True)

    def progress(percent: float, message: str) -> None:
        if on_progress:
            on_progress(percent, message)

    cached_id = parse_source_id(url)
    if cached_id:
        existing = cached_video(cache_dir, cached_id)
        if existing:
            progress(100, "Loaded from cache")
            return _meta_from(cache_dir, cached_id, existing, {})

    progress(3, "Reading video details")
    info = fetch_metadata(url)
    resolved_id = source_id(url, info)
    provider = _provider(info)
    info["_starship_provider"] = provider

    existing = cached_video(cache_dir, resolved_id)
    if existing:
        progress(100, "Loaded from cache")
        return _meta_from(cache_dir, resolved_id, existing, info)

    _sidecar(cache_dir, resolved_id).write_text(json.dumps({
        "provider": provider,
        "title": info.get("title") or resolved_id,
        "url": info.get("webpage_url") or url,
        "duration": info.get("duration") or 0,
        "thumbnail": info.get("thumbnail") or "",
    }))

    title = info.get("title") or resolved_id
    progress(5, f"Downloading “{title}”")
    _download(url, cache_dir, resolved_id, max_height, progress)

    path = cached_video(cache_dir, resolved_id)
    if not path:
        raise DownloadError("yt-dlp finished but no video file appeared in the cache.")

    progress(97, "Inspecting video")
    return _meta_from(cache_dir, resolved_id, path, info)


def _download(url: str, cache_dir: Path, source_id: str, max_height: int, progress) -> None:
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
        "-o", str(cache_dir / f"{source_id}.%(ext)s"),
        *_browser_cookie_args(),
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
            progress(5 + fraction * 0.9, f"Downloading {percent.strip()}" + (f" · {detail}" if detail else ""))
        elif line:
            tail.append(line)
            if len(tail) > 40:
                tail.pop(0)
            if "[Merger]" in line or "Merging" in line:
                progress(95, "Merging audio and video")

    if proc.wait() != 0:
        if _url_provider(url):
            raise DownloadError(_friendly_access_error(url, "\n".join(tail)))
        raise DownloadError("\n".join(tail[-12:]) or "yt-dlp failed with no output.")


def _parse_percent(text: str) -> float:
    try:
        return max(0.0, min(100.0, float(text.strip().rstrip("%")))) / 100.0
    except ValueError:
        return 0.0
