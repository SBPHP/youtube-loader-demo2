from __future__ import annotations

import base64
import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yt_dlp
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "youtube-loader.sqlite3"
DOWNLOAD_DIR = Path(os.getenv("YOUTUBE_LOADER_DOWNLOAD_DIR", BASE_DIR / "downloads")).resolve()
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

COOKIE_FILE = DATA_DIR / "youtube-cookies.txt"


def normalize_netscape_cookie_file(source: Path) -> Path | None:
    """Create a yt-dlp compatible working copy without modifying the Render secret."""
    try:
        text = source.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None

    raw_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    output: list[str] = []

    if not raw_lines or not raw_lines[0].strip().startswith(("# Netscape HTTP Cookie File", "# HTTP Cookie File")):
        output.append("# Netscape HTTP Cookie File")

    for line in raw_lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            output.append(line)
            continue

        parts = line.split("\t")
        if len(parts) != 7:
            continue

        domain, include_subdomains, path, secure, expires, name, value = parts

        if include_subdomains.upper() == "TRUE" and domain in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
            domain = ".youtube.com"

        include_subdomains = "TRUE" if include_subdomains.upper() == "TRUE" else "FALSE"
        secure = "TRUE" if secure.upper() == "TRUE" else "FALSE"

        output.append("\t".join([domain, include_subdomains, path or "/", secure, expires or "0", name, value]))

    if len([line for line in output if line and not line.startswith("#")]) == 0:
        return None

    normalized = DATA_DIR / "youtube-cookies-normalized.txt"
    normalized.write_text("\n".join(output) + "\n", encoding="utf-8")
    os.chmod(normalized, 0o600)
    return normalized


def prepare_cookie_file() -> Path | None:
    """Resolve cookies and return a normalized yt-dlp compatible working copy."""
    encoded = os.getenv("YOUTUBE_COOKIES_BASE64", "").strip()
    plain = os.getenv("YOUTUBE_COOKIES_TEXT", "")
    external_path = os.getenv("YOUTUBE_COOKIES_FILE", "").strip()

    candidates: list[Path] = [
        Path("/etc/secrets/youtube-cookies.txt"),
        Path("/etc/secrets/cookies.txt"),
    ]

    try:
        for path in candidates:
            if path.is_file() and path.stat().st_size > 0:
                return normalize_netscape_cookie_file(path)

        if encoded:
            raw = DATA_DIR / "youtube-cookies-from-base64.txt"
            raw.write_bytes(base64.b64decode(encoded, validate=True))
            os.chmod(raw, 0o600)
            return normalize_netscape_cookie_file(raw)

        if plain.strip():
            raw = DATA_DIR / "youtube-cookies-from-text.txt"
            raw.write_text(plain, encoding="utf-8")
            os.chmod(raw, 0o600)
            return normalize_netscape_cookie_file(raw)

        if external_path:
            path = Path(external_path).expanduser().resolve()
            if path.is_file() and path.stat().st_size > 0:
                return normalize_netscape_cookie_file(path)
    except Exception:
        return None

    return None


def cookie_status() -> dict[str, Any]:
    path = prepare_cookie_file()
    source = "none"

    if Path("/etc/secrets/youtube-cookies.txt").is_file() or Path("/etc/secrets/cookies.txt").is_file():
        source = "render-secret-file"
    elif os.getenv("YOUTUBE_COOKIES_BASE64", "").strip():
        source = "base64-secret"
    elif os.getenv("YOUTUBE_COOKIES_TEXT", "").strip():
        source = "text-secret"
    elif os.getenv("YOUTUBE_COOKIES_FILE", "").strip():
        source = "file"

    rows = 0
    if path and path.is_file():
        try:
            rows = len([
                line for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()
                if line and not line.startswith("#")
            ])
        except Exception:
            rows = 0

    return {
        "ok": bool(path and path.is_file() and path.stat().st_size > 0 and rows > 0),
        "configured": source != "none",
        "source": source,
        "format_ok": bool(path and rows > 0),
        "normalized_rows": rows,
    }


def youtube_user_agent() -> str | None:
    value = os.getenv("YOUTUBE_USER_AGENT", "").strip()
    return value or None


