#!/usr/bin/env bash
# Start the app. First run creates the virtualenv and installs dependencies.
set -euo pipefail

cd "$(dirname "$0")"

# Homebrew's bin is often absent from a GUI-launched shell's PATH.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

PORT="${PORT:-8420}"

missing=()
for tool in ffmpeg ffprobe yt-dlp gifsicle; do
  command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
done

if [ ${#missing[@]} -gt 0 ]; then
  echo "Missing required tools: ${missing[*]}" >&2
  echo "Install them with: brew install ffmpeg yt-dlp gifsicle" >&2
  exit 1
fi

if [ ! -d .venv ]; then
  echo "Creating virtualenv…"
  python3 -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip
  ./.venv/bin/pip install --quiet -r requirements.txt
fi

echo "Starship running at http://127.0.0.1:${PORT}"
exec ./.venv/bin/python -m uvicorn server.app:app --host 127.0.0.1 --port "$PORT" "$@"
