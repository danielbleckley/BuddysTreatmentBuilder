#!/usr/bin/env python3
from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


APP_DIR = Path(__file__).resolve().parent
PUBLIC_DIR = APP_DIR / "public"
CONFIG_FILE = APP_DIR / "config.json"
STAGING_DIR = APP_DIR / "preview-downloads"
DEFAULT_PROJECT_NAME = "Buddys Treatment Builder"
DEFAULT_DOWNLOAD_DIR = Path(
    os.environ.get(
        "BTB_DOWNLOAD_DIR",
        str(APP_DIR / "saved-downloads") if os.environ.get("RENDER") else str(Path.home() / "Desktop" / "BuddysTreatmentBuilder"),
    )
)
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8787"))
IS_RENDER = bool(os.environ.get("RENDER"))

SUPPORTED_HOSTS = (
    "ispot.tv",
    "instagram.com",
    "youtube.com",
    "youtu.be",
    "tiktok.com",
    "vimeo.com",
    "player.vimeo.com",
)

jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()
folder_error: str | None = None
config_lock = threading.Lock()


def find_executable(name: str) -> str | None:
    local = APP_DIR / ".venv" / "bin" / name
    if local.exists():
        return str(local)
    if name == "ffmpeg":
        binaries = sorted((APP_DIR / ".venv").glob("lib/python*/site-packages/imageio_ffmpeg/binaries/ffmpeg-*"))
        for binary in binaries:
            if binary.is_file():
                return str(binary)
        try:
            import imageio_ffmpeg

            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            pass
    return shutil.which(name)


def default_config() -> dict:
    return {
        "projectName": DEFAULT_PROJECT_NAME,
        "downloadFolder": str(DEFAULT_DOWNLOAD_DIR),
    }


def expand_folder(folder: str) -> Path:
    cleaned = folder.strip().strip("\"'")
    embedded_absolute = re.search(r"['\"](/Users/[^'\"]+)['\"]?", cleaned)
    if embedded_absolute:
        cleaned = embedded_absolute.group(1)
    return Path(cleaned).expanduser().resolve()


def sanitize_project_name(name: str) -> str:
    cleaned = re.sub(r"\s+", " ", name).strip()
    return cleaned or DEFAULT_PROJECT_NAME


def safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "", value).strip()
    return cleaned or DEFAULT_PROJECT_NAME


def safe_uploaded_filename(value: str) -> str:
    filename = Path(value).name
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "", filename).strip()
    return cleaned or "uploaded-media.mp4"


def load_config() -> dict:
    with config_lock:
        config = default_config()
        if CONFIG_FILE.exists() and not IS_RENDER:
            try:
                stored = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                if isinstance(stored, dict):
                    config.update(
                        {
                            key: stored[key]
                            for key in ("projectName", "downloadFolder")
                            if isinstance(stored.get(key), str) and stored[key].strip()
                        }
                    )
            except (OSError, json.JSONDecodeError):
                pass
        config["projectName"] = sanitize_project_name(config["projectName"])
        config["downloadFolder"] = str(expand_folder(config["downloadFolder"]))
        return config


def save_config(next_config: dict) -> dict:
    config = default_config()
    config["projectName"] = sanitize_project_name(str(next_config.get("projectName", "")))
    folder = str(next_config.get("downloadFolder", "")).strip()
    config["downloadFolder"] = str(expand_folder(str(DEFAULT_DOWNLOAD_DIR) if IS_RENDER else (folder or str(DEFAULT_DOWNLOAD_DIR))))
    if not IS_RENDER:
        with config_lock:
            CONFIG_FILE.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return config


def create_project_folder(folder_name: str) -> dict:
    if IS_RENDER:
        return load_config()
    config = load_config()
    base_folder = expand_folder(config["downloadFolder"])
    if not folder_name.strip():
        raise ValueError("Enter a folder name.")
    new_name = safe_filename(folder_name)

    new_folder = base_folder / new_name
    new_folder.mkdir(parents=True, exist_ok=True)
    config["downloadFolder"] = str(new_folder)
    config = save_config(config)
    return config