def user_agent_status() -> dict[str, Any]:
    value = youtube_user_agent()
    return {
        "ok": bool(value and value.startswith("Mozilla/5.0")),
        "configured": bool(value),
        "preview": (value[:54] + "…") if value and len(value) > 55 else value,
    }


def youtube_opts(options: dict[str, Any] | None = None) -> dict[str, Any]:
    opts = dict(options or {})
    cookie_file = prepare_cookie_file()
    if cookie_file:
        opts["cookiefile"] = str(cookie_file)

    user_agent = youtube_user_agent()
    if user_agent:
        opts["http_headers"] = {
            **(opts.get("http_headers") or {}),
            "User-Agent": user_agent,
        }
    return opts


app = FastAPI(title="YouTube Loader", version="0.6.4")

jobs: dict[str, dict[str, Any]] = {}
jobs_lock = threading.Lock()
cancel_events: dict[str, threading.Event] = {}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with db_connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                status TEXT NOT NULL,
                title TEXT,
                mode TEXT,
                container TEXT,
                source_url TEXT,
                is_playlist INTEGER NOT NULL DEFAULT 0,
                playlist_count INTEGER,
                percent REAL NOT NULL DEFAULT 0,
                filename TEXT,
                file_size INTEGER,
                error TEXT,
                request_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )


init_db()


