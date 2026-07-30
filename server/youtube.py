"""Compatibility aliases for the former YouTube-only source module."""

from .sources import (
    DownloadError,
    _download,
    _meta_from,
    _parse_percent,
    _read_sidecar,
    _sidecar,
    cached_video,
    fetch_metadata,
    format_selector,
    load,
    parse_source_id,
    parse_video_id,
    source_id,
    validate_url,
)

__all__ = [
    "DownloadError",
    "cached_video",
    "fetch_metadata",
    "format_selector",
    "load",
    "parse_source_id",
    "parse_video_id",
    "source_id",
    "validate_url",
]
