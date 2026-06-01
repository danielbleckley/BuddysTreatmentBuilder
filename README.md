# Buddys Treatment Builder

A small local web app for previewing, trimming, building GIFs, and saving permissioned iSpot.tv, YouTube, Vimeo, and TikTok videos to a folder you choose.

The default save folder is `~/Desktop/BuddysTreatmentBuilder`, and you can change both the project name and save folder from Settings in the app.

Use this only for videos you own, have licensed, or have explicit permission to download.

## Run

Double-click `start.command`, or run:

```bash
python3 server.py
```

Then open:

```text
http://127.0.0.1:8787
```

## Requirements

The app uses `yt-dlp` for downloads and a bundled ffmpeg package for trimming:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install yt-dlp imageio-ffmpeg
```

Some sites may require you to be logged in, may block automated downloads, or may disallow downloading in their terms. In those cases the app will show the downloader error in Activity.