def writable_check(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".youtube-loader-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def command_version(command: str, args: list[str] | None = None) -> str | None:
    executable = shutil.which(command)
    if not executable:
        return None
    try:
        result = subprocess.run(
            [executable, *(args or ["--version"])],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        text = (result.stdout or result.stderr or "").strip().splitlines()
        return text[0][:180] if text else "installed"
    except Exception:
        return "installed"


def runtime_snapshot() -> dict[str, Any]:
    usage = shutil.disk_usage(DOWNLOAD_DIR)
    ffmpeg_version = command_version("ffmpeg", ["-version"])
    return {
        "ok": True,
        "version": app.version,
        "checks": {
            "yt_dlp": {
                "ok": True,
                "version": getattr(yt_dlp.version, "__version__", "unknown"),
            },
            "ffmpeg": {
                "ok": bool(shutil.which("ffmpeg")),
                "version": ffmpeg_version,
            },
            "downloads_writable": {
                "ok": writable_check(DOWNLOAD_DIR),
                "path": str(DOWNLOAD_DIR),
            },
            "database_writable": {
                "ok": writable_check(DATA_DIR),
                "path": str(DATA_DIR),
            },
            "youtube_auth": cookie_status(),
            "browser_fingerprint": user_agent_status(),
        },
        "storage": {
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
            "total_text": format_bytes(usage.total),
            "used_text": format_bytes(usage.used),
            "free_text": format_bytes(usage.free),
        },
        "active_jobs": len([
            job for job in jobs.values()
            if job.get("status") not in {"finished", "finished_with_errors", "failed", "cancelled"}
        ]),
    }



def safe_filename(name: str) -> str:
    name = re.sub(r"[\\/:*?\"<>|]", "_", name).strip()
    return name[:180] or "download"


def format_bytes(value: int | float | None) -> str | None:
    if value is None:
        return None
    value = float(value)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return None


def format_seconds(value: float | int | None) -> str | None:
    if value is None:
        return None
    seconds = max(0, int(value))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def compact_entry(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": entry.get("id"),
        "title": entry.get("title") or "Unbekannter Titel",
        "url": entry.get("webpage_url") or entry.get("url"),
        "thumbnail": entry.get("thumbnail") or (entry.get("thumbnails") or [{}])[-1].get("url"),
        "duration": entry.get("duration"),
        "duration_text": format_seconds(entry.get("duration")),
        "channel": entry.get("channel") or entry.get("uploader"),
    }


class InfoRequest(BaseModel):
    url: str = Field(min_length=8)


class DownloadRequest(BaseModel):
    url: str = Field(min_length=8)
    mode: str = Field(pattern="^(video|audio)$")
    container: str = "mp4"
    quality: str = "best"
    audio_bitrate: int = Field(default=320, ge=32, le=512)
    filename: str | None = None
    embed_thumbnail: bool = False
    write_metadata: bool = True
    is_playlist: bool = False
    title: str | None = None
    selected_entries: list[int] | None = None


class SettingsRequest(BaseModel):
    default_mode: str = Field(default="video", pattern="^(video|audio)$")
    default_video_container: str = Field(default="mp4", pattern="^(mp4|mkv|webm)$")
    default_audio_container: str = Field(default="mp3", pattern="^(mp3|m4a|aac|opus|flac|wav)$")
    default_video_quality: str = "best"
    default_audio_bitrate: int = Field(default=320, ge=32, le=512)
    embed_thumbnail: bool = False
    write_metadata: bool = True


DEFAULT_SETTINGS = SettingsRequest().model_dump()


def summarize_formats(formats: list[dict[str, Any]]) -> dict[str, Any]:
    heights = sorted(
        {int(f["height"]) for f in formats if f.get("height") and f.get("vcodec") not in (None, "none")},
        reverse=True,
    )
    audio_bitrates = sorted(
        {int(round(float(f["abr"]))) for f in formats if f.get("abr") and f.get("acodec") not in (None, "none")},
        reverse=True,
    )
    return {"video_heights": heights, "audio_bitrates": audio_bitrates}


def public_info(raw: dict[str, Any]) -> dict[str, Any]:
    is_playlist = raw.get("_type") == "playlist" or bool(raw.get("entries"))
    if is_playlist:
        entries = [compact_entry(e) for e in (raw.get("entries") or []) if e]
        return {
            "id": raw.get("id"),
            "title": raw.get("title") or "Playlist",
            "channel": raw.get("channel") or raw.get("uploader"),
            "thumbnail": raw.get("thumbnail") or (entries[0].get("thumbnail") if entries else None),
            "webpage_url": raw.get("webpage_url"),
            "is_playlist": True,
            "playlist_count": raw.get("playlist_count") or len(entries),
            "entries": entries,
            "video_heights": [],
            "audio_bitrates": [],
        }

    formats = raw.get("formats") or []
    fmt = summarize_formats(formats)
    return {
        "id": raw.get("id"),
        "title": raw.get("title"),
        "channel": raw.get("channel") or raw.get("uploader"),
        "channel_url": raw.get("channel_url") or raw.get("uploader_url"),
        "thumbnail": raw.get("thumbnail"),
        "duration": raw.get("duration"),
        "duration_text": format_seconds(raw.get("duration")),
        "upload_date": raw.get("upload_date"),
        "view_count": raw.get("view_count"),
        "like_count": raw.get("like_count"),
        "webpage_url": raw.get("webpage_url"),
        "video_heights": fmt["video_heights"],
        "audio_bitrates": fmt["audio_bitrates"],
        "is_playlist": False,
        "playlist_count": None,
        "entries": [],
    }


def persist_job(job: dict[str, Any]) -> None:
    request = job.get("request") or {}
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO jobs (
                id, created_at, updated_at, status, title, mode, container, source_url,
                is_playlist, playlist_count, percent, filename, file_size, error, request_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                updated_at=excluded.updated_at,
                status=excluded.status,
                title=excluded.title,
                mode=excluded.mode,
                container=excluded.container,
                source_url=excluded.source_url,
                is_playlist=excluded.is_playlist,
                playlist_count=excluded.playlist_count,
                percent=excluded.percent,
                filename=excluded.filename,
                file_size=excluded.file_size,
                error=excluded.error,
                request_json=excluded.request_json
            """,
            (
                job["id"], job["created_at"], job.get("updated_at", utc_now()), job["status"],
                job.get("title"), request.get("mode"), request.get("container"), request.get("url"),
                int(bool(request.get("is_playlist"))), job.get("playlist_count"), float(job.get("percent") or 0),
                job.get("filename"), job.get("file_size"), job.get("error"), json.dumps(request, ensure_ascii=False),
            ),
        )


def update_job(job_id: str, **values: Any) -> None:
    snapshot: dict[str, Any] | None = None
    with jobs_lock:
        if job_id in jobs:
            values["updated_at"] = utc_now()
            jobs[job_id].update(values)
            snapshot = dict(jobs[job_id])
    if snapshot:
        persist_job(snapshot)


def historical_job(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "status": row["status"],
        "title": row["title"],
        "mode": row["mode"],
        "container": row["container"],
        "source_url": row["source_url"],
        "is_playlist": bool(row["is_playlist"]),
        "playlist_count": row["playlist_count"],
        "percent": row["percent"],
        "filename": row["filename"],
        "file_size": row["file_size"],
        "file_size_text": format_bytes(row["file_size"]),
        "error": row["error"],
        "download_url": f"/api/youtube/history/files/{row['id']}" if row["filename"] else None,
    }


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "version": app.version,
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "download_dir": str(DOWNLOAD_DIR),
        "database": str(DB_PATH),
    }



@app.get("/api/runtime")
def runtime_status() -> dict[str, Any]:
    return runtime_snapshot()


@app.post("/api/runtime/self-test")
def runtime_self_test() -> dict[str, Any]:
    snapshot = runtime_snapshot()
    checks = snapshot["checks"]
    failures = [name for name, info in checks.items() if not info.get("ok")]
    snapshot["self_test"] = {
        "ok": not failures,
        "failures": failures,
        "tested_at": utc_now(),
    }
    return snapshot


@app.post("/api/youtube/info")
def youtube_info(payload: InfoRequest) -> dict[str, Any]:
    opts = youtube_opts({
        "quiet": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "playlistend": 100,
    })
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            raw = ydl.extract_info(payload.url, download=False)
        return public_info(raw)
    except Exception as exc:
        text = str(exc)
        if "Sign in to confirm you’re not a bot" in text or "Sign in to confirm you're not a bot" in text:
            auth = cookie_status()
            if not auth.get("ok"):
                raise HTTPException(
                    status_code=401,
                    detail="YouTube verlangt für diese Server-IP eine angemeldete Session. Im Runtime-Center fehlt noch YouTube Auth. Hinterlege YOUTUBE_COOKIES_BASE64 als Render-Secret und versuche die Analyse erneut.",
                ) from exc
            raise HTTPException(
                status_code=401,
                detail="YouTube hat die hinterlegte Session abgelehnt. Das Cookie-Secret muss wahrscheinlich erneuert werden.",
            ) from exc
        raise HTTPException(status_code=400, detail=f"Analyse fehlgeschlagen: {exc}") from exc


def make_format_selector(req: DownloadRequest) -> tuple[str, list[dict[str, Any]]]:
    postprocessors: list[dict[str, Any]] = []

    if req.mode == "audio":
        codec = req.container.lower()
        codec_map = {"mp3": "mp3", "m4a": "m4a", "aac": "aac", "opus": "opus", "flac": "flac", "wav": "wav"}
        if codec not in codec_map:
            raise ValueError(f"Nicht unterstütztes Audioformat: {codec}")
        postprocessors.append({"key": "FFmpegExtractAudio", "preferredcodec": codec_map[codec], "preferredquality": str(req.audio_bitrate)})
        return "bestaudio/best", postprocessors

    container = req.container.lower()
    if container not in {"mp4", "mkv", "webm"}:
        raise ValueError(f"Nicht unterstütztes Videoformat: {container}")

    if req.quality == "best":
        selector = "bestvideo*+bestaudio/best"
    else:
        try:
            height = int(req.quality)
        except ValueError as exc:
            raise ValueError("Ungültige Videoqualität") from exc
        selector = f"bestvideo*[height<={height}]+bestaudio/best[height<={height}]"
    return selector, postprocessors


def run_download(job_id: str, req: DownloadRequest) -> None:
    cancel_event = cancel_events[job_id]
    try:
        selector, postprocessors = make_format_selector(req)
        if req.is_playlist:
            base_name = "%(playlist_title|Playlist)s/%(playlist_index)03d - %(title)s"
        else:
            base_name = safe_filename(req.filename) if req.filename else "%(title)s"
        outtmpl = str(DOWNLOAD_DIR / f"{base_name}.%(ext)s")

        def check_cancel() -> None:
            if cancel_event.is_set():
                raise yt_dlp.utils.DownloadError("Download durch Benutzer abgebrochen")

        def progress_hook(data: dict[str, Any]) -> None:
            check_cancel()
            status = data.get("status")
            info_dict = data.get("info_dict") or {}
            playlist_index = info_dict.get("playlist_index")
            playlist_count = info_dict.get("playlist_count") or info_dict.get("n_entries")
            current_title = info_dict.get("title")

            if req.is_playlist and playlist_index:
                idx = int(playlist_index)
                items = list((jobs.get(job_id) or {}).get("playlist_items") or playlist_items)
                found = False
                for item in items:
                    if int(item.get("index") or 0) == idx:
                        item.update({"title": current_title or item.get("title"), "status": "downloading"})
                        found = True
                        break
                if not found:
                    items.append({"index": idx, "title": current_title or f"Video {idx}", "status": "downloading", "percent": 0.0})
                playlist_items[:] = items

            if status == "downloading":
                downloaded = data.get("downloaded_bytes") or 0
                total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
                item_percent = (downloaded / total * 100) if total else 0
                overall = item_percent
                if req.is_playlist and playlist_index and playlist_count:
                    overall = ((int(playlist_index) - 1) + item_percent / 100) / int(playlist_count) * 100
                update_job(
                    job_id,
                    status="downloading",
                    percent=round(overall, 1),
                    item_percent=round(item_percent, 1),
                    current_item=int(playlist_index) if playlist_index else None,
                    playlist_count=int(playlist_count) if playlist_count else None,
                    current_title=current_title,
                    downloaded_bytes=downloaded,
                    total_bytes=total or None,
                    downloaded_text=format_bytes(downloaded),
                    total_text=format_bytes(total) if total else None,
                    speed=data.get("speed"),
                    speed_text=(f"{format_bytes(data.get('speed'))}/s" if data.get("speed") else None),
                    eta=data.get("eta"),
                    eta_text=format_seconds(data.get("eta")),
                    playlist_items=[
                        ({**item, "percent": round(item_percent, 1)} if int(item.get("index") or 0) == int(playlist_index or 0) else item)
                        for item in playlist_items
                    ] if req.is_playlist else [],
                )
            elif status == "finished":
                if req.is_playlist and playlist_index:
                    idx = int(playlist_index)
                    for item in playlist_items:
                        if int(item.get("index") or 0) == idx:
                            item.update({"title": current_title or item.get("title"), "status": "finished", "percent": 100.0})
                update_job(job_id, status="processing", current_title=current_title, playlist_items=playlist_items if req.is_playlist else [])

        def match_filter(info: dict[str, Any], *, incomplete: bool = False) -> str | None:
            del info, incomplete
            check_cancel()
            return None

        selected_entries = sorted({int(x) for x in (req.selected_entries or []) if int(x) > 0})
        playlist_items: list[dict[str, Any]] = []
        if req.is_playlist and selected_entries:
            playlist_items = [
                {"index": idx, "title": f"Video {idx}", "status": "queued", "percent": 0.0}
                for idx in selected_entries
            ]
            update_job(job_id, playlist_count=len(selected_entries), playlist_items=playlist_items)

        opts: dict[str, Any] = youtube_opts({
            "format": selector,
            "outtmpl": outtmpl,
            "progress_hooks": [progress_hook],
            "match_filter": match_filter,
            "noplaylist": not req.is_playlist,
            "quiet": True,
            "restrictfilenames": False,
            "windowsfilenames": False,
            "postprocessors": postprocessors,
            "writethumbnail": req.embed_thumbnail,
            "addmetadata": req.write_metadata,
            "ignoreerrors": bool(req.is_playlist),
        })
        if req.is_playlist and selected_entries:
            opts["playlist_items"] = ",".join(str(x) for x in selected_entries)

        if req.mode == "video":
            opts["merge_output_format"] = req.container.lower()
            opts["remuxvideo"] = req.container.lower()
        if req.embed_thumbnail:
            opts.setdefault("postprocessors", []).append({"key": "EmbedThumbnail"})

        update_job(job_id, status="starting")
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(req.url, download=True)
            check_cancel()

            if req.is_playlist:
                playlist_title = safe_filename((info or {}).get("title") or req.title or "Playlist")
                playlist_dir = DOWNLOAD_DIR / playlist_title
                result_entries = (info or {}).get("entries") or []
                requested_count = len(selected_entries) if selected_entries else ((info or {}).get("playlist_count") or len(result_entries))
                successful_count = len([entry for entry in result_entries if entry])
                failed_count = max(0, int(requested_count or 0) - successful_count)
                final_items = []
                for item in playlist_items:
                    if item.get("status") == "finished":
                        final_items.append(item)
                    else:
                        final_items.append({**item, "status": "skipped" if failed_count else "finished", "percent": 100.0 if not failed_count else item.get("percent", 0.0)})
                update_job(
                    job_id,
                    status="finished_with_errors" if failed_count else "finished",
                    percent=100.0,
                    title=(info or {}).get("title") or req.title or "Playlist",
                    playlist_count=requested_count,
                    playlist_items=final_items,
                    playlist_failures=failed_count,
                    filename=None,
                    output_path=str(playlist_dir),
                )
                return

            prepared = Path(ydl.prepare_filename(info))

        candidates = sorted(DOWNLOAD_DIR.glob(f"{prepared.stem}.*"), key=lambda p: p.stat().st_mtime, reverse=True)
        final_file = candidates[0] if candidates else prepared
        if not final_file.exists():
            raise FileNotFoundError("Die fertige Datei konnte nicht gefunden werden.")

        update_job(
            job_id,
            status="finished",
            percent=100.0,
            title=(info or {}).get("title") or req.title,
            filename=final_file.name,
            file_size=final_file.stat().st_size,
            file_size_text=format_bytes(final_file.stat().st_size),
            download_url=f"/api/youtube/files/{job_id}",
        )
    except Exception as exc:
        if cancel_event.is_set():
            update_job(job_id, status="cancelled", error=None)
        else:
            update_job(job_id, status="failed", error=str(exc))
    finally:
        cancel_events.pop(job_id, None)


@app.post("/api/youtube/download")
def youtube_download(payload: DownloadRequest) -> dict[str, Any]:
    if not shutil.which("ffmpeg"):
        raise HTTPException(status_code=503, detail="ffmpeg ist nicht installiert oder nicht im PATH.")

    job_id = uuid.uuid4().hex
    now = utc_now()
    job = {
        "id": job_id,
        "created_at": now,
        "updated_at": now,
        "status": "queued",
        "percent": 0.0,
        "title": payload.title or ("Playlist" if payload.is_playlist else "Download"),
        "playlist_count": None,
        "playlist_items": [],
        "request": payload.model_dump(),
    }
    with jobs_lock:
        jobs[job_id] = job
        cancel_events[job_id] = threading.Event()
    persist_job(job)
    threading.Thread(target=run_download, args=(job_id, payload), daemon=True, name=f"yt-{job_id[:8]}").start()
    return dict(job)


@app.get("/api/youtube/jobs/{job_id}")
def youtube_job(job_id: str) -> dict[str, Any]:
    with jobs_lock:
        job = jobs.get(job_id)
        if job:
            return dict(job)
    with db_connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Job nicht gefunden")
    return historical_job(row)


@app.post("/api/youtube/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, Any]:
    with jobs_lock:
        job = jobs.get(job_id)
        event = cancel_events.get(job_id)
        if not job or not event:
            raise HTTPException(status_code=409, detail="Dieser Job kann nicht mehr abgebrochen werden.")
        if job.get("status") in {"finished", "failed", "cancelled"}:
            raise HTTPException(status_code=409, detail="Dieser Job ist bereits beendet.")
        event.set()
    update_job(job_id, status="cancelling")
    return {"ok": True, "id": job_id, "status": "cancelling"}




@app.post("/api/youtube/duplicates")
def youtube_duplicates(payload: DownloadRequest) -> dict[str, Any]:
    """Find an already completed job with matching source and download settings."""
    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM jobs
            WHERE source_url = ? AND mode = ? AND container = ?
              AND status IN ('finished', 'finished_with_errors')
            ORDER BY created_at DESC
            LIMIT 25
            """,
            (payload.url, payload.mode, payload.container),
        ).fetchall()

    wanted = payload.model_dump()
    matches = []
    for row in rows:
        try:
            stored = json.loads(row["request_json"] or "{}")
        except json.JSONDecodeError:
            stored = {}
        same_quality = (
            stored.get("quality", "best") == wanted.get("quality", "best")
            if payload.mode == "video"
            else int(stored.get("audio_bitrate", 320)) == int(wanted.get("audio_bitrate", 320))
        )
        same_selection = sorted(stored.get("selected_entries") or []) == sorted(wanted.get("selected_entries") or [])
        if same_quality and same_selection:
            matches.append(historical_job(row))
    return {"duplicate": bool(matches), "items": matches[:5]}


@app.post("/api/youtube/jobs/{job_id}/retry")
def retry_job(job_id: str) -> dict[str, Any]:
    with db_connect() as conn:
        row = conn.execute("SELECT request_json FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Job nicht gefunden")
    try:
        request_data = json.loads(row["request_json"])
        payload = DownloadRequest(**request_data)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Gespeicherter Job kann nicht erneut gestartet werden: {exc}") from exc
    return youtube_download(payload)


@app.get("/api/youtube/suggestions")
def youtube_suggestions(url: str | None = None, q: str | None = None, limit: int = 8) -> dict[str, Any]:
    """Return lightweight, clickable suggestions related to a video/channel.

    This uses yt-dlp's regular search extraction only; it does not download media.
    """
    limit = max(1, min(limit, 12))
    query = (q or "").strip()
    try:
        if not query and url:
            with yt_dlp.YoutubeDL(youtube_opts({"quiet": True, "skip_download": True, "noplaylist": True})) as ydl:
                source = ydl.extract_info(url, download=False) or {}
            channel = source.get("channel") or source.get("uploader")
            title = source.get("title")
            query = channel or title or ""
        if not query:
            return {"items": [], "query": ""}

        opts = youtube_opts({"quiet": True, "skip_download": True, "extract_flat": True})
        with yt_dlp.YoutubeDL(opts) as ydl:
            result = ydl.extract_info(f"ytsearch{limit}:{query}", download=False) or {}
        items = []
        for entry in result.get("entries") or []:
            if not entry:
                continue
            item = compact_entry(entry)
            video_id = entry.get("id")
            if video_id and (not item.get("url") or not str(item.get("url")).startswith(("http://", "https://"))):
                item["url"] = f"https://www.youtube.com/watch?v={video_id}"
            items.append(item)
        return {"items": items[:limit], "query": query}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Vorschläge konnten nicht geladen werden: {exc}") from exc

@app.get("/api/youtube/history")
def youtube_history(limit: int = 40) -> dict[str, Any]:
    limit = max(1, min(limit, 200))
    with db_connect() as conn:
        rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return {"items": [historical_job(row) for row in rows]}


@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    settings = dict(DEFAULT_SETTINGS)
    with db_connect() as conn:
        rows = conn.execute("SELECT key, value_json FROM settings").fetchall()
    for row in rows:
        try:
            settings[row["key"]] = json.loads(row["value_json"])
        except json.JSONDecodeError:
            continue
    return settings


@app.put("/api/settings")
def put_settings(payload: SettingsRequest) -> dict[str, Any]:
    data = payload.model_dump()
    now = utc_now()
    with db_connect() as conn:
        for key, value in data.items():
            conn.execute(
                "INSERT INTO settings(key, value_json, updated_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
                (key, json.dumps(value), now),
            )
    return data


@app.get("/api/youtube/files/{job_id}")
def youtube_file(job_id: str) -> FileResponse:
    with jobs_lock:
        job = jobs.get(job_id)
        filename = job.get("filename") if job and job.get("status") == "finished" else None
    if not filename:
        with db_connect() as conn:
            row = conn.execute("SELECT filename, status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row or row["status"] != "finished" or not row["filename"]:
            raise HTTPException(status_code=404, detail="Datei nicht verfügbar")
        filename = row["filename"]
    path = (DOWNLOAD_DIR / filename).resolve()
    if DOWNLOAD_DIR not in path.parents or not path.exists():
        raise HTTPException(status_code=404, detail="Datei nicht gefunden")
    return FileResponse(path, filename=path.name)


@app.get("/api/youtube/history/files/{job_id}")
def history_file(job_id: str) -> FileResponse:
    return youtube_file(job_id)


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
