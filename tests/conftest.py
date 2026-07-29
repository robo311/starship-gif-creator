"""Shared fixtures. A synthetic clip stands in for YouTube so the suite is offline."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

# app.py reads the cache location at import time, so it must be set first.
_CACHE = Path(tempfile.mkdtemp(prefix="gye-tests-"))
os.environ["GYE_CACHE_DIR"] = str(_CACHE)

SYNTHETIC_ID = "testclip01"


def _ffmpeg() -> str:
    from server import media

    return media.find_binary("ffmpeg")


@pytest.fixture(scope="session")
def cache_dir() -> Path:
    (_CACHE / "videos").mkdir(parents=True, exist_ok=True)
    (_CACHE / "gifs").mkdir(parents=True, exist_ok=True)
    return _CACHE


@pytest.fixture(scope="session")
def synthetic_video(cache_dir: Path) -> Path:
    """A 4-second 320x240 test pattern with motion, cached under a fake video id."""
    path = cache_dir / "videos" / f"{SYNTHETIC_ID}.mp4"
    if path.is_file():
        return path
    subprocess.run(
        [
            _ffmpeg(), "-hide_banner", "-v", "error",
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=4",
            "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast",
            "-y", str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


@pytest.fixture(scope="session")
def client(cache_dir: Path, synthetic_video: Path):
    from fastapi.testclient import TestClient

    from server.app import app

    with TestClient(app) as test_client:
        yield test_client
