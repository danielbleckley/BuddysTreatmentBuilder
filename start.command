#!/bin/zsh
cd "$(dirname "$0")"

APP_URL="http://127.0.0.1:8787/?v=20260531-render-ready"

open_app() {
  if [ -x "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ]; then
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --incognito "$APP_URL" >/dev/null 2>&1 &
    return
  fi
  /usr/bin/open -na "Google Chrome" --args --incognito "$APP_URL" >/dev/null 2>&1 && return
  /usr/bin/open "$APP_URL"
}

if [ ! -x ".venv/bin/yt-dlp" ] || ! .venv/bin/python -c "import imageio_ffmpeg" >/dev/null 2>&1; then
  python3 -m venv .venv
  .venv/bin/python -m pip install yt-dlp imageio-ffmpeg
fi

if .venv/bin/python -c "import socket; s=socket.create_connection(('127.0.0.1', 8787), 0.5); s.close()" >/dev/null 2>&1; then
  echo "Buddys Treatment Builder is already running."
  echo "Opening a fresh browser window..."
  open_app
  exit 0
fi

echo "Starting Buddys Treatment Builder..."
echo "Keep this Terminal window open while using the app."
(sleep 1; open_app) &
.venv/bin/python server.py
