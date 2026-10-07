# Buddys Treatment Builder

Small Python web app (`server.py`, front end in `index.html` / `app.js` / `styles.css`) for previewing,
trimming, GIF-making and saving permissioned videos via `yt-dlp` + bundled ffmpeg (`imageio-ffmpeg`).

## Run
- `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
- `python3 server.py` → http://127.0.0.1:8787
- Only for videos Buddy owns, has licensed, or has permission to download — don't add features that bypass site protections.

## Rules for every session (desktop, web, or phone)
- **`main` is production.** Render deploys `main` (render.yaml). Never push to `main` directly — work on a branch and open a pull request with a short, plain-English summary of what changed and how to check it. Buddy reviews and merges them, often from a phone.
- Before opening the PR, run: `python3 -m py_compile server.py` and fix anything it breaks.
- Never commit secrets or `.env` files. Production env vars live in the Render dashboard (see `render.yaml`, `sync: false` keys).
- Keep commits small and titled in plain English (what the user will notice), matching the existing git log.
