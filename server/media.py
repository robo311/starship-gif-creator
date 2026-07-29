"""Discovery of and thin wrappers around the external media binaries."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Homebrew installs here but a GUI-launched process often has a minimal PATH.
EXTRA_PATHS = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"]

INSTALL_HINT = {
    "ffmpeg": "brew install ffmpeg",
    "ffprobe": "brew install ffmpeg",
    "yt-dlp": "brew install yt-dlp",
    "gifsicle": "brew install gifsicle",
}


class MissingBinary(RuntimeError):
    def __init__(self, name: str) -> None:
        hint = INSTALL_HINT.get(name, f"install {name}")
        super().__init__(f"{name} was not found on PATH. Install it with: {hint}")
        self.name = name


class CommandFailed(RuntimeError):
    def __init__(self, argv: list[str], returncode: int, stderr: str) -> None:
        self.argv = argv
        self.returncode = returncode
        self.stderr = stderr.strip()
        super().__init__(f"{Path(argv[0]).name} exited {returncode}: {self.stderr[-2000:]}")


def find_binary(name: str) -> str:
    """Absolute path to `name`, searching PATH then the usual Homebrew spots."""
    found = shutil.which(name)
    if found:
        return found
    for directory in EXTRA_PATHS:
        candidate = Path(directory) / name
        if candidate.is_file():
            return str(candidate)
    raise MissingBinary(name)


def missing_binaries(names: list[str]) -> list[str]:
    missing = []
    for name in names:
        try:
            find_binary(name)
        except MissingBinary:
            missing.append(name)
    return missing


def run(argv: list[str], timeout: float = 600.0) -> str:
    """Run to completion, raising CommandFailed on a non-zero exit."""
    proc = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise CommandFailed(argv, proc.returncode, proc.stderr or proc.stdout)
    return proc.stdout


@dataclass(frozen=True)
class Probe:
    width: int
    height: int
    duration: float
    fps: float


def _parse_fps(rate: str | None) -> float:
    """ffprobe reports frame rates as the fraction "30000/1001"."""
    if not rate or "/" not in rate:
        return 0.0
    num, _, den = rate.partition("/")
    try:
        numerator, denominator = float(num), float(den)
    except ValueError:
        return 0.0
    return numerator / denominator if denominator else 0.0


def probe(path: Path) -> Probe:
    """Read dimensions, duration and frame rate from the first video stream."""
    raw = run([
        find_binary("ffprobe"),
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,avg_frame_rate,duration",
        "-show_entries", "format=duration",
        "-of", "json",
        str(path),
    ], timeout=60)
    data = json.loads(raw)
    stream = (data.get("streams") or [{}])[0]
    fmt = data.get("format") or {}

    duration = 0.0
    for source in (fmt.get("duration"), stream.get("duration")):
        try:
            duration = float(source)
        except (TypeError, ValueError):
            continue
        if duration > 0:
            break

    return Probe(
        width=int(stream.get("width") or 0),
        height=int(stream.get("height") or 0),
        duration=duration,
        fps=_parse_fps(stream.get("avg_frame_rate")),
    )


def count_gif_frames(path: Path) -> int:
    """Exact frame count of a rendered GIF."""
    raw = run([
        find_binary("ffprobe"),
        "-v", "error",
        "-select_streams", "v:0",
        "-count_frames",
        "-show_entries", "stream=nb_read_frames",
        "-of", "csv=p=0",
        str(path),
    ], timeout=120)
    try:
        return int(raw.strip().split(",")[0])
    except (ValueError, IndexError):
        return 0