def pick_folder() -> dict:
    if IS_RENDER:
        raise ValueError("Folder selection is only available in the local desktop app.")
    script = (
        'POSIX path of (choose folder with prompt '
        '"Choose where Buddys Treatment Builder should save files")'
    )
    result = subprocess.run(
        ["osascript", "-e", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        raise ValueError("Folder selection was cancelled.")

    config = load_config()
    config["downloadFolder"] = result.stdout.strip()
    return save_config(config)


def json_response(handler: SimpleHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def is_supported_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    host = parsed.netloc.lower().removeprefix("www.")
    return any(host == allowed or host.endswith("." + allowed) for allowed in SUPPORTED_HOSTS)


def instagram_carousel_index(url: str) -> int | None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if host != "instagram.com" and not host.endswith(".instagram.com"):
        return None
    try:
        index = int(parse_qs(parsed.query).get("img_index", [""])[0])
    except ValueError:
        return None
    return index if index > 0 else None


def clean_line(line: str) -> str:
    line = re.sub(r"\x1b\[[0-9;]*m", "", line)
    return line.strip()


def parse_download_progress(line: str) -> float | None:
    match = re.search(r"\[download\]\s+([0-9.]+)%", line)
    if not match:
        return None
    try:
        return max(0.0, min(100.0, float(match.group(1))))
    except ValueError:
        return None


def update_job(job_id: str, **values) -> None:
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id].update(values)
            jobs[job_id]["updatedAt"] = time.time()


def ensure_download_dir(download_dir: Path | None = None) -> bool:
    global folder_error
    target = download_dir or expand_folder(load_config()["downloadFolder"])
    try:
        target.mkdir(parents=True, exist_ok=True)
        folder_error = None
        return True
    except OSError as exc:
        folder_error = str(exc)
        return False


def find_job(job_id: str) -> dict | None:
    with jobs_lock:
        return jobs.get(job_id)


def job_media_path(job_id: str) -> Path | None:
    job = find_job(job_id)
    if not job or not job.get("previewPath"):
        return None
    path = Path(job["previewPath"]).resolve()
    try:
        path.relative_to(STAGING_DIR.resolve())
    except ValueError:
        return None
    return path if path.exists() else None


def latest_job_file(job_id: str) -> Path | None:
    candidates = [path for path in STAGING_DIR.glob(f"{job_id} -*") if path.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def media_kind_from_name(name: str) -> str:
    mime_type = mimetypes.guess_type(name)[0] or ""
    if mime_type.startswith("audio/"):
        return "audio"
    return "video"


def media_probe(ffmpeg: str, path: Path) -> str:
    result = subprocess.run(
        [ffmpeg, "-i", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stderr


def media_kind(ffmpeg: str, path: Path) -> str:
    stderr = media_probe(ffmpeg, path)
    if "Video:" in stderr:
        return "video"
    if "Audio:" in stderr:
        return "audio"
    return media_kind_from_name(path.name)


def create_preview_job(source_name: str, preview_file: Path, message: str = "Ready to preview and trim.") -> dict:
    config = load_config()
    job_id = preview_file.name.split(" - ", 1)[0]
    ffmpeg = find_executable("ffmpeg")
    job = {
        "id": job_id,
        "url": source_name,
        "quality": "local",
        "status": "ready",
        "message": message,
        "projectName": config["projectName"],
        "folder": str(expand_folder(config["downloadFolder"])),
        "previewPath": str(preview_file),
        "mediaUrl": f"/api/media/{job_id}/{preview_file.name}",
        "savedPath": "",
        "savedUrl": "",
        "fileSize": preview_file.stat().st_size,
        "mediaKind": media_kind(ffmpeg, preview_file) if ffmpeg else media_kind_from_name(preview_file.name),
        "log": [],
        "progress": 100,
        "createdAt": time.time(),
        "updatedAt": time.time(),
    }
    with jobs_lock:
        jobs[job_id] = job
    return job


def parse_uploaded_file(content_type: str, body: bytes) -> tuple[str, bytes]:
    match = re.search(r"boundary=(.+)", content_type)
    if not match:
        raise ValueError("Upload boundary missing.")
    boundary = match.group(1).strip().strip('"').encode("utf-8")

    for part in body.split(b"--" + boundary):
        if b'Content-Disposition:' not in part or (b'name="videoFile"' not in part and b'name="mediaFile"' not in part):
            continue
        header_block, separator, file_body = part.partition(b"\r\n\r\n")
        if not separator:
            continue
        headers = header_block.decode("utf-8", errors="ignore")
        filename_match = re.search(r'filename="([^"]*)"', headers)
        filename = safe_uploaded_filename(filename_match.group(1) if filename_match else "uploaded-video.mp4")
        file_body = file_body.removesuffix(b"\r\n")
        return filename, file_body

    raise ValueError("Choose a video or audio file to upload.")


def stage_uploaded_video(filename: str, data: bytes) -> dict:
    if not data:
        raise ValueError("The selected file is empty.")
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex
    preview_file = STAGING_DIR / f"{job_id} - local - {safe_uploaded_filename(filename)}"
    preview_file.write_bytes(data)
    return create_preview_job(f"Local file: {filename}", preview_file, "Local file ready to preview and trim.")


def run_download(job_id: str, url: str, quality: str) -> None:
    config = load_config()
    project_name = config["projectName"]

    try:
        STAGING_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        update_job(
            job_id,
            status="error",
            message=f"Could not access preview folder: {exc}",
        )
        return

    ytdlp = find_executable("yt-dlp")
    if not ytdlp:
        update_job(
            job_id,
            status="error",
            message="yt-dlp is not installed. Install it, then try the download again.",
        )
        return

    ffmpeg = find_executable("ffmpeg")
    has_ffmpeg = ffmpeg is not None
    project_slug = safe_filename(project_name)
    output_template = str(
        STAGING_DIR / f"{job_id} - {project_slug} - %(extractor)s - %(title).180B [%(id)s].%(ext)s"
    )
    format_choice = (
        "bestvideo*[vcodec^=avc1][ext=mp4]+bestaudio[ext=m4a]/bestvideo*[vcodec^=avc1]+bestaudio/best[ext=mp4][vcodec^=avc1]/bestvideo*+bestaudio/bestvideo+bestaudio/best"
        if quality == "best"
        else "bestvideo*[height<=720][vcodec^=avc1][ext=mp4]+bestaudio[ext=m4a]/bestvideo*[height<=720][vcodec^=avc1]+bestaudio/best[height<=720][ext=mp4][vcodec^=avc1]/bestvideo*[height<=720]+bestaudio/best[height<=720]/best"
    )
    carousel_index = instagram_carousel_index(url)
    command = [
        ytdlp,
        "--newline",
        "--restrict-filenames",
        "--windows-filenames",
        "--format-sort",
        "res,fps,br,filesize",
        "--output",
        output_template,
        "--format",
        format_choice,
        "--merge-output-format",
        "mp4",
        url,
    ]
    if carousel_index:
        command[1:1] = ["--playlist-items", str(carousel_index)]
    else:
        command.insert(2, "--no-playlist")

    if has_ffmpeg:
        command[1:1] = ["--ffmpeg-location", ffmpeg]
    else:
        command = [part for part in command if part not in {"--merge-output-format", "mp4"}]

    update_job(job_id, status="running", message="Starting highest-quality download...", progress=0)
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        recent = []
        assert process.stdout is not None
        for raw_line in process.stdout:
            line = clean_line(raw_line)
            if not line:
                continue
            progress = parse_download_progress(line)
            recent = (recent + [line])[-20:]
            update_job(
                job_id,
                message="Downloading..." if progress is not None else line,
                log=recent,
                progress=progress if progress is not None else jobs.get(job_id, {}).get("progress", 0),
            )

        exit_code = process.wait()
        if exit_code == 0:
            preview_file = latest_job_file(job_id)
            if not preview_file:
                update_job(
                    job_id,
                    status="error",
                    message="Downloaded, but the preview file could not be found.",
                    log=recent,
                )
                return
            update_job(
                job_id,
                status="ready",
                message="Ready to preview and trim.",
                previewPath=str(preview_file),
                mediaUrl=f"/api/media/{job_id}/{preview_file.name}",
                fileSize=preview_file.stat().st_size,
                mediaKind=media_kind(ffmpeg, preview_file) if ffmpeg else media_kind_from_name(preview_file.name),
                log=recent,
                progress=100,
            )
        else:
            detail = next((line for line in reversed(recent) if line and "[download]" not in line), "")
            update_job(
                job_id,
                status="error",
                message=detail or f"Download failed with exit code {exit_code}.",
                log=recent,
            )
    except Exception as exc:
            update_job(job_id, status="error", message=str(exc), log=[str(exc)])


def media_video_codec(ffmpeg: str, path: Path) -> str:
    result = subprocess.run(
        [ffmpeg, "-i", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    match = re.search(r"Video:\s*([^,\s]+)", result.stderr)
    return match.group(1).lower() if match else ""


def needs_h264_export(ffmpeg: str, path: Path) -> bool:
    return media_video_codec(ffmpeg, path) not in {"h264"}


def copy_original(job_id: str, mute: bool = False, audio_format: str = "m4a") -> tuple[bool, str]:
    path = job_media_path(job_id)
    if not path:
        return False, "Preview file could not be found."

    config = load_config()
    download_dir = expand_folder(config["downloadFolder"])
    if not ensure_download_dir(download_dir):
        return False, f"Could not access save folder: {folder_error}"

    output_path = download_dir / path.name.split(" - ", 1)[-1]
    ffmpeg = find_executable("ffmpeg")
    if ffmpeg and media_kind(ffmpeg, path) == "audio":
        audio_format, audio_args = audio_output_settings(audio_format, None, 1)
        output_path = output_path.with_suffix(f".{audio_format}")
        command = [ffmpeg, "-y", "-i", str(path), "-vn", *audio_args, str(output_path)]
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            return False, result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "Audio conversion failed."
        return True, str(output_path)

    if mute or (ffmpeg and needs_h264_export(ffmpeg, path)):
        if not ffmpeg:
            return False, "ffmpeg is not installed. Install ffmpeg to make a compatible saved video."
        output_path = output_path.with_name(f"{output_path.stem} - compatible{' muted' if mute else ''}.mp4")
        command = [
            ffmpeg,
            "-y",
            "-i",
            str(path),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
        ]
        if mute:
            command.extend(["-an"])
        else:
            command.extend(["-c:a", "aac"])
        command.extend(["-movflags", "+faststart", str(output_path)])
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result is None or result.returncode != 0:
            return False, result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "Compatible save failed."
        return True, str(output_path)

    shutil.copy2(path, output_path)
    return True, str(output_path)


def export_gif(
    job_id: str,
    start: float,
    end: float,
    max_width: int,
    max_height: int,
    max_size_mb: float,
    fps: int,
) -> tuple[bool, str, str | None]:
    path = job_media_path(job_id)
    if not path:
        return False, "Preview file could not be found.", None
    if start < 0 or end <= start:
        return False, "Enter a start time that is before the end time.", None

    ffmpeg = find_executable("ffmpeg")
    if not ffmpeg:
        return False, "ffmpeg is not installed. Install ffmpeg to export GIFs.", None

    config = load_config()
    download_dir = expand_folder(config["downloadFolder"])
    if not ensure_download_dir(download_dir):
        return False, f"Could not access save folder: {folder_error}", None

    max_width = max(160, min(1920, int(max_width)))
    max_height = max(120, min(1920, int(max_height)))
    fps = max(6, min(30, int(fps)))
    max_size_bytes = max(0.25, min(100.0, float(max_size_mb))) * 1024 * 1024
    stem = safe_filename(path.stem.split(" - ", 1)[-1])
    output_path = download_dir / f"{stem} - gif {start:g}-{end:g}.gif"

    attempts = [
        (fps, max_width, max_height, 128),
        (max(8, int(fps * 0.8)), int(max_width * 0.9), int(max_height * 0.9), 96),
        (max(6, int(fps * 0.65)), int(max_width * 0.75), int(max_height * 0.75), 80),
        (max(6, int(fps * 0.5)), int(max_width * 0.6), int(max_height * 0.6), 64),
    ]

    last_error = ""
    for attempt_fps, attempt_width, attempt_height, colors in attempts:
        filters = (
            f"fps={attempt_fps},"
            f"scale='min(iw,{attempt_width})':'min(ih,{attempt_height})':"
            "force_original_aspect_ratio=decrease:flags=lanczos,"
            f"split[s0][s1];[s0]palettegen=max_colors={colors}[p];"
            "[s1][p]paletteuse=dither=bayer:bayer_scale=3"
        )
        command = [
            ffmpeg,
            "-y",
            "-ss",
            str(start),
            "-to",
            str(end),
            "-i",
            str(path),
            "-filter_complex",
            filters,
            "-loop",
            "0",
            str(output_path),
        ]
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result is None or result.returncode != 0:
            last_error = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "GIF export failed."
            continue
        if output_path.stat().st_size <= max_size_bytes:
            return True, str(output_path), None

    if output_path.exists():
        size_mb = output_path.stat().st_size / 1024 / 1024
        warning = f"Saved GIF, but it is {size_mb:.1f} MB, above the target {max_size_mb:g} MB."
        return True, str(output_path), warning

    return False, last_error or "GIF export failed.", None


def audio_tempo_filter(speed: float) -> str:
    filters = []
    remaining = speed
    while remaining < 0.5:
        filters.append("atempo=0.5")
        remaining /= 0.5
    while remaining > 2.0:
        filters.append("atempo=2.0")
        remaining /= 2.0
    filters.append(f"atempo={remaining:.6g}")
    return ",".join(filters)


def media_has_audio(ffmpeg: str, path: Path) -> bool:
    result = subprocess.run(
        [ffmpeg, "-i", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return "Audio:" in result.stderr


def h264_target_args(target_size_mb: float | None, duration: float, include_audio: bool) -> list[str]:
    if not target_size_mb:
        return []
    duration = max(0.1, duration)
    audio_kbps = 128 if include_audio else 0
    total_kbps = int((target_size_mb * 8192) / duration)
    video_kbps = max(250, total_kbps - audio_kbps)
    return [
        "-b:v",
        f"{video_kbps}k",
        "-maxrate",
        f"{max(video_kbps, int(video_kbps * 1.25))}k",
        "-bufsize",
        f"{max(video_kbps, int(video_kbps * 2))}k",
    ]


def audio_output_settings(audio_format: str, target_size_mb: float | None, duration: float) -> tuple[str, list[str]]:
    audio_format = audio_format.lower()
    if audio_format not in {"m4a", "mp3", "wav", "aiff"}:
        audio_format = "m4a"
    if audio_format == "mp3":
        args = ["-c:a", "libmp3lame"]
    elif audio_format == "wav":
        args = ["-c:a", "pcm_s16le"]
    elif audio_format == "aiff":
        args = ["-c:a", "pcm_s16be"]
    else:
        args = ["-c:a", "aac"]

    if target_size_mb and audio_format in {"m4a", "mp3"}:
        kbps = int((target_size_mb * 8192) / max(0.1, duration))
        kbps = max(32, min(320, kbps))
        args.extend(["-b:a", f"{kbps}k"])
    elif audio_format in {"m4a", "mp3"}:
        args.extend(["-b:a", "192k"])
    return audio_format, args


def concat_media(ffmpeg: str, segment_path: Path, output_path: Path, loop_count: int, encode_args: list[str] | None = None) -> tuple[bool, str]:
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    concat_list = STAGING_DIR / f"{uuid.uuid4().hex}-loop-list.txt"
    escaped_segment = str(segment_path).replace("'", "'\\''")
    concat_list.write_text("".join(f"file '{escaped_segment}'\n" for _ in range(loop_count)), encoding="utf-8")

    try:
        command = [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list)]
        if encode_args:
            command.extend(encode_args)
        else:
            command.extend(["-c", "copy", "-movflags", "+faststart"])
        command.append(str(output_path))
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode == 0:
            return True, str(output_path)
        return False, result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "Loop export failed."
    finally:
        concat_list.unlink(missing_ok=True)


def concat_trim_loops(ffmpeg: str, segment_path: Path, output_path: Path, loop_count: int) -> tuple[bool, str]:
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    concat_list = STAGING_DIR / f"{uuid.uuid4().hex}-loop-list.txt"
    escaped_segment = str(segment_path).replace("'", "'\\''")
    concat_list.write_text("".join(f"file '{escaped_segment}'\n" for _ in range(loop_count)), encoding="utf-8")

    try:
        command = [
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode == 0:
            return True, str(output_path)

        command = [
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode == 0:
            return True, str(output_path)
        return False, result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "Loop export failed."
    finally:
        concat_list.unlink(missing_ok=True)


def trim_audio(
    path: Path,
    download_dir: Path,
    start: float,
    end: float,
    speed: float,
    loop_count: int,
    target_size_mb: float | None,
    audio_format: str,
) -> tuple[bool, str]:
    ffmpeg = find_executable("ffmpeg")
    if not ffmpeg:
        return False, "ffmpeg is not installed. Install ffmpeg to save trimmed audio."

    stem = safe_filename(path.stem.split(" - ", 1)[-1])
    speed_label = "" if abs(speed - 1.0) < 0.001 else f" {speed:g}x"
    loop_label = "" if loop_count == 1 else f" loopx{loop_count}"
    target_label = "" if target_size_mb is None else f" target{target_size_mb:g}MB"
    segment_duration = max(0.1, (end - start) / speed)
    output_duration = segment_duration * loop_count
    audio_format, audio_args = audio_output_settings(audio_format, target_size_mb, output_duration)
    output_path = download_dir / f"{stem} - trimmed {start:g}-{end:g}{speed_label}{loop_label}{target_label}.{audio_format}"
    temp_segment = STAGING_DIR / f"{uuid.uuid4().hex}-audio-segment.m4a" if loop_count > 1 else None
    render_path = temp_segment or output_path
    render_format, render_audio_args = audio_output_settings("m4a" if temp_segment else audio_format, None if temp_segment else target_size_mb, segment_duration)
    if temp_segment:
        render_path = render_path.with_suffix(f".{render_format}")

    try:
        command = [ffmpeg, "-y", "-ss", str(start), "-to", str(end), "-i", str(path), "-vn"]
        if abs(speed - 1.0) >= 0.001:
            command.extend(["-filter:a", audio_tempo_filter(speed)])
        command.extend([*render_audio_args, str(render_path)])
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            return False, result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "Audio trim failed."
        if temp_segment:
            return concat_media(ffmpeg, render_path, output_path, loop_count, ["-vn", *audio_args])
        return True, str(output_path)
    finally:
        if temp_segment:
            temp_segment.unlink(missing_ok=True)


def trim_video(
    job_id: str,
    start: float,
    end: float,
    mute: bool = False,
    speed: float = 1.0,
    loop_count: int = 1,
    target_size_mb: float | None = None,
    audio_format: str = "m4a",
) -> tuple[bool, str]:
    path = job_media_path(job_id)
    if not path:
        return False, "Preview file could not be found."
    if start < 0 or end <= start:
        return False, "Enter a start time that is before the end time."
    speed = max(0.2, min(5.0, float(speed)))
    loop_count = max(1, min(50, int(loop_count)))
    if target_size_mb is not None and target_size_mb <= 0:
        target_size_mb = None
    if target_size_mb is not None:
        target_size_mb = max(0.5, min(5000.0, float(target_size_mb)))

    ffmpeg = find_executable("ffmpeg")
    if not ffmpeg:
        return False, "ffmpeg is not installed. Install ffmpeg to save trimmed clips."

    config = load_config()
    download_dir = expand_folder(config["downloadFolder"])
    if not ensure_download_dir(download_dir):
        return False, f"Could not access save folder: {folder_error}"
    if media_kind(ffmpeg, path) == "audio":
        return trim_audio(path, download_dir, start, end, speed, loop_count, target_size_mb, audio_format)

    stem = safe_filename(path.stem.split(" - ", 1)[-1])
    muted_label = " muted" if mute else ""
    speed_label = "" if abs(speed - 1.0) < 0.001 else f" {speed:g}x"
    loop_label = "" if loop_count == 1 else f" loopx{loop_count}"
    target_label = "" if target_size_mb is None else f" target{target_size_mb:g}MB"
    output_path = download_dir / f"{stem} - trimmed {start:g}-{end:g}{speed_label}{loop_label}{target_label}{muted_label}.mp4"
    temp_segment = STAGING_DIR / f"{uuid.uuid4().hex}-trim-segment.mp4" if loop_count > 1 else None
    render_path = temp_segment or output_path
    compatible_export_needed = needs_h264_export(ffmpeg, path)
    segment_duration = max(0.1, (end - start) / speed)
    segment_target_size_mb = (target_size_mb / loop_count) if target_size_mb and temp_segment else target_size_mb

    try:
        if abs(speed - 1.0) >= 0.001:
            has_audio = media_has_audio(ffmpeg, path)
            command = [
                ffmpeg,
                "-y",
                "-ss",
                str(start),
                "-to",
                str(end),
                "-i",
                str(path),
            ]
            if mute or not has_audio:
                command.extend(
                    [
                        "-filter:v",
                        f"setpts=PTS/{speed:.6g}",
                        "-an",
                        "-c:v",
                        "libx264",
                        "-pix_fmt",
                        "yuv420p",
                        *h264_target_args(segment_target_size_mb, segment_duration, False),
                        "-movflags",
                        "+faststart",
                        str(render_path),
                    ]
                )
            else:
                command.extend(
                    [
                        "-filter_complex",
                        f"[0:v]setpts=PTS/{speed:.6g}[v];[0:a]{audio_tempo_filter(speed)}[a]",
                        "-map",
                        "[v]",
                        "-map",
                        "[a]",
                        "-c:v",
                        "libx264",
                        *h264_target_args(segment_target_size_mb, segment_duration, True),
                        "-c:a",
                        "aac",
                        "-pix_fmt",
                        "yuv420p",
                        "-movflags",
                        "+faststart",
                        str(render_path),
                    ]
                )
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if result.returncode != 0:
                return False, result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "Speed trim failed."
            if temp_segment:
                return concat_trim_loops(ffmpeg, temp_segment, output_path, loop_count)
            return True, str(output_path)

        result = None
        if not compatible_export_needed and target_size_mb is None:
            command = [
                ffmpeg,
                "-y",
                "-ss",
                str(start),
                "-to",
                str(end),
                "-i",
                str(path),
                "-c",
                "copy",
                "-avoid_negative_ts",
                "make_zero",
                str(render_path),
            ]
            if mute:
                command.insert(-1, "-an")
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result is None or result.returncode != 0:
            command = [
                ffmpeg,
                "-y",
                "-ss",
                str(start),
                "-to",
                str(end),
                "-i",
                str(path),
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                *h264_target_args(segment_target_size_mb, segment_duration, not mute),
            ]
            if mute:
                command.extend(["-an"])
            else:
                command.extend(["-c:a", "aac"])
            command.extend(["-movflags", "+faststart", str(render_path)])
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            return False, result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "Trim failed."
        if temp_segment:
            return concat_trim_loops(ffmpeg, temp_segment, output_path, loop_count)
        return True, str(output_path)
    finally:
        if temp_segment:
            temp_segment.unlink(missing_ok=True)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PUBLIC_DIR), **kwargs)

    def log_message(self, format: str, *args) -> None:
        return

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def do_GET(self) -> None:
        if self.path == "/api/status":
            config = load_config()
            download_dir = expand_folder(config["downloadFolder"])
            ensure_download_dir(download_dir)
            payload = {
                "projectName": config["projectName"],
                "downloadFolder": str(download_dir),
                "folderReady": folder_error is None,
                "folderError": folder_error,
                "ytDlpInstalled": find_executable("yt-dlp") is not None,
                "ffmpegInstalled": find_executable("ffmpeg") is not None,
                "supportedSites": list(SUPPORTED_HOSTS),
            }
            json_response(self, 200, payload)
            return

        if self.path.startswith("/api/media/"):
            parts = self.path.split("/", 4)
            job_id = parts[3] if len(parts) > 3 else ""
            path = job_media_path(job_id)
            if not path:
                self.send_error(404)
                return
            mime_type = mimetypes.guess_type(path.name)[0] or "video/mp4"
            file_size = path.stat().st_size
            range_header = self.headers.get("Range", "")
            start = 0
            end = file_size - 1
            status = 200
            if range_header.startswith("bytes="):
                requested = range_header.removeprefix("bytes=").split(",", 1)[0]
                start_text, _, end_text = requested.partition("-")
                try:
                    if start_text:
                        start = int(start_text)
                    if end_text:
                        end = int(end_text)
                    end = min(end, file_size - 1)
                    if start > end:
                        self.send_error(416)
                        return
                    status = 206
                except ValueError:
                    self.send_error(416)
                    return

            content_length = end - start + 1
            self.send_response(status)
            self.send_header("Content-Type", mime_type)
            self.send_header("Content-Length", str(content_length))
            self.send_header("Accept-Ranges", "bytes")
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
            self.end_headers()
            try:
                with path.open("rb") as file:
                    file.seek(start)
                    remaining = content_length
                    while remaining > 0:
                        chunk = file.read(min(1024 * 1024, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        if self.path.startswith("/api/saved/"):
            parts = self.path.split("/", 3)
            job_id = parts[3] if len(parts) > 3 else ""
            job = find_job(job_id)
            path = Path(job.get("savedPath", "")).resolve() if job else None
            if not path or not path.exists():
                self.send_error(404)
                return
            mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", mime_type)
            self.send_header("Content-Length", str(path.stat().st_size))
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
            self.end_headers()
            try:
                with path.open("rb") as file:
                    shutil.copyfileobj(file, self.wfile)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        if self.path.startswith("/api/jobs"):
            with jobs_lock:
                payload = {"jobs": list(reversed(list(jobs.values())))[:20]}
            json_response(self, 200, payload)
            return

        super().do_GET()

    def do_POST(self) -> None:
        if self.path == "/api/settings":
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except json.JSONDecodeError:
                json_response(self, 400, {"error": "Invalid JSON"})
                return

            try:
                config = save_config(payload)
                download_dir = expand_folder(config["downloadFolder"])
                ensure_download_dir(download_dir)
            except OSError as exc:
                json_response(self, 400, {"error": str(exc)})
                return

            json_response(
                self,
                200,
                {
                    "projectName": config["projectName"],
                    "downloadFolder": str(download_dir),
                    "folderReady": folder_error is None,
                    "folderError": folder_error,
                },
            )
            return

        if self.path == "/api/create-folder":
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                config = create_project_folder(str(payload.get("folderName", "")))
                download_dir = expand_folder(config["downloadFolder"])
                ensure_download_dir(download_dir)
            except json.JSONDecodeError:
                json_response(self, 400, {"error": "Invalid JSON"})
                return
            except (OSError, ValueError) as exc:
                json_response(self, 400, {"error": str(exc)})
                return

            json_response(
                self,
                200,
                {
                    "projectName": config["projectName"],
                    "downloadFolder": str(download_dir),
                    "folderReady": folder_error is None,
                    "folderError": folder_error,
                },
            )
            return

        if self.path == "/api/pick-folder":
            try:
                config = pick_folder()
                download_dir = expand_folder(config["downloadFolder"])
                ensure_download_dir(download_dir)
            except (OSError, ValueError) as exc:
                json_response(self, 400, {"error": str(exc)})
                return

            json_response(
                self,
                200,
                {
                    "projectName": config["projectName"],
                    "downloadFolder": str(download_dir),
                    "folderReady": folder_error is None,
                    "folderError": folder_error,
                },
            )
            return

        if self.path == "/api/upload":
            length = int(self.headers.get("Content-Length", "0"))
            try:
                filename, data = parse_uploaded_file(
                    self.headers.get("Content-Type", ""),
                    self.rfile.read(length),
                )
                job = stage_uploaded_video(filename, data)
            except (OSError, ValueError) as exc:
                json_response(self, 400, {"error": str(exc)})
                return

            json_response(self, 202, {"job": job})
            return

        if self.path == "/api/clear-jobs":
            cleared = 0
            with jobs_lock:
                clearable_ids = [
                    job_id
                    for job_id, job in jobs.items()
                    if job.get("status") not in {"queued", "running"}
                ]
                for job_id in clearable_ids:
                    jobs.pop(job_id, None)
                    cleared += 1
            json_response(self, 200, {"cleared": cleared})
            return

        if self.path in {"/api/trim", "/api/save-original"}:
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except json.JSONDecodeError:
                json_response(self, 400, {"error": "Invalid JSON"})
                return

            job_id = str(payload.get("jobId", "")).strip()
            mute = bool(payload.get("mute", False))
            audio_format = str(payload.get("audioFormat", "m4a")).strip().lower()
            if self.path == "/api/save-original":
                ok, message = copy_original(job_id, mute, audio_format)
            else:
                try:
                    start = float(payload.get("start", 0))
                    end = float(payload.get("end", 0))
                    speed = float(payload.get("speed", 1))
                    loop_count = int(payload.get("loopCount", 1))
                    raw_target_size = str(payload.get("targetSizeMb", "")).strip()
                    target_size_mb = float(raw_target_size) if raw_target_size else None
                except (TypeError, ValueError):
                    json_response(self, 400, {"error": "Trim settings must be numbers."})
                    return
                ok, message = trim_video(job_id, start, end, mute, speed, loop_count, target_size_mb, audio_format)

            if not ok:
                json_response(self, 400, {"error": message})
                return

            saved_url = f"/api/saved/{job_id}"
            update_job(job_id, status="saved", message=f"Saved to {message}", savedPath=message, savedUrl=saved_url)
            json_response(self, 200, {"savedPath": message, "savedUrl": saved_url})
            return

        if self.path == "/api/gif":
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                job_id = str(payload.get("jobId", "")).strip()
                start = float(payload.get("start", 0))
                end = float(payload.get("end", 0))
                max_width = int(payload.get("maxWidth", 640))
                max_height = int(payload.get("maxHeight", 360))
                max_size_mb = float(payload.get("maxSizeMb", 8))
                fps = int(payload.get("fps", 12))
            except (json.JSONDecodeError, TypeError, ValueError):
                json_response(self, 400, {"error": "GIF settings are invalid."})
                return

            ok, message, warning = export_gif(job_id, start, end, max_width, max_height, max_size_mb, fps)
            if not ok:
                json_response(self, 400, {"error": message})
                return

            final_message = f"Saved GIF to {message}"
            if warning:
                final_message = f"{final_message}. {warning}"
            saved_url = f"/api/saved/{job_id}"
            update_job(job_id, status="saved", message=final_message, savedPath=message, savedUrl=saved_url)
            json_response(self, 200, {"savedPath": message, "savedUrl": saved_url, "warning": warning})
            return

        if self.path != "/api/download":
            json_response(self, 404, {"error": "Not found"})
            return

        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError:
            json_response(self, 400, {"error": "Invalid JSON"})
            return

        url = str(payload.get("url", "")).strip()
        quality = str(payload.get("quality", "best"))
        if not is_supported_url(url):
            json_response(
                self,
                400,
                {"error": "Paste a valid iSpot.tv, Instagram, YouTube, Vimeo, or TikTok link."},
            )
            return

        job_id = uuid.uuid4().hex
        config = load_config()
        job = {
            "id": job_id,
            "url": url,
            "quality": quality,
            "status": "queued",
            "message": "Queued",
            "projectName": config["projectName"],
            "folder": str(expand_folder(config["downloadFolder"])),
            "previewPath": "",
            "mediaUrl": "",
            "savedPath": "",
            "savedUrl": "",
            "fileSize": 0,
            "mediaKind": "video",
            "log": [],
            "progress": 0,
            "createdAt": time.time(),
            "updatedAt": time.time(),
        }
        with jobs_lock:
            jobs[job_id] = job

        thread = threading.Thread(target=run_download, args=(job_id, url, quality), daemon=True)
        thread.start()
        json_response(self, 202, {"job": job})


def main() -> None:
    ensure_download_dir()
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as exc:
        if getattr(exc, "errno", None) in {1, 48}:
            print(f"Buddys Treatment Builder is already running or the port is busy: http://{HOST}:{PORT}")
            print("Open that address in your browser, or close the other Terminal window running the app.")
            return
        raise
    config = load_config()
    print(f"{config['projectName']} Downloader: http://{HOST}:{PORT}")
    print(f"Saving downloads to: {expand_folder(config['downloadFolder'])}")
    server.serve_forever()


if __name__ == "__main__":
    main()
