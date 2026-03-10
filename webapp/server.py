# PROJECT: CLIPPER
# ROOT: c:\Users\ryder\projects\clipper
# WARNING: Do not apply youtube-blueprint changes here.

"""
FastAPI backend for clipper web UI. Runs clip pipeline in subprocess, streams progress via SSE.
"""

import asyncio
import atexit
import csv
import hashlib
import io
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
import zipfile
import threading
import queue as thread_queue
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import RedirectResponse

# Repo root (server.py lives in webapp/) so outputs/ is always absolute and cwd-independent
REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = REPO_ROOT
if os.environ.get("CLIPPER_REPO_ROOT", "").strip():
    _override = Path(os.environ.get("CLIPPER_REPO_ROOT", "").strip()).resolve()
    if _override.is_dir():
        REPO_ROOT = _override
        PROJECT_ROOT = REPO_ROOT

# Load .env from repo root if present (so OPENAI_API_KEY etc. can be set without system env)
_env_file = REPO_ROOT / ".env"
if _env_file.is_file():
    try:
        for line in _env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip("'\"").strip()
                if k and k not in os.environ:
                    os.environ[k] = v
    except Exception:
        pass

# Set Piper env vars from repo defaults if not already set (so Reddit TTS works without user config)
_default_piper_bin = REPO_ROOT / "piper" / "piper" / "piper.exe"
_default_piper_model = REPO_ROOT / "piper" / "voices" / "en_US-lessac-medium.onnx"
if not os.environ.get("PIPER_BIN", "").strip() and _default_piper_bin.is_file():
    os.environ["PIPER_BIN"] = str(_default_piper_bin.resolve())
if not os.environ.get("PIPER_MODEL", "").strip() and _default_piper_model.is_file():
    os.environ["PIPER_MODEL"] = str(_default_piper_model.resolve())
# OUTPUTS_DIR: use env so Fly.io volume /data/outputs can be used (e.g. OUTPUTS_DIR=/data/outputs)
_out = (os.environ.get("OUTPUTS_DIR") or os.environ.get("CLIPPER_OUTPUTS_DIR") or "").strip()
if _out:
    OUTPUTS_DIR = Path(_out).resolve()
else:
    OUTPUTS_DIR = REPO_ROOT / "outputs"
JOBS_DIR = OUTPUTS_DIR / "jobs"
RENDERS_DIR = OUTPUTS_DIR / "renders"
QUEUE_PATH = OUTPUTS_DIR / "batch_queue.json"
try:
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    RENDERS_DIR.mkdir(parents=True, exist_ok=True)
except OSError as e:
    print(f"[SERVER] non-fatal: could not create outputs dirs: {e}", flush=True)
print(f"[SERVER] Library scan path: JOBS_DIR={JOBS_DIR.resolve()!s} RENDERS_DIR={RENDERS_DIR.resolve()!s}", flush=True)
WEB_DIR = REPO_ROOT / "webapp" / "web"
ASSETS_DIR = REPO_ROOT / "webapp" / "assets"
JOB_RUNNER_PATH = Path(__file__).resolve().parent / "job_runner.py"
RUNTIME_JSON = REPO_ROOT / "webapp" / "runtime.json"
RUNTIME_LOCK = REPO_ROOT / "webapp" / "runtime.lock"
# RENDER_DEPLOY: Use 0.0.0.0 and PORT when running on Render (PORT set by platform); local stays 127.0.0.1:8000.
_port_env = (os.environ.get("PORT") or "").strip()
PORT = int(_port_env) if _port_env.isdigit() else 8000
HOST = "0.0.0.0" if _port_env else "127.0.0.1"
OLLAMA_MODEL = "llama3.1"
OLLAMA_URL = "http://localhost:11434/api/generate"

jobs = {}
jobs_lock = threading.Lock()
reddit_jobs: dict = {}
reddit_jobs_lock = threading.Lock()
REDDIT_INTRO_DURATION = 1.7
REDDIT_MAX_STORY_CHARS = 12000
OPENAI_API_KEY = (os.environ.get("OPENAI_API_KEY") or "").strip()
OPENAI_ENABLED = bool(OPENAI_API_KEY)
# Best quality: use OpenAI for story enhance (gpt-4o) and for subtitles (Whisper API). Uses your credits.
OPENAI_ENHANCE_MODEL = (os.environ.get("OPENAI_ENHANCE_MODEL") or "gpt-4o-mini").strip() or "gpt-4o-mini"
OPENAI_SUBTITLES = (os.environ.get("OPENAI_SUBTITLES") or "1").strip().lower() in ("1", "true", "yes")
queue_lock = threading.Lock()
_log_lock = threading.Lock()
metadata_lock = threading.Lock()
last_health_ok = 0.0  # timestamp; updated by /api/health

# Per-clip metadata store (outputs/metadata.json). Keyed by clip filename.
METADATA_PATH = OUTPUTS_DIR / "metadata.json"
OLLAMA_TIMEOUT_SEC = 20

# ---- TIKTOK_PHASE_1_AUTH: env-based config (do NOT hardcode secrets) ----
# TIKTOK_CONFIG_SOURCE: TikTok config is loaded from environment; .env file is loaded above from REPO_ROOT.
APP_STATE_DIR = OUTPUTS_DIR / "app_state"
TIKTOK_CLIENT_KEY = (os.environ.get("TIKTOK_CLIENT_KEY") or "").strip()
TIKTOK_CLIENT_SECRET = (os.environ.get("TIKTOK_CLIENT_SECRET") or "").strip()
APP_BASE_URL = (os.environ.get("APP_BASE_URL") or "").strip().rstrip("/")
_explicit_redirect = (os.environ.get("TIKTOK_REDIRECT_URI") or "").strip()
# TIKTOK_PROD_CALLBACK: Prefer explicit TIKTOK_REDIRECT_URI if set; else derive from APP_BASE_URL.
# In production set APP_BASE_URL to deployed backend URL → redirect URI becomes {APP_BASE_URL}/api/tiktok/callback.
if _explicit_redirect:
    TIKTOK_REDIRECT_URI = _explicit_redirect
    _redirect_source = "TIKTOK_REDIRECT_URI"
else:
    TIKTOK_REDIRECT_URI = (APP_BASE_URL + "/api/tiktok/callback") if APP_BASE_URL else ""
    _redirect_source = "APP_BASE_URL (derived)" if APP_BASE_URL else "none"
TIKTOK_TOKENS_PATH = APP_STATE_DIR / "tiktok_tokens.json"
tiktok_tokens_lock = threading.Lock()
try:
    APP_STATE_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    pass
# Startup validation logs for TikTok config (TIKTOK_SETUP_HELPER: visible in server console).
print(f"[TIKTOK_CONFIG] client_key_present={bool(TIKTOK_CLIENT_KEY)}", flush=True)
print(f"[TIKTOK_CONFIG] client_secret_present={bool(TIKTOK_CLIENT_SECRET)}", flush=True)
print(f"[TIKTOK_CONFIG] app_base_url={APP_BASE_URL or '(not set)'}", flush=True)
print(f"[TIKTOK_CONFIG] redirect_uri={TIKTOK_REDIRECT_URI or '(not set)'} (source={_redirect_source})", flush=True)


def _tiktok_load_tokens() -> dict:
    """TIKTOK_PHASE_1_AUTH: Phase-1 local storage. Load tokens from JSON file. Returns {} if missing/invalid."""
    with tiktok_tokens_lock:
        if not TIKTOK_TOKENS_PATH.is_file():
            return {}
        try:
            data = json.loads(TIKTOK_TOKENS_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}


def _tiktok_save_tokens(data: dict) -> None:
    """TIKTOK_PHASE_1_AUTH: Phase-1 local storage. Save tokens to JSON file. Only store what is needed."""
    with tiktok_tokens_lock:
        try:
            TIKTOK_TOKENS_PATH.parent.mkdir(parents=True, exist_ok=True)
            TIKTOK_TOKENS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError:
            pass


# TTS: server-side TTS disabled; AI Voice tab uses browser SpeechSynthesis only.

# Product mode: local auth + usage (outputs/users.json, outputs/usage.json)
USERS_PATH = OUTPUTS_DIR / "users.json"
USAGE_PATH = OUTPUTS_DIR / "usage.json"
users_lock = threading.Lock()
usage_lock = threading.Lock()
# Plans and limits (per day)
PLAN_FREE = "free"
PLAN_PRO = "pro"
PLAN_TURBO = "turbo"
FREE_MAX_CLIPS_DAY = 3
FREE_MAX_MINUTES_DAY = 10
PRO_MAX_CLIPS_DAY = 30
PRO_MAX_MINUTES_DAY = 120
_raw_turbo_clips = os.environ.get("TURBO_MAX_CLIPS_DAY", "100").strip()
TURBO_MAX_CLIPS_DAY = max(1, int(_raw_turbo_clips)) if _raw_turbo_clips.isdigit() else 100
_raw_turbo_mins = os.environ.get("TURBO_MAX_MINUTES_DAY", "600").strip()
try:
    TURBO_MAX_MINUTES_DAY = max(1.0, float(_raw_turbo_mins))
except ValueError:
    TURBO_MAX_MINUTES_DAY = 600
# Admin override (hardcoded; not for production)
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin"
SESSION_COOKIE = "clipper_user"
# Local dev: skip daily clip/minutes limit check when set. Enables unlimited renders in dev.
DEV_DISABLE_LIMITS = (os.environ.get("DEV_DISABLE_LIMITS") or "0").strip().lower() in ("1", "true", "yes")


def _load_users() -> dict:
    """Load users.json. Returns {} if missing or invalid. Never raises."""
    with users_lock:
        if not USERS_PATH.is_file():
            return {}
        try:
            data = json.loads(USERS_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}


def _save_users(data: dict) -> None:
    with users_lock:
        try:
            OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
            USERS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError:
            pass


def _load_usage() -> dict:
    """Load usage.json. Shape: { username: { "YYYY-MM-DD": { "clips": N, "minutes_approx": M } } }."""
    with usage_lock:
        if not USAGE_PATH.is_file():
            return {}
        try:
            data = json.loads(USAGE_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}


def _save_usage(data: dict) -> None:
    with usage_lock:
        try:
            OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
            USAGE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError:
            pass


def _get_plan(username: str) -> str:
    """Return plan for user. Default free. Admin is turbo."""
    if not username:
        return PLAN_FREE
    if username == ADMIN_USERNAME:
        return PLAN_TURBO
    users = _load_users()
    u = users.get(username)
    if not u or not isinstance(u, dict):
        return PLAN_FREE
    return u.get("plan") or PLAN_FREE


def _plan_limits(plan: str) -> tuple:
    """Return (max_clips_per_day, max_minutes_per_day) for plan."""
    if plan == PLAN_TURBO:
        return (TURBO_MAX_CLIPS_DAY, TURBO_MAX_MINUTES_DAY)
    if plan == PLAN_PRO:
        return (PRO_MAX_CLIPS_DAY, PRO_MAX_MINUTES_DAY)
    return (FREE_MAX_CLIPS_DAY, FREE_MAX_MINUTES_DAY)


def _plan_force_watermark(plan: str) -> bool:
    """Free plan must use watermark; Pro/Turbo optional."""
    return plan == PLAN_FREE


def _usage_today(username: str) -> dict:
    """Return { clips: N, minutes_approx: M } for today. Never raises."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    data = _load_usage()
    day_data = (data.get(username) or {}).get(today) or {}
    return {"clips": int(day_data.get("clips") or 0), "minutes_approx": float(day_data.get("minutes_approx") or 0)}


def _check_quota(username: str, extra_clips: int, extra_minutes: float) -> tuple[bool, str]:
    """Return (ok, error_message). Limits disabled - always allow."""
    return (True, "")


def _record_usage(username: str, clips: int, minutes_approx: float) -> None:
    """Add usage for today. No-op if username empty."""
    if not username:
        return
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    data = _load_usage()
    if username not in data:
        data[username] = {}
    if today not in data[username]:
        data[username][today] = {"clips": 0, "minutes_approx": 0}
    data[username][today]["clips"] = data[username][today].get("clips", 0) + clips
    data[username][today]["minutes_approx"] = data[username][today].get("minutes_approx", 0) + minutes_approx
    _save_usage(data)


def _reset_usage_today(username: str) -> None:
    """Set today's usage to 0 for username. Used by dev reset endpoint."""
    if not username:
        return
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    data = _load_usage()
    if username not in data:
        data[username] = {}
    data[username][today] = {"clips": 0, "minutes_approx": 0}
    _save_usage(data)


# Auth bypass: when True, no login required; unauthenticated requests use ADMIN_USERNAME.
AUTH_BYPASS = True

async def _get_current_user(request: Request) -> Optional[str]:
    """Return username if session cookie is set and user exists (or is admin). Else None (or bypass user)."""
    username = request.cookies.get(SESSION_COOKIE) or (request.headers.get("X-Clipper-User") or "").strip()
    if not username:
        if AUTH_BYPASS:
            return ADMIN_USERNAME
        return None
    if username == ADMIN_USERNAME:
        return username
    users = _load_users()
    if username not in users or not isinstance(users[username], dict):
        if AUTH_BYPASS:
            return ADMIN_USERNAME
        return None
    return username


async def get_current_user_required(request: Request) -> str:
    """Return username or raise 401. Use for protected routes. When AUTH_BYPASS, never raises."""
    user = await _get_current_user(request)
    if not user:
        if AUTH_BYPASS:
            return ADMIN_USERNAME
        raise HTTPException(status_code=401, detail="Login required")
    return user


def _verify_password(username: str, password: str) -> bool:
    """Check password. Admin uses hardcoded; others from users.json (plaintext for local dev)."""
    if username == ADMIN_USERNAME:
        return password == ADMIN_PASSWORD
    users = _load_users()
    u = users.get(username)
    if not u or not isinstance(u, dict):
        return False
    return (u.get("password") or "") == password


def _log(msg: str):
    """Write to stdout only."""
    print(msg, flush=True)


def _load_metadata() -> dict:
    """Load metadata.json. Returns {} if missing or invalid. Never raises."""
    with metadata_lock:
        if not METADATA_PATH.is_file():
            return {}
        try:
            data = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}


def _save_metadata(data: dict) -> None:
    """Persist metadata.json. No-op on write error."""
    with metadata_lock:
        try:
            OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
            METADATA_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError:
            pass


# Clip score heuristics (0-100 from transcript snippet)
CLIP_SCORE_LAUGHTER = ["laugh", "hahaha", "haha", "audience", "crowd", "claps", "applause"]
CLIP_SCORE_HOOK = ["wait", "listen", "bro", "no way", "crazy", "wild", "insane", "actually", "literally", "watch"]
CLIP_SCORE_FILLER = ["um", "uh", "er", "like", "you know", "i mean"]
CLIP_SCORE_PUNCHY_WORDS_MIN, CLIP_SCORE_PUNCHY_WORDS_MAX = 3, 10
CLIP_SCORE_FIRST_N_WORDS = 20


def _clip_score(text: str) -> int:
    """Return integer 0-100 from transcript snippet heuristics. Fast, no API."""
    if not text or not isinstance(text, str):
        return 50
    t = text.lower().strip()
    if not t:
        return 50
    score = 50.0
    words = re.findall(r"\b\w+\b", t)
    # Short punchy sentences: +1 per sentence with 3-10 words
    for sent in re.split(r"[.!?]+", t):
        w = len(re.findall(r"\b\w+\b", sent))
        if CLIP_SCORE_PUNCHY_WORDS_MIN <= w <= CLIP_SCORE_PUNCHY_WORDS_MAX:
            score += 2
    # Laughter / crowd
    for kw in CLIP_SCORE_LAUGHTER:
        if kw in t:
            score += 4
    # Hook phrases
    for kw in CLIP_SCORE_HOOK:
        if kw in t:
            score += 3
    # Strong words in first N words
    first = " ".join(words[:CLIP_SCORE_FIRST_N_WORDS])
    for kw in CLIP_SCORE_HOOK:
        if kw in first:
            score += 2
    # Filler penalty
    for kw in CLIP_SCORE_FILLER:
        score -= t.count(kw) * 1.5
    return max(0, min(100, int(round(score))))


def _get_clip_metadata(filename: str) -> dict:
    """Get metadata entry for a clip filename. Returns empty dict if missing."""
    if not filename or not filename.endswith(".mp4"):
        return {}
    return _load_metadata().get(filename, {})


def _set_clip_metadata(filename: str, entry: dict) -> None:
    """Create or update metadata for a clip. Merges with existing; entry keys override."""
    if not filename or not filename.endswith(".mp4"):
        return
    with metadata_lock:
        data = _load_metadata()
        existing = data.get(filename, {})
        merged = {
            "titles": entry.get("titles", existing.get("titles", [])),
            "caption": entry.get("caption", existing.get("caption", "")),
            "hashtags": entry.get("hashtags", existing.get("hashtags", [])),
            "source_video_id": entry.get("source_video_id", existing.get("source_video_id", "")),
            "clip_start": entry.get("clip_start", existing.get("clip_start", 0)),
            "clip_end": entry.get("clip_end", existing.get("clip_end", 0)),
            "created_at": existing.get("created_at") or entry.get("created_at", datetime.now(timezone.utc).isoformat()),
            "transcript_snippet": entry.get("transcript_snippet") or existing.get("transcript_snippet") or "",
            "score": entry.get("score") if entry.get("score") is not None else existing.get("score"),
        }
        if entry.get("created_at") and not existing.get("created_at"):
            merged["created_at"] = entry["created_at"]
        data[filename] = merged
        _save_metadata(data)


def _resolve_clip_path(filename: str) -> Optional[Path]:
    """Resolve a clip filename to an absolute path under outputs/jobs/*/clips/. Returns None if not found or invalid."""
    if not filename or not filename.endswith(".mp4") or ".." in filename or "/" in filename or "\\" in filename:
        return None
    if not JOBS_DIR.is_dir():
        return None
    for job_dir in JOBS_DIR.iterdir():
        if not job_dir.is_dir():
            continue
        clips_dir = job_dir / "clips"
        if not clips_dir.is_dir():
            continue
        path = (clips_dir / filename).resolve()
        try:
            path.relative_to(clips_dir.resolve())
        except ValueError:
            continue
        if path.is_file():
            return path
    return None


def _safe_new_filename(name: str) -> Optional[str]:
    """Validate and normalize new clip filename: slug rules, must end .mp4, max 60 chars (including .mp4). Returns None if invalid."""
    if not name or not isinstance(name, str):
        return None
    name = name.strip()
    if ".." in name or "/" in name or "\\" in name:
        return None
    if name.endswith(".mp4"):
        stem = name[:-4]
    else:
        stem = name
        name = stem + ".mp4"
    stem_slug = re.sub(r"[^a-z0-9\s-]", "", stem.lower())
    stem_slug = re.sub(r"[-\s]+", "-", stem_slug).strip("-")
    if not stem_slug:
        return None
    stem_slug = stem_slug[:60 - 4]
    return (stem_slug + ".mp4") if stem_slug else None


def _migrate_metadata_key(old_filename: str, new_filename: str) -> None:
    """Move metadata entry from old key to new key. No-op if old missing."""
    if not old_filename or not new_filename or not old_filename.endswith(".mp4") or not new_filename.endswith(".mp4"):
        return
    with metadata_lock:
        data = _load_metadata()
        if old_filename not in data:
            return
        entry = data.pop(old_filename)
        data[new_filename] = entry
        _save_metadata(data)


def _looks_like_filename(s: str) -> bool:
    """True if s looks like a clip filename stem (e.g. run_20260210_225110_short_4)."""
    if not s or len(s) > 120:
        return False
    t = s.strip().lower().replace(" ", "_")
    return bool(re.match(r"^run_\d{8}_\d{6}_short_\d+$", t)) or bool(re.match(r"^short_\d+$", t))


def _enqueue_job_clips_for_upload(job_id: str, job_dir: Path, clips: list) -> None:
    """
    If config.posting.auto_enqueue_on_job_done is true, add each completed clip
    to outputs/queue.json for the worker to upload to YouTube Shorts / TikTok.
    """
    config_path = REPO_ROOT / "config.json"
    if not config_path.is_file():
        return
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return
    posting = config.get("posting", {})
    if not posting.get("auto_enqueue_on_job_done", False):
        return
    platforms = []
    if config.get("youtube", {}).get("enabled"):
        platforms.append("youtube")
    if config.get("tiktok", {}).get("enabled"):
        platforms.append("tiktok")
    if not platforms:
        return
    extras_dir = REPO_ROOT / "extras"
    if not extras_dir.is_dir():
        return
    if str(extras_dir) not in sys.path:
        sys.path.insert(0, str(extras_dir))
    try:
        import outputs_queue
    except ImportError:
        _log(f"[SERVER] auto_enqueue: extras.outputs_queue not found; skip enqueue for job {job_id}")
        return
    clips_dir = job_dir / "clips"
    enqueued = 0
    for c in clips:
        fname = c.get("file") or ""
        mp4_path = clips_dir / fname
        if not mp4_path.is_file():
            continue
        # Prefer content-based title; never use filename (e.g. run_20260210_short_4)
        title = (c.get("title") or c.get("caption") or "Short").strip()[:100]
        if not title or _looks_like_filename(title):
            title = (c.get("caption") or "Short").strip()[:100]
        caption = ((c.get("caption") or "") + " " + (c.get("hashtags") or "")).strip()[:5000]
        item = {
            "platforms": list(platforms),
            "mp4_path": str(mp4_path.resolve()),
            "title": title,
            "caption": caption,
        }
        try:
            outputs_queue.enqueue_item(REPO_ROOT, item)
            enqueued += 1
        except Exception as e:
            _log(f"[SERVER] auto_enqueue item failed: {e}")
    if enqueued:
        _log(f"[SERVER] auto_enqueue: job {job_id} — {enqueued} clip(s) added to upload queue (YouTube Shorts / TikTok).")

app = FastAPI()


@app.middleware("http")
async def log_request(request: Request, call_next):
    """Log every incoming request path so refresh and routes can be verified."""
    path = request.url.path or ""
    method = request.method or "GET"
    _log(f"[REQUEST] {method} {path}")
    response = await call_next(request)
    return response


# CORS: Vercel frontend + localhost for dev. Optional CORS_ORIGINS env (comma-separated) appends for production.
_CORS_ORIGINS = [
    "https://web-lovat-eta-96.vercel.app",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]
_extra = (os.environ.get("CORS_ORIGINS") or "").strip()
if _extra:
    _CORS_ORIGINS = _CORS_ORIGINS + [o.strip() for o in _extra.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


def _python_exe():
    return sys.executable


def _set_process_low_priority_win(proc: subprocess.Popen) -> None:
    """On Windows, set the child process to BELOW_NORMAL_PRIORITY_CLASS so the server stays responsive."""
    if sys.platform != "win32":
        return
    try:
        import psutil
        p = psutil.Process(proc.pid)
        p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        _log(f"[API] job_runner pid={proc.pid} set to BELOW_NORMAL priority (psutil)")
    except Exception:
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            BELOW_NORMAL_PRIORITY_CLASS = 0x4000
            PROCESS_SET_INFORMATION = 0x0200
            handle = kernel32.OpenProcess(PROCESS_SET_INFORMATION, False, proc.pid)
            if handle:
                kernel32.SetPriorityClass(handle, BELOW_NORMAL_PRIORITY_CLASS)
                kernel32.CloseHandle(handle)
                _log(f"[API] job_runner pid={proc.pid} set to BELOW_NORMAL priority (ctypes)")
        except Exception:
            pass


def _run_job(job_id: str, url: str, max_clips: int, clip_seconds: int, use_ollama: bool):
    """Run clip pipeline in subprocess. Never raises: all failures are logged and job marked error."""
    j = jobs.get(job_id)
    if not j:
        return
    job_start_time = time.time()
    log_lines = []
    max_log_lines = 200

    def append_log(line: str):
        log_lines.append(line)
        if len(log_lines) > max_log_lines:
            log_lines.pop(0)
        with jobs_lock:
            if job_id in jobs:
                jobs[job_id]["log_lines"] = list(log_lines)

    try:
        j["state"] = "running"
        j["started_at"] = datetime.now(timezone.utc).isoformat()
        j["stage"] = "doctor"
        j["progress"] = 0
        j["message"] = "Checking environment..."
        j["log_lines"] = []
        j["exit_code"] = None
        j["events"].put({"stage": "doctor", "progress": 0, "message": j["message"]})

        video_id = j.get("source_video_id", "")
        received_url = j.get("received_url", url)
        source_path = f"downloads/{video_id}.mp4"
        server_pid = os.getpid()
        _log(f'[JOB] job_id={job_id} received_url="{received_url}" video_id={video_id} source_path="{source_path}"')
        job_truth_line = f'[JOB] job_id={job_id} received_url="{received_url}" video_id={video_id} source_path="{source_path}"'
        append_log(job_truth_line)

        env = os.environ.copy()
        env.pop("URL", None)
        env.pop("YT_URL", None)
        env["CLIP_URL"] = url
        env["CLIP_PREFIX"] = time.strftime("run_%Y%m%d_%H%M%S_", time.localtime(job_start_time))
        env["CLIP_JOB_ID"] = job_id
        env["SOURCE_VIDEO_ID"] = video_id
        env["SOURCE_URL"] = url
        JOB_DIR = JOBS_DIR / job_id
        env["OUTPUTS_DIR_JOB"] = str(JOB_DIR.resolve())
        if max_clips is not None:
            env["CLIP_MAX_CLIPS"] = str(max_clips)
        if clip_seconds is not None:
            env["CLIP_SECONDS"] = str(clip_seconds)
        env["CLIP_WATERMARK"] = "1" if j.get("watermark") else "0"
        try:
            meta_path = JOB_DIR / "job.meta.json"
            meta = {}
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except Exception:
                    pass
            meta["job_id"] = job_id
            meta["source_url"] = url
            meta["source_video_id"] = video_id
            meta["source_path"] = source_path
            meta["server_pid"] = server_pid
            meta.setdefault("created_at", datetime.now(timezone.utc).isoformat())
            meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        except OSError:
            pass
        cwd = str(PROJECT_ROOT)
        py = _python_exe()

        # Optional doctor
        try:
            subprocess.run(
                [py, str(PROJECT_ROOT / "doctor.py")],
                cwd=cwd,
                capture_output=True,
                timeout=120,
                encoding="utf-8",
                errors="replace",
            )
        except Exception:
            pass

        j["stage"] = "download"
        j["events"].put({"stage": "download", "progress": 5, "message": "Starting download..."})

        _log(f"[API] spawn job_runner (clip.py in separate process) --url {url!r}")
        kwargs = {
            "cwd": cwd,
            "env": env,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        proc = subprocess.Popen(
            [py, str(JOB_RUNNER_PATH), job_id],
            **kwargs,
        )
        if sys.platform == "win32":
            _set_process_low_priority_win(proc)
        with jobs_lock:
            if job_id in jobs:
                jobs[job_id]["last_progress_at"] = time.time()
                jobs[job_id]["_proc"] = proc
        progress_file = JOB_DIR / "progress.json"
        output_log = JOB_DIR / "output.log"
        done_file = JOB_DIR / "done.json"
        try:
            while proc.poll() is None:
                time.sleep(0.5)
                try:
                    if progress_file.is_file():
                        data = json.loads(progress_file.read_text(encoding="utf-8"))
                        stage = data.get("stage")
                        pct = data.get("progress", 0)
                        msg = data.get("message", "")
                        with jobs_lock:
                            if job_id in jobs:
                                jobs[job_id]["stage"] = stage
                                jobs[job_id]["progress"] = pct
                                jobs[job_id]["message"] = msg
                                jobs[job_id]["last_progress_at"] = time.time()
                                if stage == "done" and pct == 100:
                                    jobs[job_id]["state"] = "done_pending_exit"
                        try:
                            j["events"].put({"stage": stage, "progress": pct, "message": msg})
                        except Exception:
                            pass
                except Exception:
                    pass
                try:
                    if output_log.is_file():
                        lines = output_log.read_text(encoding="utf-8", errors="replace").splitlines()
                        if lines:
                            log_lines[:] = lines[-500:]
                            infos = []
                            subs = {}
                            for line in lines:
                                line = line.strip()
                                if line.startswith("[CLIP_INFO] ") and "|" in line:
                                    fname, text = line[len("[CLIP_INFO] "):].split("|", 1)
                                    infos.append({"file": fname.strip(), "text": text})
                                elif line.startswith("[CLIP_SUBS] "):
                                    parts = line[len("[CLIP_SUBS] "):].strip().split()
                                    kv = dict(p.split("=", 1) for p in parts if "=" in p)
                                    fname = kv.get("file", "").strip()
                                    if fname:
                                        try:
                                            score_val = float(kv.get("score", 0.5))
                                        except (TypeError, ValueError):
                                            score_val = 0.5
                                        subs[fname] = {"status": kv.get("status", "uncertain"), "score": score_val, "burn": kv.get("burn", "false").lower() == "true"}
                            with jobs_lock:
                                if job_id in jobs:
                                    jobs[job_id]["log_lines"] = list(log_lines)
                                    jobs[job_id]["clip_infos"] = infos
                                    jobs[job_id]["clip_subs"] = subs
                except Exception:
                    pass
        finally:
            with jobs_lock:
                if job_id in jobs:
                    jobs[job_id].pop("_proc", None)
        exit_code = None
        try:
            if done_file.is_file():
                exit_code = json.loads(done_file.read_text(encoding="utf-8")).get("exit_code")
        except Exception:
            pass
        if exit_code is None:
            exit_code = proc.returncode
        with jobs_lock:
            if job_id in jobs:
                jobs[job_id]["exit_code"] = exit_code

        # Job-scoped: only list clips from this job's directory (skip if already canceled)
        if j.get("state") == "canceled":
            try:
                j["events"].put(None)
            except Exception:
                pass
        else:
            clips_dir = JOB_DIR / "clips"
            found = list(clips_dir.glob("*.mp4")) if clips_dir.is_dir() else []
            found.sort(key=lambda p: p.stat().st_mtime)

            if found:
                clips = []
                for f in found:
                    if not f.is_file():
                        continue
                    tt = _load_tiktok_for_clip(f.name, job_id=job_id)
                    c = {"file": f.name, "job_id": job_id, "url": f"/outputs/jobs/{job_id}/clips/{f.name}", "title": None, "hashtags": None}
                    c["source_video_id"] = (tt or {}).get("source_video_id") or j.get("source_video_id", "")
                    c["source_url"] = (tt or {}).get("source_url") or j.get("source_url", "")
                    if tt:
                        c["caption"] = tt.get("caption", "") or "You won't believe this"
                        c["caption_style"] = tt.get("caption_style", "hook_only")
                        c["hashtags"] = tt.get("hashtags", "")
                        c["upload_filename"] = tt.get("upload_filename", Path(f.name).stem)
                        # Content-based title from transcript (so we don't show filename like run_20260210_short_4)
                        if tt.get("suggested_title"):
                            c["title"] = tt["suggested_title"]
                    else:
                        c["caption"] = "You won't believe this"
                        c["caption_style"] = "hook_only"
                        c["hashtags"] = ""
                        c["upload_filename"] = Path(f.name).stem
                    subs = j.get("clip_subs", {}).get(f.name)
                    if subs:
                        c["subs_status"] = "burned" if subs.get("burn") else "skipped"
                        c["subs_reason"] = subs.get("status", "uncertain")
                        c["subs_score"] = subs.get("score")
                        c["subtitle_action"] = "burn" if subs.get("burn") else "skip"
                        c["baked_confidence"] = subs.get("score")
                    clips.append(c)
                with jobs_lock:
                    if job_id in jobs:
                        jobs[job_id]["clips"] = clips
                user_id_job = j.get("user_id")
                if user_id_job and clips:
                    clip_sec = int(j.get("clip_seconds") or 45)
                    minutes_approx = len(clips) * clip_sec / 60.0
                    _record_usage(user_id_job, len(clips), minutes_approx)
                infos = j.get("clip_infos") or []
                created_at = datetime.now(timezone.utc).isoformat()
                for i, c in enumerate(clips):
                    text = (infos[i].get("text", "")[:2000] if i < len(infos) else "") or ""
                    initial_caption = c.get("caption") or "You won't believe this"
                    titles, caption, hashtags_list = _generate_titles_caption_hashtags(text, use_ollama, initial_caption)
                    c["title"] = titles[0] if titles else c.get("title") or "Short"
                    c["titles"] = titles
                    c["caption"] = caption
                    c["hashtags"] = " ".join(hashtags_list) if isinstance(hashtags_list, list) else (hashtags_list or "")
                    snippet = (text[:1500] if text else "") or ""
                    score = _clip_score(snippet)
                    _set_clip_metadata(c["file"], {
                        "titles": titles,
                        "caption": caption,
                        "hashtags": hashtags_list,
                        "source_video_id": c.get("source_video_id", ""),
                        "clip_start": 0,
                        "clip_end": 0,
                        "created_at": created_at,
                        "transcript_snippet": snippet,
                        "score": score,
                    })
                    c["score"] = score
                j["state"] = "done"
                j["stage"] = "done"
                j["progress"] = 100
                j["error"] = None
                j["finished_at"] = datetime.now(timezone.utc).isoformat()
                if exit_code != 0:
                    j["message"] = "Done (pipeline exited non-zero but outputs exist)."
                else:
                    j["message"] = "Done"
                win_scores_path = JOB_DIR / "job.win_scores.json"
                if win_scores_path.is_file():
                    try:
                        data = json.loads(win_scores_path.read_text(encoding="utf-8"))
                        j["win_scores"] = data.get("top_candidates", data)
                    except Exception:
                        j["win_scores"] = None
                else:
                    j["win_scores"] = None
                try:
                    _enqueue_job_clips_for_upload(job_id, JOB_DIR, clips)
                except Exception as eq:
                    _log(f"[SERVER] auto_enqueue error: {eq}")
                try:
                    j["events"].put(None)
                except Exception:
                    pass
            else:
                j["state"] = "error"
                err_default = f"Pipeline exited with code {exit_code}" if exit_code is not None else "Pipeline failed"
                err_from_log = None
                try:
                    if output_log.is_file():
                        lines = output_log.read_text(encoding="utf-8", errors="replace").strip().splitlines()
                        for line in reversed(lines):
                            line = (line or "").strip()
                            if "[ERROR]" in line:
                                err_from_log = line.replace("[ERROR] ", "").strip() or line
                                break
                            if "timed out" in line.lower() or "stalled" in line.lower() or "Download failed" in line:
                                err_from_log = line
                                break
                except Exception:
                    pass
                j["error"] = err_from_log if err_from_log else err_default
                j["message"] = j["error"]
                j["finished_at"] = datetime.now(timezone.utc).isoformat()
                win_scores_path = JOB_DIR / "job.win_scores.json"
                if win_scores_path.is_file():
                    try:
                        data = json.loads(win_scores_path.read_text(encoding="utf-8"))
                        j["win_scores"] = data.get("top_candidates", data)
                    except Exception:
                        j["win_scores"] = None
                else:
                    j["win_scores"] = None
                try:
                    j["events"].put(None)
                except Exception:
                    pass
    except Exception as e:
        j["state"] = "error"
        j["error"] = str(e)
        j["message"] = str(e)
        j["finished_at"] = datetime.now(timezone.utc).isoformat()
        try:
            j["events"].put(None)
        except Exception:
            pass
    except BaseException as e:
        _log(f"[JOB] job_id={job_id} unhandled exception (server will not exit): {e}")
        _log(traceback.format_exc())
        with jobs_lock:
            if job_id in jobs:
                jobs[job_id]["state"] = "error"
                jobs[job_id]["error"] = str(e)
                jobs[job_id]["message"] = str(e)
                jobs[job_id]["finished_at"] = datetime.now(timezone.utc).isoformat()
                try:
                    ev = jobs[job_id].get("events")
                    if ev is not None:
                        ev.put(None)
                except Exception:
                    pass
    finally:
        with jobs_lock:
            if job_id in jobs:
                jobs[job_id]["log_lines"] = list(log_lines)
                if jobs[job_id].get("finished_at") is None and jobs[job_id].get("state") in ("done", "error", "canceled"):
                    jobs[job_id]["finished_at"] = datetime.now(timezone.utc).isoformat()
        _start_next_queued_job()


def _is_valid_youtube_id(video_id: str) -> bool:
    """YouTube IDs are exactly 11 chars, alphanumeric plus - and _."""
    if not video_id or not isinstance(video_id, str):
        return False
    s = video_id.strip()
    if len(s) != 11:
        return False
    return all(c.isalnum() or c in "-_" for c in s)


def _extract_youtube_video_id_ytdlp(url: str) -> str:
    """Extract video ID via yt-dlp (reliable). Returns empty string on failure."""
    if not url or not isinstance(url, str) or not url.strip():
        return ""
    url = url.strip()
    try:
        r = subprocess.run(
            ["yt-dlp", "--print", "id", "--no-warnings", "--skip-download", "--no-playlist", url],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=15,
            encoding="utf-8",
            errors="replace",
        )
        if r.returncode != 0:
            return ""
        out = (r.stdout or "").strip().splitlines()
        return out[0].strip() if out else ""
    except Exception:
        return ""


def _extract_youtube_video_id(url: str) -> str:
    """Extract video ID from YouTube URL (v=, youtu.be/, embed/, shorts/). Returns empty string if not found."""
    if not url or not isinstance(url, str):
        return ""
    url = url.strip()
    # youtu.be/VIDEO_ID
    if "youtu.be/" in url:
        try:
            return url.split("youtu.be/")[1].split("?")[0].split("/")[0].strip() or ""
        except IndexError:
            pass
    # youtube.com/shorts/VIDEO_ID
    if "shorts/" in url:
        try:
            return url.split("shorts/")[1].split("?")[0].split("/")[0].strip() or ""
        except IndexError:
            pass
    # watch?v=VIDEO_ID or embed/VIDEO_ID
    for prefix in ("v=", "embed/"):
        if prefix in url:
            try:
                return url.split(prefix)[1].split("&")[0].split("?")[0].split("/")[0].strip() or ""
            except IndexError:
                pass
    return ""


def _load_tiktok_for_clip(mp4_filename: str, job_id: str = None):
    """Load <stem>.tiktok.json for a clip. If job_id, read from outputs/jobs/<job_id>/clips/; else from OUTPUTS_DIR (legacy flat)."""
    if not mp4_filename or not mp4_filename.endswith(".mp4"):
        return None
    stem = Path(mp4_filename).stem
    if job_id:
        path = JOBS_DIR / job_id / "clips" / (stem + ".tiktok.json")
    else:
        path = OUTPUTS_DIR / (stem + ".tiktok.json")
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        out = {
            "caption": data.get("caption", ""),
            "caption_style": data.get("caption_style", "hook_only"),
            "hashtags": data.get("hashtags", ""),
            "upload_filename": data.get("upload_filename", stem),
            "suggested_title": (data.get("suggested_title") or "").strip() or None,
        }
        if "job_id" in data:
            out["job_id"] = data["job_id"]
        if "source_video_id" in data:
            out["source_video_id"] = data["source_video_id"]
        if "source_url" in data:
            out["source_url"] = data["source_url"]
        return out
    except Exception:
        return None


# Rule-based fallback when Ollama is off or fails. No external APIs.
HOOK_TITLES = [
    "You won't believe what he says here",
    "This moment changed everything",
    "The crowd lost it after this",
    "Wait for the last line",
    "This is actually insane",
]
FALLBACK_CAPTION = "Wait for the ending."
BASE_HASHTAGS = ["#fyp", "#viral", "#shorts", "#tiktok", "#trending", "#clips", "#youtube", "#viral"]


def _extract_hashtag_keywords(text: str, max_words: int = 4) -> list:
    """Extract simple keywords from transcript for hashtags (no API)."""
    if not text or not isinstance(text, str):
        return []
    words = re.findall(r"\b[a-zA-Z]{3,}\b", text.lower())
    stop = {"the", "and", "for", "you", "this", "that", "with", "have", "from", "was", "were", "are", "but", "not", "what", "when", "they", "said", "about", "there", "then"}
    seen = set()
    out = []
    for w in words:
        if w in stop or w in seen or len(out) >= max_words:
            continue
        seen.add(w)
        out.append("#" + w)
    return out


def _fallback_titles_caption_hashtags(transcript_snippet: str):
    """Return (titles list of 5, caption str, hashtags list 8-12). No API."""
    titles = list(HOOK_TITLES)
    caption = FALLBACK_CAPTION
    hashtags = list(BASE_HASHTAGS)
    extra = _extract_hashtag_keywords(transcript_snippet or "", max_words=4)
    for tag in extra:
        if tag not in hashtags and len(hashtags) < 12:
            hashtags.append(tag)
    return titles, caption, hashtags


def _ollama_rewrite_caption(caption: str, transcript_excerpt: str) -> str:
    """Rewrite caption with Ollama (max 80 chars). Returns original on failure or empty response."""
    if not caption or not transcript_excerpt:
        return caption
    try:
        import requests
        r = requests.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "prompt": f"Rewrite this TikTok caption to be punchier, based on the context. Keep under 80 characters. Reply with ONLY the new caption, nothing else.\n\nCurrent caption: {caption}\n\nContext: {transcript_excerpt[:800]}",
                "stream": False,
            },
            timeout=OLLAMA_TIMEOUT_SEC,
        )
        if r.status_code != 200:
            return caption
        out = (r.json().get("response") or "").strip().split("\n")[0].strip()
        return (out[:80] or caption) if out else caption
    except Exception:
        return caption


def _ollama_title_hashtags(text: str):
    """Return (title, hashtags). Uses fallback on timeout, exception, or empty response."""
    fallback_title = (text[:70] + "...") if len(text) > 70 else (text or "Short")
    fallback_hashtags = " ".join(BASE_HASHTAGS[:6])
    try:
        import requests
        r = requests.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "prompt": f"Generate a short punchy title (max 70 characters) and exactly 6 hashtags for this transcript excerpt. Reply with exactly one line in this format: TITLE | #tag1 #tag2 #tag3 #tag4 #tag5 #tag6\n\nExcerpt:\n{text[:1500]}",
                "stream": False,
            },
            timeout=OLLAMA_TIMEOUT_SEC,
        )
        if r.status_code != 200:
            return fallback_title, fallback_hashtags
        out = (r.json().get("response") or "").strip()
        if not out or "|" not in out:
            return fallback_title, fallback_hashtags
        a, b = out.split("|", 1)
        title = a.strip()[:70] or fallback_title
        tags = b.strip()[:200] or fallback_hashtags
        return title, tags
    except Exception:
        return fallback_title, fallback_hashtags


def _generate_titles_caption_hashtags(transcript_snippet: str, use_ollama: bool, initial_caption: str = ""):
    """
    Return (titles: list, caption: str, hashtags: list).
    Tries Ollama when use_ollama; on any failure uses rule-based fallback. Never raises.
    """
    fallback_titles, fallback_caption, fallback_hashtags_list = _fallback_titles_caption_hashtags(transcript_snippet)
    if not use_ollama:
        return fallback_titles, fallback_caption or initial_caption or FALLBACK_CAPTION, fallback_hashtags_list
    title, hashtags_str = _ollama_title_hashtags(transcript_snippet)
    caption = initial_caption or fallback_caption
    if transcript_snippet and initial_caption:
        caption = _ollama_rewrite_caption(initial_caption, transcript_snippet)
    titles = [title] if title else fallback_titles[:1]
    hashtags = [t.strip() for t in hashtags_str.replace("#", " #").split() if t.strip().startswith("#")] if hashtags_str else fallback_hashtags_list
    if not hashtags:
        hashtags = fallback_hashtags_list
    return titles, caption, hashtags


# Version for static assets: avoid stale app.js/styles.css (injected into index.html, used in no-cache routes)
WEB_ASSET_VERSION = "2"


def _get_web_asset_version() -> str:
    """Version string for /web/app.js and /web/styles.css (from runtime.json or constant)."""
    try:
        r = _read_runtime()
        if r:
            return (r.get("started_at") or r.get("version") or WEB_ASSET_VERSION).replace(":", "-")
    except Exception:
        pass
    return WEB_ASSET_VERSION


def _read_runtime() -> dict:
    """Read webapp/runtime.json if present. Returns {} if missing/invalid."""
    if not RUNTIME_JSON.is_file():
        return {}
    try:
        return json.loads(RUNTIME_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {}


_NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


@app.get("/web/app.js")
async def serve_app_js():
    """Serve app.js with no-cache so frontend changes take effect immediately."""
    path = WEB_DIR / "app.js"
    if not path.is_file():
        return JSONResponse(content={"error": "Not found"}, status_code=404)
    return FileResponse(path, media_type="application/javascript", headers=_NO_CACHE_HEADERS)


@app.get("/web/styles.css")
async def serve_styles_css():
    """Serve styles.css with no-cache so frontend changes take effect immediately."""
    path = WEB_DIR / "styles.css"
    if not path.is_file():
        return JSONResponse(content={"error": "Not found"}, status_code=404)
    return FileResponse(path, media_type="text/css", headers=_NO_CACHE_HEADERS)


if WEB_DIR.is_dir():
    app.mount("/web", StaticFiles(directory=str(WEB_DIR)), name="web")
if ASSETS_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")


@app.get("/api/health")
async def api_health():
    """Constant-time: no locks, no jobs dict, no filesystem. Always 200 while server is running."""
    global last_health_ok
    last_health_ok = time.time()
    return {
        "ok": True,
        "server_time": datetime.now(timezone.utc).isoformat(),
        "version": "1.0",
        "pid": os.getpid(),
        "host": HOST,
        "port": PORT,
    }


@app.get("/health")
async def health():
    """Simple health check for Render/lbs: GET /health -> { \"ok\": true }."""
    return {"ok": True}


@app.get("/api/routes")
async def api_routes():
    """List available API routes (path, methods) for debugging."""
    out = []
    for r in getattr(app, "routes", []):
        path = getattr(r, "path", None)
        methods = getattr(r, "methods", None)
        if path is None:
            continue
        if methods is not None:
            out.append({"path": path, "methods": sorted(methods)})
    return {"routes": out}


@app.get("/api/debug/whoami")
async def api_debug_whoami():
    """Prove UI is talking to this backend: pid, cwd, port, version, routes_count."""
    return {
        "pid": os.getpid(),
        "cwd": str(Path.cwd()),
        "port": PORT,
        "version": "1.0",
        "routes_count": len(app.routes),
    }


@app.post("/api/register")
async def api_register(request: Request):
    """Register a new user (plan free). Body: { username, password }. Not for production."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON body required")
    username = (body.get("username") or "").strip()
    password = (body.get("password") or "")
    if not username or not password:
        raise HTTPException(status_code=400, detail="username and password required")
    if username == ADMIN_USERNAME:
        raise HTTPException(status_code=400, detail="Cannot register admin")
    users = _load_users()
    if username in users:
        raise HTTPException(status_code=400, detail="Username already exists")
    users[username] = {"password": password, "plan": PLAN_FREE}
    _save_users(users)
    response = JSONResponse(content={"user": username, "plan": PLAN_FREE})
    response.set_cookie(key=SESSION_COOKIE, value=username, path="/", max_age=86400 * 7, samesite="lax")
    return response


@app.post("/api/login")
async def api_login(request: Request):
    """Login with username/password. Sets session cookie. Body: { username, password }."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON body required")
    username = (body.get("username") or "").strip()
    password = (body.get("password") or "")
    if not username:
        raise HTTPException(status_code=400, detail="username required")
    if not _verify_password(username, password):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    plan = _get_plan(username)
    response = JSONResponse(content={"user": username, "plan": plan})
    response.set_cookie(key=SESSION_COOKIE, value=username, path="/", max_age=86400 * 7, samesite="lax")
    return response


@app.post("/api/logout")
async def api_logout():
    """Clear session cookie."""
    response = JSONResponse(content={"ok": True})
    response.delete_cookie(key=SESSION_COOKIE, path="/")
    return response


@app.get("/api/me")
async def api_me(request: Request):
    """Return current user and plan, or 401 if not logged in."""
    user = await _get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not logged in")
    return {"user": user, "plan": _get_plan(user)}


@app.get("/api/usage")
async def api_usage(request: Request):
    """Usage dashboard: clips and minutes today, limits, remaining. Requires login."""
    user = await get_current_user_required(request)
    plan = _get_plan(user)
    max_clips, max_mins = _plan_limits(plan)
    used = _usage_today(user)
    return {
        "user": user,
        "plan": plan,
        "clips_today": used["clips"],
        "minutes_today": used["minutes_approx"],
        "max_clips_per_day": max_clips,
        "max_minutes_per_day": max_mins,
        "remaining_clips": max(0, max_clips - used["clips"]),
        "remaining_minutes": max(0, max_mins - used["minutes_approx"]),
        "watermark_forced": _plan_force_watermark(plan),
    }


@app.post("/api/dev/reset_limits")
async def api_dev_reset_limits(request: Request):
    """Local dev only: reset daily used counter to 0 for current user. Only works when DEV_DISABLE_LIMITS=1."""
    if not DEV_DISABLE_LIMITS:
        return JSONResponse(
            content={"error": "dev_only", "detail": "POST /api/dev/reset_limits is only available when DEV_DISABLE_LIMITS=1."},
            status_code=403,
        )
    user = await get_current_user_required(request)
    _reset_usage_today(user)
    return {"ok": True, "message": "Daily limits reset to 0 for today."}


@app.get("/api/admin/users")
async def api_admin_list_users(request: Request):
    """List all users and their plans. Admin only."""
    if await get_current_user_required(request) != ADMIN_USERNAME:
        raise HTTPException(status_code=403, detail="Admin only")
    users = _load_users()
    out = {}
    for uname, u in (users or {}).items():
        if isinstance(u, dict):
            out[uname] = {"plan": u.get("plan") or PLAN_FREE}
    out[ADMIN_USERNAME] = {"plan": PLAN_TURBO}
    return {"users": out}


@app.post("/api/admin/users/{username}/plan")
async def api_admin_set_plan(username: str, request: Request):
    """Set plan for a user. Body: { "plan": "free"|"pro"|"turbo" }. Admin only."""
    if await get_current_user_required(request) != ADMIN_USERNAME:
        raise HTTPException(status_code=403, detail="Admin only")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON body required")
    plan = (body.get("plan") or "").strip().lower()
    if plan not in (PLAN_FREE, PLAN_PRO, PLAN_TURBO):
        raise HTTPException(status_code=400, detail="plan must be free, pro, or turbo")
    if username == ADMIN_USERNAME:
        return {"user": username, "plan": plan, "message": "Admin plan not stored (always turbo)"}
    users = _load_users()
    if username not in users or not isinstance(users[username], dict):
        raise HTTPException(status_code=404, detail="User not found")
    users[username]["plan"] = plan
    _save_users(users)
    return {"user": username, "plan": plan}


@app.get("/favicon.ico")
async def favicon():
    """Return 204 so the browser stops requesting favicon and we avoid 404 in logs."""
    return Response(status_code=204)

@app.get("/", response_class=HTMLResponse)
async def index():
    p = WEB_DIR / "index.html"
    if not p.exists():
        return HTMLResponse("<h1>Not found</h1><p>web/index.html missing</p>", status_code=404)
    html = p.read_text(encoding="utf-8")
    # PROD_API_BASE_URL: inject backend URL when deployed (APP_BASE_URL); local keeps 127.0.0.1:8000
    _api_base = (APP_BASE_URL or "http://127.0.0.1:8000").replace("\\", "/")
    html = html.replace("__API_BASE_PLACEHOLDER__", _api_base)
    version = _get_web_asset_version()
    html = html.replace('href="/web/styles.css"', f'href="/web/styles.css?v={version}"')
    html = html.replace('src="/web/app.js"', f'src="/web/app.js?v={version}"')
    return HTMLResponse(html)


@app.post("/api/jobs")
async def create_job(request: Request):
    _log("[API] POST /api/jobs hit")
    try:
        user = await get_current_user_required(request)
        body = await request.json()
        received_url = (body.get("url") or "").strip()
        client_request_id = (body.get("client_request_id") or "").strip() or None
        _log(f"[API] received_url={received_url!r} (len={len(received_url)})")
        max_clips = body.get("max_clips")
        clip_seconds = body.get("clip_seconds")
        use_ollama = bool(body.get("use_ollama", False))
        plan = _get_plan(user)
        watermark = bool(body.get("watermark", False))
        if _plan_force_watermark(plan):
            watermark = True
        planned_clips = max(1, int(max_clips) if max_clips is not None else 6)
        planned_sec = int(clip_seconds) if clip_seconds is not None else 45
        planned_minutes = (planned_clips * planned_sec) / 60.0
        ok, err_msg = _check_quota(user, planned_clips, planned_minutes)
        if not ok:
            return JSONResponse(content={"error": "quota_exceeded", "detail": err_msg}, status_code=403)

        # Idempotency: return existing job if client_request_id matches (same user)
        if client_request_id:
            with jobs_lock:
                for j in jobs.values():
                    if j.get("client_request_id") == client_request_id and j.get("user_id") == user:
                        job_id = j["job_id"]
                        return {
                            "job_id": job_id,
                            "source_url": j.get("source_url", ""),
                            "source_video_id": j.get("source_video_id", ""),
                            "status_url": f"/api/jobs/{job_id}",
                            "events_url": f"/api/jobs/{job_id}/events",
                        }

        # Queue: max 1 concurrent; if any job running, new job is queued (start_immediately=False)
        with jobs_lock:
            any_running = any(j.get("state") == "running" for j in jobs.values())
        start_immediately = not any_running

        try:
            job_id, video_id = _create_job_internal(
                received_url, max_clips, clip_seconds, use_ollama, client_request_id=client_request_id, start_immediately=start_immediately, user_id=user, watermark=watermark
            )
        except ValueError as e:
            err = str(e)
            if err == "missing_url":
                return JSONResponse(content={"error": "missing_url"}, status_code=400)
            _log("[API] rejected: could not extract valid video_id from URL")
            return JSONResponse(
                content={"error": "invalid_url", "detail": err},
                status_code=400,
            )
        return {
            "job_id": job_id,
            "source_url": received_url,
            "source_video_id": video_id,
            "status_url": f"/api/jobs/{job_id}",
            "events_url": f"/api/jobs/{job_id}/events",
        }
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        _log("".join(traceback.format_exc()))
        return JSONResponse(
            content={"error": "job_start_failed", "detail": str(e)},
            status_code=500,
        )


@app.get("/api/jobs")
async def list_jobs():
    """Return all jobs with id, status, created_at, started_at, finished_at, progress, stage, message, source_video_id. Sorted by created_at desc."""
    with jobs_lock:
        out = []
        for j in jobs.values():
            out.append({
                "id": j["job_id"],
                "status": j.get("state", "queued"),
                "created_at": j.get("created_at"),
                "started_at": j.get("started_at"),
                "finished_at": j.get("finished_at"),
                "progress": j.get("progress", 0),
                "stage": j.get("stage", "idle"),
                "message": j.get("message", ""),
                "source_video_id": j.get("source_video_id", ""),
                "source_url": j.get("source_url", ""),
                "error": j.get("error"),
            })
    out.sort(key=lambda x: (x["created_at"] or ""), reverse=True)
    return {"jobs": out}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    with jobs_lock:
        j = jobs.get(job_id)
    if not j:
        return JSONResponse(content={"error": "Job not found"}, status_code=404)
    job_dir = JOBS_DIR / job_id
    job_meta = _load_job_meta(job_dir) if job_dir.is_dir() else {}
    return {
        "job_id": j["job_id"],
        "state": j["state"],
        "stage": j["stage"],
        "progress": j["progress"],
        "message": j["message"],
        "created_at": j.get("created_at"),
        "started_at": j.get("started_at"),
        "finished_at": j.get("finished_at"),
        "clips": j.get("clips", []),
        "error": j.get("error"),
        "log_lines": j.get("log_lines", []),
        "exit_code": j.get("exit_code"),
        "win_scores": j.get("win_scores"),
        "source_url": j.get("source_url", ""),
        "source_video_id": j.get("source_video_id", ""),
        "job_meta": job_meta,
    }


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    """Terminate the job's runner process and mark job as canceled."""
    with jobs_lock:
        j = jobs.get(job_id)
    if not j:
        return JSONResponse(content={"error": "Job not found"}, status_code=404)
    if j.get("state") != "running":
        return JSONResponse(
            content={"error": "not_running", "detail": "Job is not running.", "state": j.get("state")},
            status_code=400,
        )
    proc = None
    with jobs_lock:
        if job_id in jobs and jobs[job_id].get("state") == "running":
            proc = jobs[job_id].pop("_proc", None)
            jobs[job_id]["state"] = "canceled"
            jobs[job_id]["message"] = "Canceled by user"
            jobs[job_id]["error"] = "Canceled"
            jobs[job_id]["finished_at"] = datetime.now(timezone.utc).isoformat()
            ev = jobs[job_id].get("events")
            if ev is not None:
                try:
                    ev.put({"stage": "canceled", "progress": 0, "message": "Canceled by user"})
                    ev.put(None)
                except Exception:
                    pass
    if proc is not None:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    return {"job_id": job_id, "state": "canceled"}


SSE_KEEPALIVE_INTERVAL = 15.0  # seconds


@app.get("/api/jobs/{job_id}/events")
async def job_events(request: Request, job_id: str):
    with jobs_lock:
        j = jobs.get(job_id)
    if not j:
        return JSONResponse(content={"error": "Job not found"}, status_code=404)
    ev = j.get("events")
    if not ev:

        async def empty():
            yield "event: progress\ndata: {}\n\n"

        return StreamingResponse(
            empty(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    import json as _json

    async def _client_disconnected():
        """Check if client disconnected; support both sync and async is_disconnected."""
        fn = getattr(request, "is_disconnected", None)
        if fn is None:
            return False
        try:
            if asyncio.iscoroutinefunction(fn):
                return await fn()
            return bool(fn())
        except Exception:
            return True

    async def stream():
        last_keepalive = time.monotonic()
        try:
            while True:
                try:
                    msg = ev.get_nowait()
                except thread_queue.Empty:
                    await asyncio.sleep(0.5)
                    now = time.monotonic()
                    if now - last_keepalive >= SSE_KEEPALIVE_INTERVAL:
                        yield ": ping\n\n"
                        last_keepalive = now
                    if await _client_disconnected():
                        break
                    continue
                if msg is None:
                    break
                yield f"event: progress\ndata: {_json.dumps(msg)}\n\n"
        except (asyncio.CancelledError, GeneratorExit):
            pass
        finally:
            pass

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


def _load_job_meta(job_dir: Path) -> dict:
    """Load job.meta.json from a job directory. Returns {} if missing/invalid."""
    path = job_dir / "job.meta.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_queue() -> dict:
    """Load batch queue from disk. Returns { items: [], version: 1 }."""
    with queue_lock:
        if not QUEUE_PATH.is_file():
            return {"items": [], "version": 1}
        try:
            data = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
            return {"items": data.get("items", []), "version": data.get("version", 1)}
        except Exception:
            return {"items": [], "version": 1}


def _save_queue(data: dict) -> None:
    """Persist queue to disk."""
    with queue_lock:
        try:
            QUEUE_PATH.write_text(json.dumps({"items": data.get("items", []), "version": data.get("version", 1)}, indent=2), encoding="utf-8")
        except OSError:
            pass


def _start_next_queued_job() -> None:
    """If no job is running, start the next queued job. Turbo users get priority (then by created_at)."""
    with jobs_lock:
        for j in jobs.values():
            if j.get("state") == "running":
                return
        queued = [j for j in jobs.values() if j.get("state") == "queued"]
        if not queued:
            return
        def _queue_priority(j):
            uid = j.get("user_id")
            is_turbo = _get_plan(uid or "") == PLAN_TURBO
            return (0 if is_turbo else 1, j.get("created_at") or "")
        next_job = min(queued, key=_queue_priority)
        job_id = next_job["job_id"]
        url = next_job.get("received_url") or next_job.get("source_url") or ""
        max_clips = next_job.get("max_clips")
        clip_seconds = next_job.get("clip_seconds")
        use_ollama = bool(next_job.get("use_ollama", False))
    t = threading.Thread(target=_run_job, args=(job_id, url, max_clips, clip_seconds, use_ollama))
    t.daemon = True
    t.start()
    _log(f"[JOB] started next queued job_id={job_id}")


def _create_job_internal(
    received_url: str, max_clips=None, clip_seconds=None, use_ollama: bool = False, client_request_id: str | None = None, start_immediately: bool = True, user_id: Optional[str] = None, watermark: bool = False
) -> tuple:
    """Validate URL, create job. If start_immediately and no job running, start _run_job thread; else job stays queued. Returns (job_id, video_id)."""
    received_url = (received_url or "").strip()
    if not received_url:
        raise ValueError("missing_url")
    video_id = _extract_youtube_video_id_ytdlp(received_url)
    if not video_id or not _is_valid_youtube_id(video_id):
        video_id = _extract_youtube_video_id(received_url)
    if not video_id or not _is_valid_youtube_id(video_id):
        raise ValueError("Could not extract video_id from URL. Refusing to continue to prevent reuse.")
    source_path = f"downloads/{video_id}.mp4"
    job_id = uuid.uuid4().hex
    created_at = datetime.now(timezone.utc).isoformat()
    _log(f'[JOB] job_id={job_id} received_url="{received_url}" video_id={video_id} source_path="{source_path}"')
    events = thread_queue.Queue()
    with jobs_lock:
        jobs[job_id] = {
            "job_id": job_id,
            "state": "queued",
            "stage": "idle",
            "progress": 0,
            "message": "Queued",
            "clips": [],
            "error": None,
            "clip_infos": [],
            "use_ollama": use_ollama,
            "events": events,
            "log_lines": [],
            "exit_code": None,
            "source_url": received_url,
            "source_video_id": video_id,
            "received_url": received_url,
            "client_request_id": client_request_id,
            "created_at": created_at,
            "started_at": None,
            "finished_at": None,
            "max_clips": max_clips,
            "clip_seconds": clip_seconds,
            "user_id": user_id,
            "watermark": watermark,
        }
    JOB_DIR = JOBS_DIR / job_id
    (JOB_DIR / "clips").mkdir(parents=True, exist_ok=True)
    meta = {
        "job_id": job_id,
        "source_url": received_url,
        "received_url": received_url,
        "source_video_id": video_id,
        "source_path": source_path,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if client_request_id:
        meta["client_request_id"] = client_request_id
    if user_id:
        meta["user_id"] = user_id
    if watermark:
        meta["watermark"] = True
    try:
        (JOB_DIR / "job.meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    except OSError:
        pass
    if start_immediately:
        _start_next_queued_job()
    return (job_id, video_id)


def _queue_worker():
    """Single-threaded worker: process first pending queue item, then repeat. Never raises."""
    while True:
        try:
            data = _load_queue()
            items = data.get("items", [])
            pending_index = None
            for i, it in enumerate(items):
                if it.get("status") == "pending":
                    pending_index = i
                    break
            if pending_index is None:
                time.sleep(2)
                continue
            item = items[pending_index]
            item["status"] = "running"
            _save_queue(data)
            url = (item.get("url") or "").strip()
            try:
                job_id, video_id = _create_job_internal(url)
                item["job_id"] = job_id
                item["video_id"] = video_id
                _save_queue(data)
            except Exception as e:
                item["status"] = "error"
                item["error"] = str(e)
                _save_queue(data)
                continue
            while True:
                with jobs_lock:
                    j = jobs.get(job_id)
                state = j.get("state") if j else None
                if state in ("done", "error"):
                    break
                time.sleep(1)
            data = _load_queue()
            items = data.get("items", [])
            if pending_index < len(items) and items[pending_index].get("job_id") == job_id:
                items[pending_index]["status"] = "done" if state == "done" else "error"
                items[pending_index]["error"] = (j.get("error") or None) if state == "error" else None
            _save_queue(data)
        except BaseException as e:
            _log(f"[QUEUE] worker unhandled exception (server will not exit): {e}")
            _log(traceback.format_exc())
            time.sleep(2)


def _watchdog_worker():
    """Every 30s, mark running jobs with no output for 60s as stalled; terminate subprocess to unblock server."""
    while True:
        try:
            time.sleep(30)
            now = time.time()
            procs_to_terminate = []
            with jobs_lock:
                for job_id, j in list(jobs.items()):
                    if j.get("state") != "running":
                        continue
                    last = j.get("last_progress_at")
                    if last is not None and (now - last) > 60:
                        j["state"] = "error"
                        j["error"] = "Job stalled (no output for 60s)"
                        j["message"] = j["error"]
                        proc = j.get("_proc")
                        if proc is not None:
                            procs_to_terminate.append(proc)
                            j.pop("_proc", None)
                        try:
                            ev = j.get("events")
                            if ev is not None:
                                ev.put(None)
                        except Exception:
                            pass
                        _log(f"[WATCHDOG] job_id={job_id} marked stalled (no output for 60s)")
            for proc in procs_to_terminate:
                try:
                    proc.terminate()
                    proc.wait(timeout=10)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
        except BaseException as e:
            _log(f"[WATCHDOG] exception: {e}")
            _log(traceback.format_exc())


_queue_worker_thread = threading.Thread(target=_queue_worker, daemon=True)
_queue_worker_thread.start()
_watchdog_thread = threading.Thread(target=_watchdog_worker, daemon=True)
_watchdog_thread.start()


@app.get("/api/queue")
async def get_queue():
    """Return current batch queue (items with url, video_id, job_id, status, error)."""
    return _load_queue()


@app.post("/api/queue/add")
async def queue_add(request: Request):
    """Add URLs to the queue (one per line or JSON array). Validate each; invalid URLs get status 'error' and message."""
    try:
        body = await request.json()
        urls = body.get("urls") or []
        if isinstance(urls, str):
            urls = [u.strip() for u in urls.splitlines() if u.strip()]
        else:
            urls = [(u or "").strip() for u in urls if (u or "").strip()]
    except Exception:
        urls = []
    data = _load_queue()
    items = data.get("items", [])
    now = datetime.now(timezone.utc).isoformat()
    for raw in urls:
        url = (raw or "").strip()
        if not url:
            continue
        video_id = _extract_youtube_video_id_ytdlp(url)
        if not video_id or not _is_valid_youtube_id(video_id):
            items.append({
                "url": url,
                "video_id": "",
                "job_id": None,
                "status": "error",
                "error": "Could not extract video_id from URL.",
                "created_at": now,
            })
        else:
            items.append({
                "url": url,
                "video_id": video_id,
                "job_id": None,
                "status": "pending",
                "error": None,
                "created_at": now,
            })
    data["items"] = items
    _save_queue(data)
    return _load_queue()


@app.post("/api/queue/retry")
async def queue_retry(request: Request):
    """Set item at index to pending and clear error/job_id so worker will pick it up."""
    try:
        body = await request.json()
        index = int(body.get("index", -1))
    except Exception:
        index = -1
    if index < 0:
        return JSONResponse(content={"error": "invalid_index"}, status_code=400)
    data = _load_queue()
    items = data.get("items", [])
    if index >= len(items):
        return JSONResponse(content={"error": "index_out_of_range"}, status_code=400)
    items[index]["status"] = "pending"
    items[index]["error"] = None
    items[index]["job_id"] = None
    _save_queue(data)
    return _load_queue()


@app.delete("/api/clips/all")
async def delete_all_clips():
    """Delete all clip files (mp4 + sidecars) in every job's clips/ folder. Does not delete job folders or server.log.
    Locked files are skipped after retries and returned in skipped_in_use."""
    deleted_clips = 0
    skipped_in_use = []
    if not JOBS_DIR.is_dir():
        return {"ok": True, "deleted_clips": 0, "deleted_jobs": 0, "skipped_in_use": []}
    try:
        for job_dir in list(JOBS_DIR.iterdir()):
            if not job_dir.is_dir():
                continue
            clips_dir = job_dir / "clips"
            if not clips_dir.is_dir():
                continue
            for f in clips_dir.glob("*.mp4"):
                if not f.is_file():
                    continue
                stem = f.stem
                for ext in ("", ".tiktok.json", ".subs.json", ".meta.json", ".srt"):
                    p = clips_dir / (stem + ext) if ext else f
                    if p.is_file():
                        if _unlink_with_retry(p):
                            if p.suffix == ".mp4":
                                deleted_clips += 1
                        else:
                            skipped_in_use.append(str(p))
    except (PermissionError, OSError) as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)
    return {"ok": True, "deleted_clips": deleted_clips, "deleted_jobs": 0, "skipped_locked": len(skipped_in_use), "skipped_in_use": skipped_in_use}


_clips_cache: dict = {"clips": [], "jobs_meta": {}}
_clips_cache_lock = threading.Lock()


_LIB_SCAN_SUBFOLDERS = ("clips", "renders", "out", "output")


def _scan_clips() -> dict:
    """Scan outputs/jobs and outputs/renders for clips. Bounded depth, no recursive glob. Safe on Windows locks."""
    t0 = time.time()
    print(f"[LIB_SCAN] start jobs_dir={JOBS_DIR.resolve()!s} renders_dir={RENDERS_DIR.resolve()!s}", flush=True)
    out = []
    jobs_meta = {}
    skipped_locked = 0
    skipped_errors = 0
    if not JOBS_DIR.is_dir():
        print(f"[LIB_SCAN] done count=0 ms={int((time.time() - t0) * 1000)} skipped_locked=0 skipped_errors=0", flush=True)
        return {"clips": out, "jobs_meta": jobs_meta}
    try:
        job_dirs = [p for p in JOBS_DIR.iterdir() if p.is_dir()]
    except (OSError, PermissionError):
        skipped_errors += 1
        print(f"[LIB_SCAN] done count=0 ms={int((time.time() - t0) * 1000)} skipped_locked={skipped_locked} skipped_errors={skipped_errors}", flush=True)
        return {"clips": out, "jobs_meta": jobs_meta}

    def _dir_mtime(p):
        try:
            return p.stat().st_mtime if p.is_dir() else 0
        except (OSError, PermissionError):
            return 0

    try:
        job_dirs_sorted = sorted(job_dirs, key=_dir_mtime, reverse=True)
    except Exception:
        job_dirs_sorted = job_dirs

    for job_dir in job_dirs_sorted:
        if not job_dir.is_dir():
            continue
        try:
            job_id = job_dir.name
            try:
                job_meta = _load_job_meta(job_dir)
            except Exception:
                job_meta = {}
            jobs_meta[job_id] = job_meta
            for sub in _LIB_SCAN_SUBFOLDERS:
                subdir = job_dir / sub
                try:
                    if not subdir.is_dir():
                        continue
                except (OSError, PermissionError):
                    continue
                try:
                    files = list(subdir.glob("*.mp4"))
                except (OSError, PermissionError):
                    continue
                for f in files:
                    try:
                        if not f.is_file():
                            continue
                    except (OSError, PermissionError):
                        skipped_locked += 1
                        continue
                    try:
                        st = f.stat()
                    except (OSError, PermissionError):
                        skipped_locked += 1
                        continue
                    c = {
                        "file": f.name,
                        "job_id": job_id,
                        "url": f"/outputs/jobs/{job_id}/{sub}/{f.name}",
                        "mtime": st.st_mtime,
                        "size": st.st_size,
                    }
                    try:
                        tt = _load_tiktok_for_clip(f.name, job_id=job_id)
                    except Exception:
                        tt = None
                        skipped_errors += 1
                    c["caption"] = (tt or {}).get("caption") or "You won't believe this"
                    c["caption_style"] = (tt or {}).get("caption_style") or "hook_only"
                    c["hashtags"] = (tt or {}).get("hashtags") or ""
                    c["upload_filename"] = (tt or {}).get("upload_filename") or Path(f.name).stem
                    if (tt or {}).get("suggested_title"):
                        c["title"] = (tt or {}).get("suggested_title")
                    c["source_video_id"] = (tt or {}).get("source_video_id") or job_meta.get("source_video_id", "")
                    c["source_url"] = (tt or {}).get("source_url") or job_meta.get("source_url", "")
                    try:
                        meta = _get_clip_metadata(f.name)
                    except Exception:
                        meta = {}
                        skipped_errors += 1
                    if meta:
                        if meta.get("titles"):
                            c["titles"] = meta["titles"] if isinstance(meta["titles"], list) else []
                        if meta.get("caption") not in (None, ""):
                            c["caption"] = meta["caption"]
                        if meta.get("hashtags") is not None:
                            c["hashtags"] = " ".join(meta["hashtags"]) if isinstance(meta["hashtags"], list) else (meta["hashtags"] or "")
                        if meta.get("transcript_snippet") is not None:
                            c["transcript_snippet"] = meta["transcript_snippet"]
                        if meta.get("score") is not None:
                            c["score"] = int(meta["score"]) if isinstance(meta["score"], (int, float)) else None
                    if "titles" not in c:
                        c["titles"] = []
                    if "score" not in c:
                        c["score"] = None
                    subs_json = subdir / (Path(f.name).stem + ".subs.json")
                    try:
                        if subs_json.is_file():
                            telem = json.loads(subs_json.read_text(encoding="utf-8"))
                            act = telem.get("subtitle_action", "") or (telem.get("final_action") == "burn" and "burned" or "skipped")
                            c["subtitle_action"] = "burn" if act in ("burn", "burned") else "skip"
                            bd = telem.get("baked_detection") or {}
                            score = bd.get("score") if bd.get("score") is not None else telem.get("confidence")
                            c["baked_confidence"] = score
                            c["subs_status"] = "burned" if act in ("burn", "burned") else "skipped"
                            c["subs_reason"] = bd.get("status", "uncertain")
                            c["subs_score"] = score
                            if telem.get("initial_action") is not None:
                                c["subs_initial_action"] = telem["initial_action"]
                            if telem.get("verify") is not None:
                                c["subs_verify"] = telem["verify"]
                            if telem.get("fallback") is not None:
                                c["subs_fallback"] = telem["fallback"]
                            if telem.get("final_action") is not None:
                                c["subs_final_action"] = telem["final_action"]
                    except Exception:
                        skipped_errors += 1
                    out.append(c)
        except (OSError, PermissionError):
            skipped_errors += 1

    if RENDERS_DIR.is_dir():
        try:
            for f in RENDERS_DIR.glob("*.mp4"):
                try:
                    if not f.is_file():
                        continue
                    st = f.stat()
                except (OSError, PermissionError):
                    skipped_locked += 1
                    continue
                out.append({
                    "file": f.name,
                    "job_id": "reddit",
                    "url": f"/outputs/renders/{f.name}",
                    "mtime": st.st_mtime,
                    "size": st.st_size,
                    "caption": "Reddit story video",
                    "caption_style": "hook_only",
                    "hashtags": "",
                    "upload_filename": Path(f.name).stem,
                    "source_video_id": "",
                    "source_url": "",
                    "titles": [],
                    "score": None,
                })
        except (OSError, PermissionError):
            skipped_errors += 1

    out.sort(key=lambda x: x["mtime"], reverse=True)
    elapsed_ms = int((time.time() - t0) * 1000)
    print(f"[LIB_SCAN] done count={len(out)} ms={elapsed_ms}", flush=True)
    print(f"[LIB_SCAN] skipped_locked={skipped_locked} skipped_errors={skipped_errors}", flush=True)
    return {"clips": out, "jobs_meta": jobs_meta}


@app.get("/api/clips")
async def list_clips(scan: Optional[str] = None):
    """Return clips. scan=1 or scan=true triggers filesystem scan (Library tab); otherwise returns cached."""
    do_scan = str(scan or "").strip().lower() in ("1", "true", "yes")
    if do_scan:
        _log("[API] GET /api/clips?scan=1 (scanning)")
        _log(f"[API] JOBS_DIR={JOBS_DIR.resolve()!s} exists={JOBS_DIR.is_dir()}")
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, _scan_clips)
        nc = len(data.get("clips") or [])
        _log(f"[API] scan result: {nc} clips")
        with _clips_cache_lock:
            _clips_cache["clips"] = data["clips"]
            _clips_cache["jobs_meta"] = data["jobs_meta"]
        data["scan_dir_jobs"] = str(JOBS_DIR.resolve())
        data["scan_dir_renders"] = str(RENDERS_DIR.resolve())
        if nc == 0 and RENDERS_DIR.is_dir():
            try:
                data["renders_dir_sample"] = [f.name for f in RENDERS_DIR.glob("*.mp4") if f.is_file()][:5]
            except OSError:
                data["renders_dir_sample"] = []
        else:
            data["renders_dir_sample"] = []
        data["library_debug"] = os.environ.get("LIBRARY_DEBUG", "").strip() == "1"
        return JSONResponse(content=data, headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"})
    with _clips_cache_lock:
        out = {"clips": list(_clips_cache["clips"]), "jobs_meta": dict(_clips_cache["jobs_meta"])}
        out["scan_dir_jobs"] = str(JOBS_DIR.resolve())
        out["scan_dir_renders"] = str(RENDERS_DIR.resolve())
        out["library_debug"] = os.environ.get("LIBRARY_DEBUG", "").strip() == "1"
        return JSONResponse(content=out, headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"})


@app.post("/api/post-pack")
async def post_pack(request: Request):
    """
    Build a ZIP with selected clips (mp4) and post_pack.csv from metadata.
    Body: { "filenames": ["clip1.mp4", "clip2.mp4", ...] }.
    Returns ZIP download; temp file is deleted after stream.
    """
    try:
        body = await request.json()
        filenames = body.get("filenames") or []
    except Exception:
        return JSONResponse(content={"error": "Invalid JSON body"}, status_code=400)
    if not isinstance(filenames, list) or len(filenames) == 0:
        return JSONResponse(content={"error": "filenames must be a non-empty array"}, status_code=400)
    resolved = []
    for f in filenames:
        if not isinstance(f, str):
            continue
        path = _resolve_clip_path(f.strip())
        if path is not None:
            resolved.append((f.strip(), path))
    if not resolved:
        return JSONResponse(content={"error": "No valid clip filenames found under outputs/jobs"}, status_code=400)
    meta = _load_metadata()
    fd, zip_path = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for filename, path in resolved:
                zf.write(path, arcname=filename)
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow([
                "filename", "title_1", "title_2", "title_3", "title_4", "title_5",
                "caption", "hashtags", "source_video_id", "clip_start", "clip_end", "created_at"
            ])
            for filename, _ in resolved:
                row_meta = meta.get(filename, {})
                titles = row_meta.get("titles") or []
                if not isinstance(titles, list):
                    titles = []
                title_1 = titles[0] if len(titles) > 0 else ""
                title_2 = titles[1] if len(titles) > 1 else ""
                title_3 = titles[2] if len(titles) > 2 else ""
                title_4 = titles[3] if len(titles) > 3 else ""
                title_5 = titles[4] if len(titles) > 4 else ""
                caption = row_meta.get("caption") or ""
                hashtags_raw = row_meta.get("hashtags")
                hashtags = " ".join(hashtags_raw) if isinstance(hashtags_raw, list) else (hashtags_raw or "")
                source_video_id = row_meta.get("source_video_id") or ""
                clip_start = row_meta.get("clip_start", 0)
                clip_end = row_meta.get("clip_end", 0)
                created_at = row_meta.get("created_at") or ""
                writer.writerow([
                    filename, title_1, title_2, title_3, title_4, title_5,
                    caption, hashtags, source_video_id, clip_start, clip_end, created_at
                ])
            buf.seek(0)
            zf.writestr("post_pack.csv", buf.getvalue())
    except Exception as e:
        try:
            os.unlink(zip_path)
        except OSError:
            pass
        return JSONResponse(content={"error": "Failed to build zip", "detail": str(e)}, status_code=500)

    def stream_and_cleanup():
        with open(zip_path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                yield chunk
        try:
            os.unlink(zip_path)
        except OSError:
            pass

    return StreamingResponse(
        stream_and_cleanup(),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=post_pack.zip"}
    )


@app.post("/api/rename-clip")
async def rename_clip(request: Request):
    """
    Rename a clip file and migrate its metadata.json entry.
    Body: { "old": "old.mp4", "new": "new.mp4" }.
    New filename is validated (slug-safe, max 60 chars). Returns 409 if new already exists.
    """
    try:
        body = await request.json()
        old_name = (body.get("old") or "").strip()
        new_name_raw = (body.get("new") or "").strip()
    except Exception:
        return JSONResponse(content={"error": "Invalid JSON body"}, status_code=400)
    if not old_name:
        return JSONResponse(content={"error": "Missing 'old' filename"}, status_code=400)
    if not old_name.endswith(".mp4"):
        return JSONResponse(content={"error": "Old filename must end with .mp4"}, status_code=400)
    old_path = _resolve_clip_path(old_name)
    if old_path is None:
        return JSONResponse(content={"error": "Clip not found"}, status_code=404)
    new_name = _safe_new_filename(new_name_raw)
    if new_name is None:
        return JSONResponse(content={"error": "Invalid new filename (use slug: lowercase, hyphens, max 60 chars)"}, status_code=400)
    clips_dir = old_path.parent
    new_path = clips_dir / new_name
    if new_path.exists():
        return JSONResponse(content={"error": "A file with that name already exists", "detail": new_name}, status_code=409)
    old_stem = old_path.stem
    new_stem = new_path.stem
    sidecar_exts = [".tiktok.json", ".subs.json", ".meta.json", ".srt"]
    try:
        old_path.rename(new_path)
        for ext in sidecar_exts:
            sidecar = clips_dir / (old_stem + ext)
            if sidecar.is_file():
                sidecar.rename(clips_dir / (new_stem + ext))
        _migrate_metadata_key(old_name, new_name)
    except OSError as e:
        return JSONResponse(content={"error": "Rename failed", "detail": str(e)}, status_code=500)
    return {"ok": True, "new": new_name}


# ---- TIKTOK_PHASE_1_AUTH / TIKTOK_PHASE_1_SINGLE_POST: Content Posting API Direct Post flow ----

# TikTok OAuth and API base URLs (official)
_TIKTOK_AUTH_BASE = "https://www.tiktok.com/v2/auth/authorize"
_TIKTOK_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token"
_TIKTOK_API_BASE = "https://open.tiktokapis.com/v2"
_TIKTOK_SCOPES = "user.info.basic,video.upload,video.publish"


def _tiktok_setup_payload() -> dict:
    """TIKTOK_SETUP_HELPER: Build setup JSON for /api/tiktok/setup. Used by setup endpoint and frontend."""
    missing = []
    if not TIKTOK_CLIENT_KEY:
        missing.append("TIKTOK_CLIENT_KEY")
    if not TIKTOK_CLIENT_SECRET:
        missing.append("TIKTOK_CLIENT_SECRET")
    if not TIKTOK_REDIRECT_URI:
        missing.append("APP_BASE_URL or TIKTOK_REDIRECT_URI")
    portal_prefix = APP_BASE_URL or ""
    redirect_uri = TIKTOK_REDIRECT_URI or ""
    return {
        "client_key_present": bool(TIKTOK_CLIENT_KEY),
        "client_secret_present": bool(TIKTOK_CLIENT_SECRET),
        "app_base_url": portal_prefix,
        "redirect_uri": redirect_uri,
        "portal_url_prefix": portal_prefix,
        "portal_redirect_uri": redirect_uri,
        "missing": missing,
    }


@app.get("/api/tiktok/setup")
async def tiktok_setup():
    """TIKTOK_SETUP_HELPER: Setup visibility only. Returns values to paste into TikTok Developer Portal."""
    return JSONResponse(content=_tiktok_setup_payload())


@app.get("/api/tiktok/connect")
async def tiktok_connect():
    """Redirect user to TikTok OAuth authorization URL. Uses computed redirect_uri from config."""
    if not TIKTOK_CLIENT_KEY or not TIKTOK_REDIRECT_URI:
        print("[TIKTOK_AUTH] connect skipped: TIKTOK_CLIENT_KEY or TIKTOK_REDIRECT_URI not set", flush=True)
        return JSONResponse(
            content={
                "error": "TikTok not configured",
                "detail": "TikTok not configured. Set TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET, and APP_BASE_URL.",
            },
            status_code=503,
        )
    state = hashlib.sha256(os.urandom(32)).hexdigest()[:32]
    params = {
        "client_key": TIKTOK_CLIENT_KEY,
        "scope": _TIKTOK_SCOPES,
        "response_type": "code",
        "redirect_uri": TIKTOK_REDIRECT_URI,
        "state": state,
    }
    from urllib.parse import urlencode
    url = _TIKTOK_AUTH_BASE + "?" + urlencode(params)
    print(f"[TIKTOK_AUTH] redirecting to TikTok authorize (state={state[:8]}...)", flush=True)
    return RedirectResponse(url=url, status_code=302)


@app.get("/api/tiktok/callback")
async def tiktok_callback(code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None):
    """Receive OAuth callback; exchange code for tokens; store in phase-1 local storage."""
    if error:
        print(f"[TIKTOK_AUTH] callback error from TikTok: {error}", flush=True)
        return RedirectResponse(url="/#library", status_code=302)
    if not code:
        print("[TIKTOK_AUTH] callback missing code", flush=True)
        return RedirectResponse(url="/#library", status_code=302)
    if not TIKTOK_CLIENT_KEY or not TIKTOK_CLIENT_SECRET or not TIKTOK_REDIRECT_URI:
        print("[TIKTOK_AUTH] callback: app not configured. Set TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET, and APP_BASE_URL.", flush=True)
        return RedirectResponse(url="/#library", status_code=302)
    import requests
    try:
        r = requests.post(
            _TIKTOK_TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "client_key": TIKTOK_CLIENT_KEY,
                "client_secret": TIKTOK_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": TIKTOK_REDIRECT_URI,
            },
            timeout=15,
        )
        data = r.json() if r.text else {}
        if r.status_code != 200:
            print(f"[TIKTOK_AUTH] token exchange failed status={r.status_code} body={data}", flush=True)
            return RedirectResponse(url="/#library", status_code=302)
        access_token = (data.get("data") or {}).get("access_token") or data.get("access_token")
        refresh_token = (data.get("data") or {}).get("refresh_token") or data.get("refresh_token")
        expires_in = (data.get("data") or {}).get("expires_in") or data.get("expires_in")
        if not access_token:
            print("[TIKTOK_AUTH] token response missing access_token", flush=True)
            return RedirectResponse(url="/#library", status_code=302)
        # Phase-1 local storage: store only what is needed
        stored = {
            "access_token": access_token,
            "refresh_token": refresh_token or "",
            "expires_in": int(expires_in) if expires_in is not None else 86400,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        _tiktok_save_tokens(stored)
        print("[TIKTOK_AUTH] tokens saved successfully", flush=True)
    except Exception as e:
        print(f"[TIKTOK_AUTH] token exchange exception: {e}", flush=True)
        traceback.print_exc()
        return RedirectResponse(url="/#library", status_code=302)
    return RedirectResponse(url="/#library", status_code=302)


@app.get("/api/tiktok/status")
async def tiktok_status():
    """Return whether TikTok is connected and basic non-sensitive account info if available."""
    tokens = _tiktok_load_tokens()
    if not tokens or not tokens.get("access_token"):
        return JSONResponse(content={"connected": False, "message": "TikTok not connected"})
    # Optionally fetch display name from creator_info without failing status
    connected = True
    account_info = {}
    try:
        import requests
        r = requests.post(
            f"{_TIKTOK_API_BASE}/user/info/",
            headers={
                "Authorization": f"Bearer {tokens['access_token']}",
                "Content-Type": "application/json",
            },
            json={"user": {"fields": ["display_name", "avatar_url"]}},
            timeout=10,
        )
        if r.status_code == 200:
            d = r.json()
            user = (d.get("data") or {}).get("user") or {}
            if user:
                account_info = {"display_name": user.get("display_name") or "", "avatar_url": user.get("avatar_url") or ""}
    except Exception as e:
        print(f"[TIKTOK_AUTH] status creator info optional: {e}", flush=True)
    return JSONResponse(content={"connected": connected, "account": account_info})


@app.get("/api/tiktok/creator_info")
async def tiktok_creator_info():
    """Use authorized token to query creator info. Confirms account is usable and returns post constraints."""
    tokens = _tiktok_load_tokens()
    if not tokens or not tokens.get("access_token"):
        print("[TIKTOK_CREATOR_INFO] no token", flush=True)
        return JSONResponse(content={"error": "TikTok not connected"}, status_code=401)
    try:
        import requests
        r = requests.post(
            f"{_TIKTOK_API_BASE}/user/info/",
            headers={
                "Authorization": f"Bearer {tokens['access_token']}",
                "Content-Type": "application/json",
            },
            json={"user": {"fields": ["open_id", "union_id", "display_name", "avatar_url"]}},
            timeout=10,
        )
        data = r.json() if r.text else {}
        if r.status_code != 200:
            print(f"[TIKTOK_CREATOR_INFO] API error status={r.status_code} body={data}", flush=True)
            return JSONResponse(
                content={"error": "Creator info failed", "detail": data.get("message") or data.get("error") or str(data)},
                status_code=r.status_code,
            )
        print("[TIKTOK_CREATOR_INFO] success", flush=True)
        return JSONResponse(content=data)
    except Exception as e:
        print(f"[TIKTOK_CREATOR_INFO] exception: {e}", flush=True)
        traceback.print_exc()
        return JSONResponse(content={"error": "Creator info failed", "detail": str(e)}, status_code=500)


@app.post("/api/tiktok/post_clip")
async def tiktok_post_clip(request: Request):
    """TIKTOK_PHASE_1_SINGLE_POST: One real clip post via TikTok Direct Post. FUTURE_BULK_AUTPOST_QUEUE in phase 2."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(content={"error": "Invalid JSON body"}, status_code=400)
    clip_path_arg = (body.get("clip_path") or body.get("clip_id") or "").strip()
    if not clip_path_arg:
        return JSONResponse(content={"error": "Missing clip_path or clip_id"}, status_code=400)
    # Accept filename only (e.g. "my-clip.mp4") or path; resolve to file
    filename = clip_path_arg if clip_path_arg.endswith(".mp4") else (clip_path_arg.strip("/").split("/")[-1] or clip_path_arg)
    if not filename.endswith(".mp4"):
        filename = filename + ".mp4"
    path = _resolve_clip_path(filename)
    if path is None:
        return JSONResponse(content={"error": "Clip not found", "detail": filename}, status_code=404)
    title = (body.get("title") or body.get("caption") or "").strip() or path.stem
    privacy_level = (body.get("privacy_level") or "PUBLIC_TO_EVERYONE").strip() or "PUBLIC_TO_EVERYONE"
    tokens = _tiktok_load_tokens()
    if not tokens or not tokens.get("access_token"):
        return JSONResponse(content={"error": "TikTok not connected"}, status_code=401)

    # Confirm account is usable and get post constraints (log errors clearly)
    import requests as _req
    try:
        r_ci = _req.post(
            f"{_TIKTOK_API_BASE}/user/info/",
            headers={"Authorization": f"Bearer {tokens['access_token']}", "Content-Type": "application/json"},
            json={"user": {"fields": ["display_name"]}},
            timeout=10,
        )
        if r_ci.status_code != 200:
            print(f"[TIKTOK_CREATOR_INFO] pre-post check failed status={r_ci.status_code}", flush=True)
        else:
            print("[TIKTOK_CREATOR_INFO] pre-post check ok", flush=True)
    except Exception as e:
        print(f"[TIKTOK_CREATOR_INFO] pre-post check exception: {e}", flush=True)

    print(f"[TIKTOK_POST_INIT] clip={filename} title_len={len(title)} privacy={privacy_level}", flush=True)
    import requests
    try:
        # Step 1: Initialize direct post (get upload URL)
        init_url = f"{_TIKTOK_API_BASE}/post/publish/inbox/video/init/"
        init_body = {
            "post_info": {
                "title": title[:150],
                "privacy_level": privacy_level,
                "disable_duet": False,
                "disable_comment": False,
            },
            "source_info": {"source": "FILE_UPLOAD"},
        }
        r_init = requests.post(
            init_url,
            headers={
                "Authorization": f"Bearer {tokens['access_token']}",
                "Content-Type": "application/json",
            },
            json=init_body,
            timeout=30,
        )
        init_data = r_init.json() if r_init.text else {}
        if r_init.status_code != 200:
            print(f"[TIKTOK_POST_ERROR] init failed status={r_init.status_code} body={init_data}", flush=True)
            return JSONResponse(
                content={
                    "success": False,
                    "error": "TikTok init failed",
                    "detail": init_data.get("message") or init_data.get("error") or str(init_data),
                },
                status_code=r_init.status_code,
            )
        upload_url = (init_data.get("data") or {}).get("upload_url") or init_data.get("upload_url")
        publish_id = (init_data.get("data") or {}).get("publish_id") or init_data.get("publish_id")
        if not upload_url:
            print(f"[TIKTOK_POST_ERROR] init response missing upload_url body={init_data}", flush=True)
            return JSONResponse(
                content={"success": False, "error": "TikTok init missing upload_url", "detail": str(init_data)},
                status_code=502,
            )
        print("[TIKTOK_POST_UPLOAD] uploading file to TikTok", flush=True)
        # Step 2: Upload video binary to upload_url
        with open(path, "rb") as f:
            video_bytes = f.read()
        r_upload = requests.put(upload_url, data=video_bytes, headers={"Content-Type": "video/mp4"}, timeout=120)
        if r_upload.status_code not in (200, 201, 204):
            print(f"[TIKTOK_POST_ERROR] upload failed status={r_upload.status_code}", flush=True)
            return JSONResponse(
                content={
                    "success": False,
                    "error": "TikTok upload failed",
                    "detail": f"HTTP {r_upload.status_code}",
                },
                status_code=502,
            )
        print("[TIKTOK_POST_RESULT] upload completed successfully", flush=True)
        return JSONResponse(
            content={
                "success": True,
                "message": "Posted successfully",
                "publish_id": publish_id,
                "detail": "Video uploaded to TikTok.",
            },
        )
    except Exception as e:
        print(f"[TIKTOK_POST_ERROR] exception: {e}", flush=True)
        traceback.print_exc()
        return JSONResponse(
            content={"success": False, "error": "Post failed", "detail": str(e)},
            status_code=500,
        )


# ---- TTS: /api/tts disabled; /api/tts_offline uses Piper (offline neural) ----

TTS_DIR = OUTPUTS_DIR / "tts"
SUBS_DIR = OUTPUTS_DIR / "subs"
RENDERS_DIR = OUTPUTS_DIR / "renders"
TTS_SAMPLES_DIR = OUTPUTS_DIR / "tts_samples"
REDDIT_CACHE_DIR = OUTPUTS_DIR / "cache"
OVERLAYS_DIR = OUTPUTS_DIR / "overlays"
GAMEPLAY_DIR = ASSETS_DIR / "gameplay" if ASSETS_DIR else Path()

REDDIT_TAG_ENABLED = (os.environ.get("REDDIT_TAG_ENABLED") or "1").strip().lower() in ("1", "true", "yes")
REDDIT_TAG_HANDLE = (os.environ.get("REDDIT_TAG_HANDLE") or "u/RedditStories").strip() or "u/RedditStories"
REDDIT_TAG_SUBREDDIT = (os.environ.get("REDDIT_TAG_SUBREDDIT") or "").strip()
REDDIT_TAG_UPVOTES = (os.environ.get("REDDIT_TAG_UPVOTES") or "1.2K+").strip()[:16]
REDDIT_TAG_COMMENTS = (os.environ.get("REDDIT_TAG_COMMENTS") or "450+").strip()[:16]
REDDIT_TAG_DURATION = max(0, min(120, float(os.environ.get("REDDIT_TAG_DURATION") or "12")))


def _reddit_cache_hash(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:24]


def _reddit_cache_enhance_get(story_text: str) -> Optional[dict]:
    """Return cached enhance JSON if present and valid. Optional; on failure return None."""
    try:
        key = _reddit_cache_hash(story_text)
        path = REDDIT_CACHE_DIR / "enhance" / f"{key}.json"
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("narration_script"):
                return data
    except Exception:
        pass
    return None


def _reddit_cache_enhance_set(story_text: str, data: dict) -> None:
    try:
        REDDIT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (REDDIT_CACHE_DIR / "enhance").mkdir(parents=True, exist_ok=True)
        key = _reddit_cache_hash(story_text)
        (REDDIT_CACHE_DIR / "enhance" / f"{key}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _reddit_cache_tts_get(script: str, render_id: str) -> Optional[Tuple[Path, float, bool]]:
    """If cached TTS exists: return (audio_path, duration, used_normalized). Copies to TTS_DIR under render_id. Else None."""
    try:
        key = _reddit_cache_hash(script)
        cache_dir = REDDIT_CACHE_DIR / "tts"
        mp3_cached = cache_dir / f"{key}.mp3"
        wav_cached = cache_dir / f"{key}_normalized.wav"
        if wav_cached.is_file():
            dest_wav = TTS_DIR / f"{render_id}_normalized.wav"
            TTS_DIR.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copy2(wav_cached, dest_wav)
            dur = _get_audio_duration_ffprobe(dest_wav)
            if dur and dur > 0:
                return (dest_wav, dur, True)
        if mp3_cached.is_file():
            dest_mp3 = TTS_DIR / f"{render_id}.mp3"
            TTS_DIR.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copy2(mp3_cached, dest_mp3)
            dur = _get_audio_duration_ffprobe(dest_mp3)
            if dur and dur > 0:
                return (dest_mp3, dur, False)
    except Exception:
        pass
    return None


def _reddit_cache_tts_set(script: str, mp3_path: Path, normalized_wav_path: Optional[Path]) -> None:
    try:
        key = _reddit_cache_hash(script)
        cache_dir = REDDIT_CACHE_DIR / "tts"
        cache_dir.mkdir(parents=True, exist_ok=True)
        import shutil
        if mp3_path.is_file():
            shutil.copy2(mp3_path, cache_dir / f"{key}.mp3")
        if normalized_wav_path and normalized_wav_path.is_file():
            shutil.copy2(normalized_wav_path, cache_dir / f"{key}_normalized.wav")
    except Exception:
        pass


def _reddit_cache_subs_get(script: str, duration: float, intro_sec: float, margin_v: int, render_id: str, karaoke: bool = False) -> Optional[Path]:
    """If cached ASS exists, copy to SUBS_DIR and return ass_path. Else None."""
    try:
        suffix = "karaoke" if karaoke else "70"
        payload = f"{script}|{duration:.3f}|{intro_sec}|{margin_v}|{suffix}"
        key = _reddit_cache_hash(payload)
        cache_path = REDDIT_CACHE_DIR / "subs" / f"{key}.ass"
        if cache_path.is_file():
            dest = SUBS_DIR / f"{render_id}.ass"
            SUBS_DIR.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copy2(cache_path, dest)
            return dest
    except Exception:
        pass
    return None


def _reddit_cache_subs_set(script: str, duration: float, intro_sec: float, margin_v: int, ass_path: Path, karaoke: bool = False) -> None:
    try:
        suffix = "karaoke" if karaoke else "70"
        payload = f"{script}|{duration:.3f}|{intro_sec}|{margin_v}|{suffix}"
        key = _reddit_cache_hash(payload)
        (REDDIT_CACHE_DIR / "subs").mkdir(parents=True, exist_ok=True)
        import shutil
        if ass_path.is_file():
            shutil.copy2(ass_path, REDDIT_CACHE_DIR / "subs" / f"{key}.ass")
    except Exception:
        pass
FFMPEG_BIN = os.environ.get("FFMPEG_BIN", "ffmpeg").strip()
FFPROBE_BIN = os.environ.get("FFPROBE_BIN", "ffprobe").strip()


def _load_gameplay_manifest() -> list:
    """Load webapp/assets/gameplay/manifest.json. Return list of {id, name, file}. Empty if missing."""
    path = GAMEPLAY_DIR / "manifest.json" if GAMEPLAY_DIR else Path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _list_gameplay_backgrounds() -> list:
    """List only .mp4 files that exist under assets/gameplay/. No manifest presets—avoids broken template options."""
    out = []
    if not GAMEPLAY_DIR or not GAMEPLAY_DIR.is_dir():
        return out
    for f in sorted(GAMEPLAY_DIR.glob("*.mp4")):
        if f.is_file() and f.stat().st_size >= 1000:
            stem = f.stem
            out.append({"id": stem, "name": stem, "file": f.name})
    return out


def _run_piper_to_file(text: str, wav_path: Path, voice_model: str) -> Tuple[bool, str]:
    """Run Piper TTS; write WAV to wav_path. Return (success, error_message)."""
    piper_bin = os.environ.get("PIPER_BIN", "").strip()
    if not piper_bin or not Path(piper_bin).is_file():
        return (False, "PIPER_BIN not set or missing.")
    if not voice_model or not Path(voice_model).is_file():
        return (False, "PIPER_MODEL not set or missing.")
    piper_dir = str(Path(piper_bin).resolve().parent)
    espeak_data = Path(piper_dir) / "espeak-ng-data"
    args = [piper_bin, "-m", voice_model, "-f", str(wav_path)]
    if (Path(voice_model).parent / (Path(voice_model).name + ".json")).is_file():
        args.extend(["-c", voice_model + ".json"])
    if espeak_data.is_dir():
        args.extend(["--espeak_data", str(espeak_data)])
    try:
        proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            cwd=piper_dir,
            encoding="utf-8",
            errors="replace",
        )
        _, stderr = proc.communicate(input=text, timeout=300)
        if proc.returncode != 0 or not wav_path.is_file():
            return (False, (stderr or "Piper exited non-zero").strip() or "Piper failed.")
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception:
            pass
        return (False, "Piper timeout.")
    except Exception as e:
        return (False, str(e))
    return (True, "")


def _get_audio_duration_ffprobe(wav_path: Path) -> Optional[float]:
    """Return duration in seconds or None."""
    try:
        out = subprocess.run(
            [FFPROBE_BIN, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(wav_path)],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(REPO_ROOT),
        )
        if out.returncode != 0:
            return None
        return float(out.stdout.strip())
    except Exception:
        return None


def _reddit_speed_audio(src_path: Path, out_path: Path, speed: float, pitch: float = 1.0) -> Tuple[bool, Optional[float], str]:
    """Speed up audio (atempo) and optionally raise pitch for chipmunk effect (asetrate+aresample). Saves WAV to out_path."""
    if speed <= 1.0:
        return (False, None, "speed must be > 1.0")
    try:
        if pitch > 1.0:
            filt = f"atempo={speed},asetrate=44100*{pitch},aresample=44100"
        else:
            filt = f"atempo={speed}"
        cmd = [
            FFMPEG_BIN, "-y", "-i", str(src_path),
            "-filter:a", filt,
            "-ar", "44100",
            str(out_path),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=str(REPO_ROOT))
        if r.returncode != 0 or not out_path.is_file():
            return (False, None, (r.stderr or "atempo failed").strip()[:200])
        dur = _get_audio_duration_ffprobe(out_path)
        return (True, dur, "")
    except Exception as e:
        return (False, None, str(e)[:200])


def _reddit_normalize_audio(src_path: Path, out_path: Path) -> Tuple[bool, Optional[float], str]:
    """Normalize narration with ffmpeg loudnorm. Return (success, duration_sec, error). Saves WAV to out_path."""
    try:
        cmd = [
            FFMPEG_BIN, "-y", "-i", str(src_path),
            "-af", "loudnorm=I=-16:LRA=11:TP=-1.5",
            "-ar", "44100",
            str(out_path),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=str(REPO_ROOT))
        if r.returncode != 0 or not out_path.is_file():
            return (False, None, (r.stderr or "loudnorm failed").strip()[:200])
        dur = _get_audio_duration_ffprobe(out_path)
        return (True, dur, "")
    except Exception as e:
        return (False, None, str(e)[:200])


def _reddit_pitch_shift(src_path: Path, out_path: Path) -> Tuple[bool, Optional[float], str]:
    """Apply +1 semitone via asetrate/aresample. Returns (success, duration_sec, error)."""
    try:
        af = "asetrate=44100*1.05946,aresample=44100"
        cmd = [FFMPEG_BIN, "-y", "-i", str(src_path), "-af", af, "-ar", "44100", str(out_path)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=str(REPO_ROOT))
        if r.returncode != 0 or not out_path.is_file():
            return (False, None, (r.stderr or "pitch failed").strip()[:200])
        return (True, _get_audio_duration_ffprobe(out_path), "")
    except Exception as e:
        return (False, None, str(e)[:200])


def _reddit_compress_audio(src_path: Path, out_path: Path) -> Tuple[bool, Optional[float], str]:
    """Light compression via compand. Returns (success, duration_sec, error)."""
    try:
        af = "compand=attacks=0.3:decays=0.8:points=-80/-80|-20/-18|0/-14"
        cmd = [FFMPEG_BIN, "-y", "-i", str(src_path), "-af", af, "-ar", "44100", str(out_path)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=str(REPO_ROOT))
        if r.returncode != 0 or not out_path.is_file():
            return (False, None, (r.stderr or "compand failed").strip()[:200])
        return (True, _get_audio_duration_ffprobe(out_path), "")
    except Exception as e:
        return (False, None, str(e)[:200])


def _reddit_eq_audio(src_path: Path, out_path: Path) -> Tuple[bool, Optional[float], str]:
    """Presence boost 3–6 kHz via equalizer. Returns (success, duration_sec, error)."""
    try:
        af = "equalizer=f=4500:width_type=o:width=1:g=3"
        cmd = [FFMPEG_BIN, "-y", "-i", str(src_path), "-af", af, "-ar", "44100", str(out_path)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=str(REPO_ROOT))
        if r.returncode != 0 or not out_path.is_file():
            return (False, None, (r.stderr or "eq failed").strip()[:200])
        return (True, _get_audio_duration_ffprobe(out_path), "")
    except Exception as e:
        return (False, None, str(e)[:200])


def _reddit_find_bgm_file() -> Optional[Path]:
    """Return first .mp3 in REDDIT_BGM_DIR, or REDDIT_BGM_DIR/REDDIT_BGM_DEFAULT_FILE if present."""
    if not REDDIT_BGM_DIR or not REDDIT_BGM_DIR.is_dir():
        return None
    default = REDDIT_BGM_DIR / REDDIT_BGM_DEFAULT_FILE
    if default.is_file():
        return default
    for f in sorted(REDDIT_BGM_DIR.glob("*.mp3")):
        return f
    return None


def _reddit_mix_bgm(voice_path: Path, duration_sec: float, out_path: Path, render_id: str) -> Tuple[bool, Optional[float], str]:
    """Loop BGM to duration_sec, duck under voice, amix. Returns (success, duration_sec, error)."""
    bgm_file = _reddit_find_bgm_file()
    if not bgm_file or not bgm_file.is_file():
        return (False, None, "no BGM file")
    try:
        # Linear gain from dB: 10^(dB/20)
        gain = 10 ** (REDDIT_BGM_VOLUME_DB / 20.0)
        looped = TTS_DIR / f"{render_id}_bgm_looped.wav"
        cmd_loop = [
            FFMPEG_BIN, "-y", "-stream_loop", "-1", "-i", str(bgm_file),
            "-t", str(max(0.1, duration_sec)), "-ar", "44100", "-ac", "1", str(looped),
        ]
        r = subprocess.run(cmd_loop, capture_output=True, text=True, timeout=60, cwd=str(REPO_ROOT))
        if r.returncode != 0 or not looped.is_file():
            return (False, None, (r.stderr or "bgm loop failed").strip()[:200])
        # amix: voice + BGM, duration=first (voice length), weights 1 and gain
        filt = f"[0:a]volume=1[v];[1:a]volume={gain}[m];[v][m]amix=inputs=2:duration=first:dropout_transition=0"
        cmd_mix = [
            FFMPEG_BIN, "-y", "-i", str(voice_path), "-i", str(looped),
            "-filter_complex", filt, "-ar", "44100", str(out_path),
        ]
        r = subprocess.run(cmd_mix, capture_output=True, text=True, timeout=120, cwd=str(REPO_ROOT))
        try:
            if looped.is_file():
                looped.unlink()
        except Exception:
            pass
        if r.returncode != 0 or not out_path.is_file():
            return (False, None, (r.stderr or "amix failed").strip()[:200])
        return (True, _get_audio_duration_ffprobe(out_path), "")
    except Exception as e:
        return (False, None, str(e)[:200])


def _reddit_apply_audio_chain(
    src_path: Path,
    out_path: Path,
    duration_sec: float,
    render_id: str,
) -> Tuple[bool, Optional[float], List[str]]:
    """TikTok-style chain: atempo -> pitch -> compress -> eq -> [BGM] -> loudnorm. Each stage optional; on failure skip and log. Returns (success, final_duration, list of applied stage names)."""
    stages_applied: List[str] = []
    current = src_path
    duration = duration_sec

    # 1) Speed (atempo only, no pitch)
    if REDDIT_TTS_SPEED > 1.0:
        sped_path = TTS_DIR / f"{render_id}_chain_sped.wav"
        ok, dur, err = _reddit_speed_audio(current, sped_path, REDDIT_TTS_SPEED, pitch=1.0)
        if ok and sped_path.is_file():
            current = sped_path
            if dur:
                duration = dur
            stages_applied.append("speed")
        else:
            _log(f"[REDDIT] audio_chain stage speed failed: {err or 'unknown'}")

    # 2) Pitch shift
    if REDDIT_PITCH_SHIFT:
        pitch_path = TTS_DIR / f"{render_id}_chain_pitch.wav"
        ok, dur, err = _reddit_pitch_shift(current, pitch_path)
        if ok and pitch_path.is_file():
            current = pitch_path
            if dur:
                duration = dur
            stages_applied.append("pitch")
        else:
            _log(f"[REDDIT] audio_chain stage pitch failed: {err or 'unknown'}")

    # 3) Compression
    if REDDIT_COMPRESS:
        comp_path = TTS_DIR / f"{render_id}_chain_compress.wav"
        ok, dur, err = _reddit_compress_audio(current, comp_path)
        if ok and comp_path.is_file():
            current = comp_path
            if dur:
                duration = dur
            stages_applied.append("compress")
        else:
            _log(f"[REDDIT] audio_chain stage compress failed: {err or 'unknown'}")

    # 4) EQ
    if REDDIT_EQ:
        eq_path = TTS_DIR / f"{render_id}_chain_eq.wav"
        ok, dur, err = _reddit_eq_audio(current, eq_path)
        if ok and eq_path.is_file():
            current = eq_path
            if dur:
                duration = dur
            stages_applied.append("eq")
        else:
            _log(f"[REDDIT] audio_chain stage eq failed: {err or 'unknown'}")

    # 5) BGM (optional mix)
    if REDDIT_BGM:
        mix_path = TTS_DIR / f"{render_id}_chain_mix.wav"
        ok, dur, err = _reddit_mix_bgm(current, duration, mix_path, render_id)
        if ok and mix_path.is_file():
            current = mix_path
            if dur:
                duration = dur
            stages_applied.append("bgm")
        else:
            _log(f"[REDDIT] audio_chain stage bgm skipped: {err or 'disabled'}")

    # 6) Loudnorm (final)
    ok_norm, norm_dur, err = _reddit_normalize_audio(current, out_path)
    if ok_norm and out_path.is_file():
        if norm_dur:
            duration = norm_dur
        stages_applied.append("loudnorm")
    else:
        _log(f"[REDDIT] audio_chain stage loudnorm failed: {err or 'unknown'}")
        return (False, _get_audio_duration_ffprobe(current) if current.is_file() else None, stages_applied)

    return (True, duration, stages_applied)


def _openai_transcribe_to_srt(wav_path: Path, srt_path: Path) -> Tuple[bool, str]:
    """Use OpenAI Whisper API for transcription (best quality, uses your credits). Writes SRT. Return (success, error_message)."""
    if not OPENAI_ENABLED or not OPENAI_API_KEY:
        return (False, "OpenAI not configured")
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        with open(wav_path, "rb") as f:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                response_format="srt",
            )
        content = transcript if isinstance(transcript, str) else (getattr(transcript, "content", None) or str(transcript))
        if content and content.strip():
            srt_path.write_text(content.strip(), encoding="utf-8")
            _log("[RENDER] subtitles=openai_whisper (best quality)")
            return (True, "")
        return (False, "OpenAI returned empty transcript")
    except Exception as e:
        err = str(e).strip()[:300]
        _log(f"[RENDER] OpenAI transcription error: {e}")
        return (False, err)


def _whisper_to_srt(wav_path: Path, srt_path: Path) -> Tuple[bool, str]:
    """Generate SRT from WAV. When OPENAI_ENABLED and OPENAI_SUBTITLES=1, use OpenAI Whisper API (best quality). Else use local faster-whisper."""
    if OPENAI_ENABLED and OPENAI_SUBTITLES:
        ok, err = _openai_transcribe_to_srt(wav_path, srt_path)
        if ok:
            return (True, "")
        _log(f"[RENDER] OpenAI subtitles failed, falling back to faster-whisper: {err}")
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel("base", device="cpu", compute_type="int8")
        segments, _ = model.transcribe(str(wav_path), word_timestamps=False)
        lines = []
        for i, seg in enumerate(segments, 1):
            start = seg.start
            end = seg.end
            text = (seg.text or "").strip()
            if not text:
                continue
            def _ts(t):
                h = int(t // 3600)
                m = int((t % 3600) // 60)
                s = int(t % 60)
                ms = int((t % 1) * 1000)
                return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
            lines.append(str(i))
            lines.append(f"{_ts(start)} --> {_ts(end)}")
            lines.append(text)
            lines.append("")
        srt_path.write_text("\n".join(lines), encoding="utf-8")
        return (True, "")
    except Exception as e:
        err = str(e).strip()[:300]
        _log(f"[RENDER] Whisper error: {e}")
        return (False, err)


def _scale_srt(srt_path: Path, scale: float, out_path: Path) -> None:
    """Scale SRT timestamps by scale (e.g. 1/tempo). Write to out_path."""
    import re
    content = srt_path.read_text(encoding="utf-8")
    def repl(m):
        a, b = m.group(1), m.group(2)
        def to_sec(s):
            p = s.replace(",", ".").split(":")
            return int(p[0]) * 3600 + int(p[1]) * 60 + float(p[2])
        def to_srt(t):
            h = int(t // 3600)
            m = int((t % 3600) // 60)
            s = t % 60
            sec = int(s)
            ms = int(round((s % 1) * 1000)) % 1000
            return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"
        t1, t2 = to_sec(a) * scale, to_sec(b) * scale
        return f"{to_srt(t1)} --> {to_srt(t2)}"
    content = re.sub(r"(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})", repl, content)
    out_path.write_text(content, encoding="utf-8")


def _shift_srt(srt_path: Path, offset_sec: float, out_path: Path) -> None:
    """Shift SRT timestamps by offset_sec (positive = later). Write to out_path."""
    import re
    content = srt_path.read_text(encoding="utf-8")

    def to_sec(s: str) -> float:
        p = s.replace(",", ".").split(":")
        return int(p[0]) * 3600 + int(p[1]) * 60 + float(p[2])

    def to_srt(t: float) -> str:
        if t < 0:
            t = 0
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = t % 60
        sec = int(s)
        ms = int(round((s % 1) * 1000)) % 1000
        return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"

    def repl(m: re.Match) -> str:
        t1, t2 = to_sec(m.group(1)) + offset_sec, to_sec(m.group(2)) + offset_sec
        return f"{to_srt(t1)} --> {to_srt(t2)}"

    content = re.sub(r"(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})", repl, content)
    out_path.write_text(content, encoding="utf-8")


def _clean_story_text(raw: str) -> str:
    """Sanitize and format Reddit story text: normalize whitespace, quotes, etc."""
    if not raw or not isinstance(raw, str):
        return ""
    t = raw.strip()
    t = re.sub(r"\r\n", "\n", t)
    t = re.sub(r"\r", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r" \n", "\n", t)
    t = re.sub(r"\n ", "\n", t)
    return t.strip()


def _parse_story_title_body(raw: str) -> Tuple[str, str]:
    """
    Parse pasted story text into (title, body).
    - First non-empty line under 120 chars = title; everything after first blank line = body.
    - If no blank line: first line = title, rest = body.
    - If only one paragraph (no newline): title = 'Reddit Story', body = full text.
    - If line starts with 'TITLE:', strip prefix and use rest of line as title.
    """
    if not raw or not isinstance(raw, str):
        return ("Reddit Story", "")
    t = raw.strip()
    if not t:
        return ("Reddit Story", "")
    lines = [ln.strip() for ln in t.split("\n")]
    # Explicit TITLE: prefix
    if lines and lines[0].upper().startswith("TITLE:"):
        title = lines[0][6:].strip()
        if not title:
            title = "Reddit Story"
        body = "\n".join(lines[1:]).strip()
        return (title[:120], body)
    # Only one paragraph (single line)
    if "\n" not in t:
        return ("Reddit Story", t)
    # Find first blank line
    first_blank = None
    for i, ln in enumerate(lines):
        if ln == "":
            first_blank = i
            break
    if first_blank is not None:
        title = "Reddit Story"
        for ln in lines[:first_blank]:
            if ln and len(ln) <= 120:
                title = ln
                break
            elif ln:
                title = ln[:120]
                break
        body = "\n".join(lines[first_blank + 1:]).strip()
    else:
        title = (lines[0][:120] if lines[0] else "Reddit Story")
        body = "\n".join(lines[1:]).strip()
    if not title:
        title = "Reddit Story"
    return (title, body)


def _escape_drawtext(s: str) -> str:
    """Escape string for ffmpeg drawtext: single quote doubled (drawtext uses '' for literal ')."""
    if not s:
        return ""
    return s.replace("'", "''").replace("\\", "\\\\")


def _get_piper_paths() -> Tuple[Optional[Path], Optional[Path]]:
    """Return (piper_exe_path, model_path). Prefer high-quality voice (large > medium) if not set via env."""
    piper_bin = (os.environ.get("PIPER_BIN") or "").strip()
    piper_model = (os.environ.get("PIPER_MODEL") or "").strip()
    if not piper_bin:
        default_bin = REPO_ROOT / "piper" / "piper" / "piper.exe"
        if default_bin.is_file():
            piper_bin = str(default_bin)
    if not piper_model:
        voices_dir = REPO_ROOT / "piper" / "voices"
        default_model = None
        if voices_dir.is_dir():
            onnx_files = [f for f in voices_dir.glob("*.onnx") if f.is_file()]
            for preferred in ("large", "x_low", "medium", "small", "base"):
                for f in onnx_files:
                    if preferred in f.stem.lower():
                        default_model = f
                        break
                if default_model is not None:
                    break
            if default_model is None and onnx_files:
                default_model = sorted(onnx_files, key=lambda p: p.stat().st_size, reverse=True)[0]
        if default_model is None:
            default_model = REPO_ROOT / "piper" / "voices" / "en_US-lessac-medium.onnx"
        if default_model.is_file():
            piper_model = str(default_model)
    p_bin = Path(piper_bin).resolve() if piper_bin else None
    p_model = Path(piper_model).resolve() if piper_model else None
    if p_bin and p_bin.is_file() and p_model and p_model.is_file():
        return (p_bin, p_model)
    return (None, None)


def _piper_tts_available() -> Tuple[bool, str]:
    """Return (ok, error_message). If ok, Piper and model are configured and present."""
    p_bin, p_model = _get_piper_paths()
    if p_bin is None:
        return (False, "PIPER_BIN not set. Set PIPER_BIN to the path to piper.exe (e.g. C:\\Piper\\piper.exe).")
    if p_model is None:
        return (False, "PIPER_MODEL not set. Set PIPER_MODEL to the path to the voice model .onnx file (e.g. en_US-lessac-medium.onnx).")
    return (True, "")


@app.post("/api/tts_offline")
async def api_tts_offline(request: Request):
    """
    Generate WAV using Piper (offline neural TTS). Body: { "text": string, "voice_model": string optional }.
    Returns WAV file with X-Filename header. 503 if Piper or model not configured.
    """
    ok, err_msg = _piper_tts_available()
    if not ok:
        return JSONResponse(
            content={"error": "tts_unavailable", "detail": err_msg},
            status_code=503,
        )
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON body required")
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    voice_model = (body.get("voice_model") or "").strip() or os.environ.get("PIPER_MODEL", "").strip()
    if not voice_model or not Path(voice_model).is_file():
        _, p_model = _get_piper_paths()
        if p_model and p_model.is_file():
            voice_model = str(p_model)
        else:
            return JSONResponse(
                content={
                    "error": "model_not_found",
                    "detail": f"Voice model not found. Set PIPER_MODEL to path to .onnx file (e.g. en_US-lessac-medium.onnx).",
                },
                status_code=503,
            )
    try:
        TTS_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        _log(f"[TTS] could not create outputs/tts: {e}")
        return JSONResponse(content={"error": "Could not create TTS output directory"}, status_code=500)
    ts = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    rnd = uuid.uuid4().hex[:8]
    wav_name = f"{ts}_{rnd}.wav"
    wav_path = TTS_DIR / wav_name
    p_bin, _ = _get_piper_paths()
    if not p_bin:
        return JSONResponse(content={"error": "Piper not configured", "detail": "PIPER_BIN not set or missing."}, status_code=503)
    piper_bin = str(p_bin)
    piper_dir = str(p_bin.parent)
    espeak_data = Path(piper_dir) / "espeak-ng-data"
    args = [piper_bin, "-m", voice_model, "-f", str(wav_path)]
    if (Path(voice_model).parent / (Path(voice_model).name + ".json")).is_file():
        args.extend(["-c", voice_model + ".json"])
    if espeak_data.is_dir():
        args.extend(["--espeak_data", str(espeak_data)])
    try:
        proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            cwd=piper_dir,
            encoding="utf-8",
            errors="replace",
        )
        _, stderr = proc.communicate(input=text, timeout=300)
        if proc.returncode != 0 or not wav_path.is_file():
            _log(f"[TTS] Piper exit code={proc.returncode} stderr={stderr or ''}")
            return JSONResponse(
                content={"error": "Piper failed", "detail": (stderr or "Piper exited non-zero").strip() or "Check server logs."},
                status_code=500,
            )
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception:
            pass
        return JSONResponse(content={"error": "TTS timeout", "detail": "Piper took too long (max 300s)."}, status_code=504)
    except Exception as e:
        _log(f"[TTS] Piper subprocess error: {e}")
        return JSONResponse(content={"error": "TTS failed", "detail": str(e)}, status_code=500)
    headers = {"X-Filename": wav_name}
    return FileResponse(wav_path, media_type="audio/wav", headers=headers)


def _ass_time_reddit(secs: float) -> str:
    """Format seconds as ASS timestamp (H:MM:SS.cc centiseconds)."""
    h = int(secs // 3600)
    m = int((secs % 3600) // 60)
    s = secs % 60
    cs = int(round(s * 100)) % 100
    sec_int = int(s)
    return f"{h}:{m:02d}:{sec_int:02d}.{cs:02d}"


REDDIT_DEBUG_SUBS = (os.environ.get("REDDIT_DEBUG_SUBS") or "").strip() in ("1", "true", "yes")
REDDIT_DEBUG_KARAOKE = (os.environ.get("REDDIT_DEBUG_KARAOKE") or "").strip() in ("1", "true", "yes")
_reddit_debug_karaoke_done: bool = False

REDDIT_ASS_MIN_BYTES = 200
REDDIT_TTS_VOICE = (os.environ.get("REDDIT_TTS_VOICE") or "nova").strip().lower() or "nova"
REDDIT_TTS_VOICE_LIST = [v.strip().lower() for v in (os.environ.get("REDDIT_TTS_VOICE_LIST") or "").strip().split(",") if v.strip()]
_raw_reddit_tts_speed = (os.environ.get("REDDIT_TTS_SPEED") or "1.22").strip()
REDDIT_TTS_SPEED = max(1.0, min(1.35, float(_raw_reddit_tts_speed))) if _raw_reddit_tts_speed else 1.22
_raw_reddit_tts_pitch = (os.environ.get("REDDIT_TTS_PITCH") or "1.25").strip()
REDDIT_TTS_PITCH = max(1.0, min(1.5, float(_raw_reddit_tts_pitch))) if _raw_reddit_tts_pitch else 1.25
# TikTok-style audio chain (Reddit only): pitch shift, compression, EQ, BGM
REDDIT_PITCH_SHIFT = (os.environ.get("REDDIT_PITCH_SHIFT") or "1").strip().lower() in ("1", "true", "yes")
REDDIT_COMPRESS = (os.environ.get("REDDIT_COMPRESS") or "1").strip().lower() in ("1", "true", "yes")
REDDIT_EQ = (os.environ.get("REDDIT_EQ") or "1").strip().lower() in ("1", "true", "yes")
REDDIT_BGM = (os.environ.get("REDDIT_BGM") or "1").strip().lower() in ("1", "true", "yes")
_raw_bgm_vol = (os.environ.get("REDDIT_BGM_VOLUME") or "-30").strip()
REDDIT_BGM_VOLUME_DB = max(-60, min(0, int(_raw_bgm_vol))) if _raw_bgm_vol.lstrip("-").isdigit() else -30
REDDIT_BGM_DIR = ASSETS_DIR / "music" if ASSETS_DIR else Path()
REDDIT_BGM_DEFAULT_FILE = "ambient.mp3"


def _reddit_ass_parse_time(ass_time_str: str) -> float:
    """Parse ASS timestamp H:MM:SS.cc to seconds."""
    try:
        parts = ass_time_str.strip().split(":")
        if len(parts) >= 3:
            h, m = int(parts[0]), int(parts[1])
            s_part = parts[2].replace(",", ".")
            s = float(s_part)
            return h * 3600 + m * 60 + s
    except Exception:
        pass
    return 0.0


def _reddit_validate_ass(ass_path: Path, expected_end_sec: Optional[float] = None, tolerance_sec: float = 2.0) -> Tuple[bool, str, int]:
    """Validate ASS: exists, size, Dialogue lines; optionally last end time ~= expected_end_sec. Return (ok, error_message, dialogue_count)."""
    if not ass_path or not ass_path.is_file():
        return (False, f"ASS file missing: {ass_path}", 0)
    content = ass_path.read_text(encoding="utf-8", errors="replace")
    size = len(content.encode("utf-8"))
    if size < REDDIT_ASS_MIN_BYTES:
        return (False, f"ASS file too small ({size} bytes, need >= {REDDIT_ASS_MIN_BYTES})", 0)
    dialogue_lines = [line for line in content.splitlines() if line.strip().startswith("Dialogue:")]
    dialogue_count = len(dialogue_lines)
    if dialogue_count == 0:
        return (False, "ASS contains 0 Dialogue lines (no subtitles)", 0)
    if expected_end_sec is not None and dialogue_lines:
        last_line = dialogue_lines[-1].strip()
        parts = last_line.split(",")
        if len(parts) >= 3:
            end_ts = _reddit_ass_parse_time(parts[2].strip())
            if abs(end_ts - expected_end_sec) > tolerance_sec:
                return (False, f"ASS end time {end_ts:.1f}s does not match expected {expected_end_sec:.1f}s (tolerance {tolerance_sec}s)", dialogue_count)
    return (True, "", dialogue_count)


REDDIT_SUBS_MAX_CHUNKS = 120
REDDIT_SUBS_MIN_DUR = 0.25
REDDIT_SUBS_MAX_DUR = 1.10
REDDIT_SUBS_HOOK_SEC = 3.0
REDDIT_SUBS_HOOK_SCALE = 0.85

# Subs mode: karaoke (word highlight) or chunk (phrase). Env REDDIT_SUBS_MODE=karaoke|chunk (default karaoke).
_reddit_subs_mode = (os.environ.get("REDDIT_SUBS_MODE") or "karaoke").strip().lower()
REDDIT_SUBS_MODE_KARAOKE = _reddit_subs_mode in ("karaoke", "1", "true", "yes")
# Karaoke (word-level) limits and styling
REDDIT_KARAOKE_MAX_WORDS = 420
REDDIT_KARAOKE_WORD_DUR_MIN = 0.10
REDDIT_KARAOKE_WORD_DUR_MAX = 0.55
# Global subtitle offset (seconds): applied to every start/end after intro. Positive = delay subs.
_raw_subs_offset = (os.environ.get("REDDIT_SUBS_OFFSET_SEC") or "0.05").strip()
try:
    REDDIT_SUBS_OFFSET_SEC = float(_raw_subs_offset)
except ValueError:
    REDDIT_SUBS_OFFSET_SEC = 0.05
# Word timing bounds and smoothing (karaoke)
_raw_word_min = (os.environ.get("REDDIT_WORD_MIN_SEC") or "0.14").strip()
_raw_word_max = (os.environ.get("REDDIT_WORD_MAX_SEC") or "0.50").strip()
try:
    REDDIT_WORD_MIN_SEC = float(_raw_word_min)
except ValueError:
    REDDIT_WORD_MIN_SEC = 0.14
try:
    REDDIT_WORD_MAX_SEC = float(_raw_word_max)
except ValueError:
    REDDIT_WORD_MAX_SEC = 0.50
REDDIT_KARAOKE_WORDS_PER_LINE_LO = 5
REDDIT_KARAOKE_WORDS_PER_LINE_HI = 8
REDDIT_KARAOKE_WORDS_PER_LINE_CAP = 10
# Subs: pro TikTok style — big, punchy, thick outline. Montserrat Bold / Arial Bold. Clamps prevent layout break.
SUBS_FONT = (os.environ.get("SUBS_FONT") or "Montserrat Bold").strip() or "Arial Bold"
_raw_subs_font = (os.environ.get("SUBS_FONT_SIZE") or os.environ.get("REDDIT_SUBS_FONT_SIZE") or "72").strip()
SUBS_FONT_SIZE = max(56, min(96, int(_raw_subs_font))) if _raw_subs_font.isdigit() else 72
_raw_subs_margin = (os.environ.get("SUBS_MARGIN_V") or os.environ.get("REDDIT_SUBS_MARGIN_V") or "300").strip()
SUBS_MARGIN_V = max(240, min(380, int(_raw_subs_margin))) if _raw_subs_margin.isdigit() else 300
SUBS_OUTLINE = max(3, min(7, int((os.environ.get("SUBS_OUTLINE") or "5").strip()))) if (os.environ.get("SUBS_OUTLINE") or "5").strip().isdigit() else 5
SUBS_SHADOW = max(0, min(5, int((os.environ.get("SUBS_SHADOW") or "1").strip()))) if (os.environ.get("SUBS_SHADOW") or "1").strip().isdigit() else 1
REDDIT_KARAOKE_FONT_SIZE = SUBS_FONT_SIZE
REDDIT_KARAOKE_MARGIN_V = SUBS_MARGIN_V
_raw_reddit_subs_font = (os.environ.get("REDDIT_SUBS_FONT_SIZE") or "72").strip()
if _raw_reddit_subs_font.isdigit():
    REDDIT_KARAOKE_FONT_SIZE = max(56, min(96, int(_raw_reddit_subs_font)))
_raw_reddit_subs_margin = (os.environ.get("REDDIT_SUBS_MARGIN_V") or "300").strip()
if _raw_reddit_subs_margin.isdigit():
    REDDIT_KARAOKE_MARGIN_V = max(240, min(380, int(_raw_reddit_subs_margin)))
# ASS colour format: &HAABBGGRR (alpha, blue, green, red). Karaoke: Primary=base (white), Secondary=highlight (yellow).
def _ass_colour_bgr(r: int, g: int, b: int, a: int = 0) -> str:
    """Convert RGB (0-255) to ASS &HAABBGGRR. Use for PrimaryColour/SecondaryColour so karaoke fill works."""
    r, g, b = max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b))
    a = max(0, min(255, a))
    return f"&H{a:02X}{b:02X}{g:02X}{r:02X}"


REDDIT_KARAOKE_COLOUR_PRIMARY = _ass_colour_bgr(255, 255, 255)   # white (base)
REDDIT_KARAOKE_COLOUR_SECONDARY = _ass_colour_bgr(255, 212, 0)  # yellow #FFD400 (karaoke highlight)
REDDIT_KARAOKE_COLOUR_ACTIVE = REDDIT_KARAOKE_COLOUR_SECONDARY
REDDIT_KARAOKE_COLOUR_BASE = REDDIT_KARAOKE_COLOUR_PRIMARY
_raw_karaoke_tag = (os.environ.get("REDDIT_KARAOKE_TAG") or "kf").strip().lower()
REDDIT_KARAOKE_TAG = "kf" if _raw_karaoke_tag == "kf" else "k"
# Visual polish (Reddit pipeline only)
REDDIT_BG_ZOOM = (os.environ.get("REDDIT_BG_ZOOM") or "1").strip().lower() in ("1", "true", "yes")
REDDIT_VIGNETTE = (os.environ.get("REDDIT_VIGNETTE") or "1").strip().lower() in ("1", "true", "yes")
REDDIT_PROGRESS_BAR = (os.environ.get("REDDIT_PROGRESS_BAR") or "0").strip().lower() in ("1", "true", "yes")

# Vertical fill: cover (scale+crop 9:16, no blur) or blur (legacy). Both pipelines.
VERTICAL_FILL_MODE = (os.environ.get("VERTICAL_FILL_MODE") or "cover").strip().lower()
if VERTICAL_FILL_MODE not in ("cover", "blur"):
    VERTICAL_FILL_MODE = "cover"
# FIT_MODE: BLUR_BANDS (default, blur top/bottom + sharp center) or COVER (full-frame 1080x1920, no blur).
FIT_MODE = (os.environ.get("FIT_MODE") or "BLUR_BANDS").strip().upper()
if FIT_MODE not in ("COVER", "BLUR_BANDS"):
    raise RuntimeError(f"FIT_MODE must be COVER or BLUR_BANDS, got {FIT_MODE!r}")
# COVER framing: CROP_Y_BIAS shifts crop upward so faces sit higher (talking-head TikTok feel). Range 0-300.
try:
    CROP_Y_BIAS = int((os.environ.get("CROP_Y_BIAS") or "120").strip())
except ValueError:
    CROP_Y_BIAS = 120
CROP_Y_BIAS = max(0, min(300, CROP_Y_BIAS))

def _cover_crop_filter() -> str:
    """9:16 cover crop; when CROP_Y_BIAS>0, crop is biased upward (recommended 80-160 for faces higher)."""
    if CROP_Y_BIAS == 0:
        return "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
    return f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920:(iw-1080)/2:max(0,(ih-1920)/2-{CROP_Y_BIAS})"

# Single 9:16 cover-crop filter used when FIT_MODE=COVER (use _cover_crop_filter() for CROP_Y_BIAS support).
COVER_CROP = _cover_crop_filter()

# BLUR_BANDS: blur top/bottom, sharp centered foreground. Same builder for Reddit and YouTube.
BLUR_BANDS_BG = "scale=1080:1920,boxblur=30:1,eq=brightness=-0.10:saturation=0.88"
BLUR_BANDS_FG = "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2"
# Debug stamp: default OFF; set DEBUG_RENDER_STAMP=1 to burn PIPELINE_ACTIVE:<FIT_MODE>:<RENDER_ID> (proof).
DEBUG_RENDER_STAMP = (os.environ.get("DEBUG_RENDER_STAMP") or "0").strip().lower() in ("1", "true", "yes")
BRANDING = (os.environ.get("BRANDING") or "1").strip().lower() in ("1", "true", "yes")


def _debug_stamp_filter(render_id: str) -> str:
    """When DEBUG_RENDER_STAMP=1: drawtext PIPELINE_ACTIVE:<FIT_MODE>:<RENDER_ID>, font 56, top-left, box. Hard proof."""
    text = f"PIPELINE_ACTIVE:{FIT_MODE}:{render_id}"
    esc = text.replace("'", "'\\''")
    return f"drawtext=text='{esc}':fontcolor=white@0.95:fontsize=56:x=20:y=20:box=1:boxcolor=black@0.4"


def _render_filter_checks(filter_complex: str, branch_id: str, _required_stamp_ignored: Optional[str] = None) -> None:
    """Emit deterministic logs; raise if stamp required but missing or filter invalid. Log [VIDEO] fit_mode."""
    _log(f"[VIDEO] fit_mode={FIT_MODE}")
    if not (filter_complex and filter_complex.strip()):
        raise RuntimeError(f"{branch_id}: filter_complex is empty")
    if "[vout]" not in filter_complex:
        raise RuntimeError(f"{branch_id}: filter_complex missing [vout] output")
    if FIT_MODE == "COVER":
        _log(f"[VIDEO] crop_y_bias={CROP_Y_BIAS}")
        if "pad=" in filter_complex:
            raise RuntimeError(f"{branch_id}: FIT_MODE=COVER but filter contains pad= (no black bars allowed)")
    if BRANDING and "storyclipsdaily_" not in filter_complex:
        raise RuntimeError(f"{branch_id}: BRANDING=1 but watermark 'storyclipsdaily_' not in filter_complex")
    cover = 1 if ("scale=1080:1920" in filter_complex and "crop=1080:1920" in filter_complex) else 0
    watermark = 1 if ("storyclipsdaily_" in filter_complex or "PIPELINE_ACTIVE" in filter_complex) else 0
    _log(f"[VIDEO] cover_crop_applied={cover}")
    _log(f"[BRAND] watermark_present={watermark}")
    if DEBUG_RENDER_STAMP:
        _log("[DEBUG] render_stamp=on")
        if "PIPELINE_ACTIVE" not in filter_complex:
            raise RuntimeError(f"{branch_id}: DEBUG_RENDER_STAMP=1 but PIPELINE_ACTIVE missing from filter_complex")
    else:
        _log("[DEBUG] render_stamp=off")


def build_and_run_final_ffmpeg_render(
    cmd_inputs: List[str],
    filter_video: str,
    output_path: Path,
    duration_sec: float,
    map_audio: str,
    filter_audio: Optional[str] = None,
) -> Tuple[bool, str]:
    """Single source of truth for final MP4 render. Used by Reddit and YouTube pipelines.
    Output: webapp/outputs/renders/<render_id>.mp4. Logs [FFMPEG] cmd, filter_complex; [OUT] mp4_path, bytes, res."""
    filter_complex = filter_video if not filter_audio else filter_video + ";" + filter_audio
    map_a = "[a]" if filter_audio else map_audio
    cmd = [
        FFMPEG_BIN, "-y"
    ] + cmd_inputs + [
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", map_a,
        "-t", str(duration_sec),
        "-c:v", "libx264", "-preset", "fast", "-c:a", "aac", "-movflags", "+faststart",
        str(output_path),
    ]
    _log("[FFMPEG] cmd=" + " ".join(cmd))
    _log("[FFMPEG] filter_complex=" + filter_video)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, cwd=str(REPO_ROOT))
    except subprocess.TimeoutExpired:
        return (False, "FFmpeg timeout (600s)")
    except Exception as e:
        return (False, str(e)[:400])
    if r.returncode != 0 or not output_path.is_file():
        err = (r.stderr or r.stdout or "FFmpeg failed.").strip()[:400]
        return (False, err)
    out_abs = str(output_path.resolve())
    size = output_path.stat().st_size
    _log("[OUT] mp4_path=" + out_abs)
    _log("[OUT] bytes=" + str(size))
    try:
        probe = subprocess.run(
            [FFPROBE_BIN, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(output_path)],
            capture_output=True, text=True, timeout=15, cwd=str(REPO_ROOT),
        )
        if probe.returncode == 0 and probe.stdout.strip():
            parts = probe.stdout.strip().split(",")
            if len(parts) >= 2:
                w, h = int(parts[0]), int(parts[1])
                _log(f"[OUT] res={w}x{h}")
                if w != 1080 or h != 1920:
                    return (False, f"Output resolution {w}x{h} is not 1080x1920")
    except Exception as e:
        _log(f"[OUT] ffprobe resolution check failed: {e}")
    return (True, "")


# Caption band: cinematic bottom fade (~45% of screen), darkest at bottom, smooth ramp. Env overrides apply.
CAPTION_BAND_ENABLE = (os.environ.get("CAPTION_BAND_ENABLE") or "1").strip().lower() in ("1", "true", "yes")
try:
    CAPTION_BAND_OPACITY = float((os.environ.get("CAPTION_BAND_OPACITY") or "0.42").strip())
except ValueError:
    CAPTION_BAND_OPACITY = 0.42
CAPTION_BAND_OPACITY = max(0.25, min(0.55, CAPTION_BAND_OPACITY))
try:
    CAPTION_BAND_H = int((os.environ.get("CAPTION_BAND_H") or "860").strip())
except ValueError:
    CAPTION_BAND_H = 860
CAPTION_BAND_H = max(700, min(960, CAPTION_BAND_H))  # ~45% of 1920
try:
    CAPTION_BAND_Y = int((os.environ.get("CAPTION_BAND_Y") or "1060").strip())
except ValueError:
    CAPTION_BAND_Y = 1920 - CAPTION_BAND_H
CAPTION_BAND_Y = max(960, min(1220, CAPTION_BAND_Y))  # bottom-heavy, fade starts slightly higher

# Brand logo watermark (optional). Reddit: on by default; YouTube: off by default.
BRAND_LOGO_ENABLE_REDDIT = (os.environ.get("BRAND_LOGO_ENABLE_REDDIT") or "1").strip().lower() in ("1", "true", "yes")
BRAND_LOGO_ENABLE_YOUTUBE = (os.environ.get("BRAND_LOGO_ENABLE_YOUTUBE") or "0").strip().lower() in ("1", "true", "yes")
_raw_logo_path = (os.environ.get("BRAND_LOGO_PATH") or "").strip() or str(ASSETS_DIR / "brand" / "logo.png")
BRAND_LOGO_PATH = Path(_raw_logo_path) if os.path.isabs(_raw_logo_path) else (REPO_ROOT / _raw_logo_path.replace("\\", "/"))
_pos = (os.environ.get("BRAND_LOGO_POS") or "bottom_center").strip().lower()
BRAND_LOGO_POS = _pos if _pos in ("bottom_center", "bottom_left", "bottom_right", "top_left", "top_right") else "bottom_center"
try:
    BRAND_LOGO_SCALE = float((os.environ.get("BRAND_LOGO_SCALE") or "0.14").strip())
except ValueError:
    BRAND_LOGO_SCALE = 0.14
try:
    BRAND_LOGO_OPACITY = float((os.environ.get("BRAND_LOGO_OPACITY") or "0.75").strip())
except ValueError:
    BRAND_LOGO_OPACITY = 0.75
try:
    BRAND_LOGO_MARGIN_X = int((os.environ.get("BRAND_LOGO_MARGIN_X") or "40").strip())
except ValueError:
    BRAND_LOGO_MARGIN_X = 40
try:
    BRAND_LOGO_MARGIN_Y = int((os.environ.get("BRAND_LOGO_MARGIN_Y") or "760").strip())
except ValueError:
    BRAND_LOGO_MARGIN_Y = 760
BRAND_LOGO_SHADOW = (os.environ.get("BRAND_LOGO_SHADOW") or "1").strip().lower() in ("1", "true", "yes")

# Text watermark (all pipelines). TikTok clipper style: centered, above captions, subtle shadow/box.
WATERMARK_ENABLE = (os.environ.get("WATERMARK_ENABLE") or "1").strip().lower() in ("1", "true", "yes")
WATERMARK_TEXT = (os.environ.get("WATERMARK_TEXT") or "storyclipsdaily_").strip()
# WATERMARK_SIZE: font size override (default 52). WATERMARK_Y_OFFSET: pixels above caption band top (default 48).
_raw_wm_size = (os.environ.get("WATERMARK_SIZE") or os.environ.get("WATERMARK_FONT_SIZE") or "52").strip()
WATERMARK_FONT_SIZE = max(12, min(120, int(_raw_wm_size))) if _raw_wm_size.isdigit() else 52
try:
    WATERMARK_Y_OFFSET = int((os.environ.get("WATERMARK_Y_OFFSET") or "48").strip())
except ValueError:
    WATERMARK_Y_OFFSET = 48
WATERMARK_Y_OFFSET = max(0, min(200, WATERMARK_Y_OFFSET))
try:
    WATERMARK_OPACITY = float((os.environ.get("WATERMARK_OPACITY") or "0.70").strip())
except ValueError:
    WATERMARK_OPACITY = 0.70
WATERMARK_OPACITY = max(0, min(1, WATERMARK_OPACITY))
# Always center horizontally (TikTok clipper style).
WATERMARK_X = "(w-text_w)/2"
WATERMARK_Y = int((os.environ.get("WATERMARK_Y") or "930").strip()) if (os.environ.get("WATERMARK_Y") or "930").strip().lstrip("-").isdigit() else 930
WATERMARK_SHADOW = (os.environ.get("WATERMARK_SHADOW") or "1").strip().lower() in ("1", "true", "yes")
try:
    WATERMARK_SHADOW_OPACITY = float((os.environ.get("WATERMARK_SHADOW_OPACITY") or "0.6").strip())
except ValueError:
    WATERMARK_SHADOW_OPACITY = 0.6
WATERMARK_SHADOW_X = int((os.environ.get("WATERMARK_SHADOW_X") or "2").strip()) if (os.environ.get("WATERMARK_SHADOW_X") or "2").strip().lstrip("-").isdigit() else 2
WATERMARK_SHADOW_Y = int((os.environ.get("WATERMARK_SHADOW_Y") or "2").strip()) if (os.environ.get("WATERMARK_SHADOW_Y") or "2").strip().lstrip("-").isdigit() else 2
WATERMARK_FONTFILE = (os.environ.get("WATERMARK_FONTFILE") or "").strip()
WATERMARK_BOX = (os.environ.get("WATERMARK_BOX") or "1").strip().lower() in ("1", "true", "yes")
try:
    WATERMARK_BOX_OPACITY = float((os.environ.get("WATERMARK_BOX_OPACITY") or "0.18").strip())
except ValueError:
    WATERMARK_BOX_OPACITY = 0.18
WATERMARK_BOX_OPACITY = max(0, min(0.5, WATERMARK_BOX_OPACITY))
WATERMARK_BOX_BORDERW = 2


def _caption_band_filter() -> str:
    """Cinematic bottom fade: 6-layer ramp (1.00a, 0.80a, 0.60a, 0.40a, 0.22a, 0.10a) + boxblur 5:1. No hard edges."""
    if not CAPTION_BAND_ENABLE:
        return ""
    a = max(0, min(1, CAPTION_BAND_OPACITY))
    y, h = CAPTION_BAND_Y, CAPTION_BAND_H
    _log(f"[VIDEO] caption_band=on y={y} h={h} a={a} (6-layer fade)")
    # Six layers from bottom up for smooth ramp; then feather with blur.
    mults = (1.00, 0.80, 0.60, 0.40, 0.22, 0.10)
    layer_h = h // 6
    draw_parts = []
    for i, m in enumerate(mults):
        ly = y + int(h * (1 - (i + 1) / 6))
        lh = layer_h if i < 5 else (h - 5 * layer_h)
        draw_parts.append(f"drawbox=x=0:y={ly}:w=1080:h={lh}:color=black@{a * m}:t=fill")
    drawboxes = ",".join(draw_parts) + ",boxblur=5:1"
    return drawboxes


def _watermark_filter() -> str:
    """TikTok clipper style: centered, above captions, subtle (not template-y). fontcolor white@0.92; optional light box; shadow. Logs [BRAND]."""
    if not (BRANDING or (WATERMARK_ENABLE and WATERMARK_TEXT)):
        _log("[BRAND] watermark=off")
        return ""
    try:
        text_esc = WATERMARK_TEXT.replace("'", "'\\''")
        # Softer look: white@0.92; shadow black@0.6, 2,2; box only if WATERMARK_BOX=1, light (0.18, borderw 2).
        op = 0.92
        caption_top = CAPTION_BAND_Y
        est_h = max(40, int(WATERMARK_FONT_SIZE * 1.2))
        wm_y = caption_top - WATERMARK_Y_OFFSET - est_h
        wm_y = max(80, min(1600, wm_y))
        parts = [
            f"drawtext=text='{text_esc}'",
            f"fontcolor=white@{op}",
            f"fontsize={WATERMARK_FONT_SIZE}",
            f"x='(w-text_w)/2'",
            f"y={wm_y}",
        ]
        if WATERMARK_BOX:
            parts.append(f"box=1:boxcolor=black@{max(0, min(0.5, WATERMARK_BOX_OPACITY))}:boxborderw={WATERMARK_BOX_BORDERW}")
        if WATERMARK_FONTFILE:
            font_esc = WATERMARK_FONTFILE.replace("\\", "/").replace("'", "'\\''")
            parts.append(f"fontfile='{font_esc}'")
        if WATERMARK_SHADOW:
            parts.append("shadowcolor=black@0.6")
            parts.append(f"shadowx={WATERMARK_SHADOW_X}")
            parts.append(f"shadowy={WATERMARK_SHADOW_Y}")
        _log(f"[BRAND] watermark=on text={WATERMARK_TEXT!r} y={wm_y} size={WATERMARK_FONT_SIZE} y_offset={WATERMARK_Y_OFFSET}")
        return ":".join(parts)
    except Exception as e:
        _log(f"[BRAND] watermark=failed reason={e}")
        return ""


def _brand_logo_overlay_filter(logo_input_idx: int, pipeline: str) -> Optional[str]:
    """Build FFmpeg filter to overlay logo on [vout_pre]. Returns None if logo disabled or file missing. Logs [BRAND]."""
    if pipeline == "reddit" and not BRAND_LOGO_ENABLE_REDDIT:
        return None
    if pipeline == "youtube" and not BRAND_LOGO_ENABLE_YOUTUBE:
        return None
    path = BRAND_LOGO_PATH.resolve() if BRAND_LOGO_PATH else None
    if not path or not path.is_file():
        _log("[BRAND] logo=skipped reason=missing_file")
        return None
    try:
        w_main, h_main = 1080, 1920
        logo_w = max(1, int(round(w_main * BRAND_LOGO_SCALE)))
        mx, my = BRAND_LOGO_MARGIN_X, BRAND_LOGO_MARGIN_Y
        # Reddit: ensure logo sits above caption band (no overlap)
        if pipeline == "reddit":
            caption_margin_v = REDDIT_KARAOKE_MARGIN_V
            caption_height_est = 260
            caption_top_y = h_main - caption_margin_v - caption_height_est
            gap = 20
            # Logo bottom = 1920 - my; must be < caption_top_y - gap => my > caption_margin_v + caption_height_est + gap
            min_logo_margin_y = caption_margin_v + caption_height_est + gap
            if my < min_logo_margin_y:
                my = min_logo_margin_y
                _log(f"[BRAND] logo_auto_shifted reason=avoid_caption_overlap new_marginY={my}")
        op = max(0, min(1, BRAND_LOGO_OPACITY))
        # Scale logo; apply opacity
        scale_f = f"[{logo_input_idx}:v]scale={logo_w}:-1[logos]"
        if BRAND_LOGO_SHADOW:
            split_alpha = f"[logos]split[logoa_src][logosh_src];[logoa_src]format=rgba,colorchannelmixer=aa={op}[logoa];[logosh_src]format=rgba,colorchannelmixer=rr=0:gg=0:bb=0:aa=0.4[logosh]"
            # overlay shadow at +4,+4 then logo on top
            if BRAND_LOGO_POS == "bottom_center":
                x_expr = f"(main_w-overlay_w)/2"
                y_expr = f"main_h-overlay_h-{my}"
            elif BRAND_LOGO_POS == "bottom_left":
                x_expr, y_expr = str(mx), f"main_h-overlay_h-{my}"
            elif BRAND_LOGO_POS == "bottom_right":
                x_expr, y_expr = f"main_w-overlay_w-{mx}", f"main_h-overlay_h-{my}"
            elif BRAND_LOGO_POS == "top_left":
                x_expr, y_expr = str(mx), str(my)
            else:
                x_expr, y_expr = f"main_w-overlay_w-{mx}", str(my)
            overlay_shadow = f"[vout_pre][logosh]overlay=x='{x_expr}+4':y='{y_expr}+4'[v2]"
            overlay_logo = f"[v2][logoa]overlay=x='{x_expr}':y='{y_expr}'[vout]"
            parts = [scale_f, split_alpha, overlay_shadow, overlay_logo]
        else:
            alpha_f = f"[logos]format=rgba,colorchannelmixer=aa={op}[logoa]"
            if BRAND_LOGO_POS == "bottom_center":
                x_expr = f"(main_w-overlay_w)/2"
                y_expr = f"main_h-overlay_h-{my}"
            elif BRAND_LOGO_POS == "bottom_left":
                x_expr, y_expr = str(mx), f"main_h-overlay_h-{my}"
            elif BRAND_LOGO_POS == "bottom_right":
                x_expr, y_expr = f"main_w-overlay_w-{mx}", f"main_h-overlay_h-{my}"
            elif BRAND_LOGO_POS == "top_left":
                x_expr, y_expr = str(mx), str(my)
            else:
                x_expr, y_expr = f"main_w-overlay_w-{mx}", str(my)
            overlay_logo = f"[vout_pre][logoa]overlay=x='{x_expr}':y='{y_expr}'[vout]"
            parts = [scale_f, alpha_f, overlay_logo]
        _log(f"[BRAND] logo=on pipeline={pipeline} file={path} pos={BRAND_LOGO_POS} scale={BRAND_LOGO_SCALE} opacity={op}")
        return ";".join(parts)
    except Exception as e:
        _log(f"[BRAND] logo=skipped reason=ffmpeg_error {e}")
        return None


# Impact words: optional emphasis (red + slightly larger) for high-retention terms. Reddit pipeline only.
REDDIT_IMPACT_MODE = (os.environ.get("REDDIT_IMPACT_MODE") or "1").strip() in ("1", "true", "yes")
_reddit_impact_words_default = (
    "cheating,hotel,caught,cops,police,expelled,principal,fired,arrested,broke,blood,knife,gun,found,"
    "texted,screenshot,location,wife,husband,boyfriend,girlfriend,mom,dad,teacher,school,revenge,secret,divorce,affair"
)
_raw_reddit_impact_words = (os.environ.get("REDDIT_IMPACT_WORDS") or _reddit_impact_words_default).strip()
REDDIT_IMPACT_WORDS_SET = frozenset(
    w.strip().lower() for w in _raw_reddit_impact_words.split(",") if w.strip()
) if _raw_reddit_impact_words else frozenset()

# ASS BGR red for impact words (when not active); yellow still wins when word is active
REDDIT_KARAOKE_COLOUR_IMPACT = "&H000000FF"


def _reddit_normalize_word_for_impact(w: str) -> str:
    """Lowercase and strip punctuation for impact-word matching."""
    return "".join(c for c in (w or "").lower() if c.isalnum())


def _write_reddit_ass_karaoke(
    ass_path: Path,
    narration_script: str,
    content_start: float,
    content_dur: float,
    margin_v: int = REDDIT_KARAOKE_MARGIN_V,
) -> bool:
    """Write ASS with word-level karaoke (\\k tags). TikTok-style: each word turns yellow while spoken.
    Returns True on success. Returns False if word count > REDDIT_KARAOKE_MAX_WORDS (caller should fall back to chunk mode)."""
    if not (narration_script or narration_script.strip()) or content_dur <= 0:
        return False
    words = [w.strip() for w in narration_script.strip().split() if w.strip()]
    if not words:
        return False
    if len(words) > REDDIT_KARAOKE_MAX_WORDS:
        _log(f"[REDDIT] subs_mode=fallback_chunk reason=words_over_cap words={len(words)} cap={REDDIT_KARAOKE_MAX_WORDS}")
        return False
    total_words = len(words)
    min_d = REDDIT_WORD_MIN_SEC
    max_d = REDDIT_WORD_MAX_SEC
    # Weights: short tokens (alpha length <= 2) get +25% so they don't flash too fast
    def _alpha_len(w: str) -> int:
        return len("".join(c for c in (w or "") if c.isalpha()))
    weights = [1.25 if _alpha_len(w) <= 2 and _alpha_len(w) > 0 else 1.0 for w in words]
    total_weight = sum(weights)
    if total_weight <= 0:
        total_weight = 1.0
        weights = [1.0] * len(words)
    raw_durs = [content_dur * (w / total_weight) for w in weights]
    # Clamp to [min_d, max_d]; enforce short words at least min_d
    durs = []
    for i, d in enumerate(raw_durs):
        d = max(min_d, min(max_d, d))
        if weights[i] > 1.0:  # short word
            d = max(min_d, d)
        durs.append(d)
    total_raw = sum(durs)
    if total_raw > 0:
        scale = content_dur / total_raw
        durs = [d * scale for d in durs]
    # Easing: reduce jitter with 3-tap blend (0.7 self + 0.15 prev + 0.15 next), then renormalize
    n = len(durs)
    if n >= 2:
        prev = durs.copy()
        for i in range(n):
            left = prev[i - 1] if i > 0 else prev[0]
            right = prev[i + 1] if i + 1 < n else prev[n - 1]
            durs[i] = 0.7 * prev[i] + 0.15 * left + 0.15 * right
        total_smooth = sum(durs)
        if total_smooth > 0:
            durs = [d * (content_dur / total_smooth) for d in durs]
    avg_word_dur = sum(durs) / len(durs) if durs else 0
    _log(f"[REDDIT] subs_offset={REDDIT_SUBS_OFFSET_SEC}s")
    _log(f"[REDDIT] word_dur min={min_d} max={max_d} smooth=on")
    _log(f"[REDDIT] short_word_boost=on")
    _log(f"[REDDIT] subs_mode=karaoke words={total_words} avg_word_dur={avg_word_dur:.3f}")
    word_starts = []
    t = content_start
    for d in durs:
        word_starts.append(t)
        t += d
    word_ends = [word_starts[i] + durs[i] for i in range(len(words))]
    last_end = word_ends[-1] if word_ends else content_start
    if abs(last_end - (content_start + content_dur)) > 1.0:
        _log(f"[REDDIT] karaoke time check: last_end={last_end:.2f} expected={content_start + content_dur:.2f}")
    # Global offset: apply to every start/end we write; clamp start >= intro (content_start), end > start
    subs_offset = REDDIT_SUBS_OFFSET_SEC
    def _apply_offset(t: float) -> float:
        return t + subs_offset
    def _clamp_start(s: float) -> float:
        return max(content_start, s)
    def _clamp_end(s: float, e: float) -> float:
        return max(s + 0.01, e)
    margin_v = max(400, min(640, margin_v))
    font_size = REDDIT_KARAOKE_FONT_SIZE
    script_info = (
        "[Script Info]\r\n"
        "ScriptType: v4.00+\r\n"
        "PlayResX: 1080\r\n"
        "PlayResY: 1920\r\n"
        "\r\n"
    )
    # Single style: PrimaryColour=white (base), SecondaryColour=yellow (karaoke fill). Outline/Shadow punchier. Alignment=2 bottom-center.
    styles = (
        "[V4+ Styles]\r\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\r\n"
        f"Style: Default,{SUBS_FONT},{font_size},{REDDIT_KARAOKE_COLOUR_PRIMARY},{REDDIT_KARAOKE_COLOUR_SECONDARY},&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,{SUBS_OUTLINE},{SUBS_SHADOW},2,80,80,{margin_v},1\r\n"
    )
    if REDDIT_DEBUG_SUBS:
        styles += "Style: Debug,Arial,36,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,7,20,20,30,1\r\n"
    styles += "\r\n"
    events = "[Events]\r\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\r\n"
    lines = [script_info, styles, events]
    num_phrase_lines = 0
    idx = 0
    tag = REDDIT_KARAOKE_TAG
    sample_dialogue = None
    while idx < len(words):
        remaining = len(words) - idx
        line_word_count = min(REDDIT_KARAOKE_WORDS_PER_LINE_CAP, min(REDDIT_KARAOKE_WORDS_PER_LINE_HI, remaining))
        if line_word_count <= 0:
            break
        line_words = words[idx : idx + line_word_count]
        line_durs = durs[idx : idx + line_word_count]
        line_start = word_starts[idx]
        line_end = word_ends[idx + line_word_count - 1]
        start_adj = _clamp_start(_apply_offset(line_start))
        end_adj = _clamp_end(start_adj, _apply_offset(line_end))
        # Build one Dialogue line with \k or \kf per word (centiseconds, integer). No \c overrides.
        parts = []
        for w, d in zip(line_words, line_durs):
            cs = max(1, int(round(d * 100)))
            parts.append(f"{{\\{tag}{cs}}}{w}")
        line_text = " ".join(parts).replace("\\N", " ").replace("\r", "").replace("\n", " ")
        dialogue_line = f"Dialogue: 0,{_ass_time_reddit(start_adj)},{_ass_time_reddit(end_adj)},Default,,0,0,0,,{line_text}\r\n"
        lines.append(dialogue_line)
        if sample_dialogue is None:
            sample_dialogue = line_text.strip()
        num_phrase_lines += 1
        idx += line_word_count
    if REDDIT_DEBUG_SUBS:
        dbg_start = _clamp_start(_apply_offset(content_start))
        dbg_end = _clamp_end(dbg_start, _apply_offset(content_start + 2.0))
        lines.append(f"Dialogue: 0,{_ass_time_reddit(dbg_start)},{_ass_time_reddit(dbg_end)},Debug,,0,0,0,,SUBS_OK\r\n")
    ass_path.write_text("".join(lines), encoding="utf-8")
    content = ass_path.read_text(encoding="utf-8", errors="replace")
    if "\\kf" not in content and "\\k" not in content:
        _log("[REDDIT] subs_mode=fallback_chunk reason=no_karaoke_tags_in_ass")
        return False
    if sample_dialogue:
        _log(f"[REDDIT] karaoke_sample={sample_dialogue[:120]!r}")
    expected_end = content_start + content_dur + subs_offset
    ok_val, err_val, num_lines = _reddit_validate_ass(ass_path, expected_end_sec=expected_end, tolerance_sec=2.0)
    if not ok_val:
        _log(f"[REDDIT] subs_mode=fallback_chunk reason=validation_failed err={err_val}")
        return False
    _log(f"[REDDIT] subs_mode=karaoke words={total_words} lines={num_phrase_lines} tag={tag}")
    _log(f"[REDDIT] subs_written path={ass_path.name} bytes={ass_path.stat().st_size} dialogue_lines={num_lines}")
    _log(f"[SUBS] font={SUBS_FONT} fontsize={font_size} outline={SUBS_OUTLINE} marginV={margin_v}")
    if REDDIT_DEBUG_KARAOKE:
        global _reddit_debug_karaoke_done
        if not _reddit_debug_karaoke_done:
            _reddit_debug_karaoke_done = True
            _reddit_debug_karaoke_clip()
    return True


def _reddit_debug_karaoke_clip() -> None:
    """When REDDIT_DEBUG_KARAOKE=1: write a minimal ASS and render 3s to outputs/debug_karaoke_test.mp4 to confirm karaoke highlight."""
    try:
        OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
        ass_path = OUTPUTS_DIR / "debug_karaoke.ass"
        out_mp4 = OUTPUTS_DIR / "debug_karaoke_test.mp4"
        # One line: "THIS WORD SHOULD TURN YELLOW" with \kf per word (60 centiseconds each over 3s)
        debug_words = ["THIS", "WORD", "SHOULD", "TURN", "YELLOW"]
        cs_per_word = 60  # 0.6s each
        parts = [f"{{\\kf{cs_per_word}}}{w}" for w in debug_words]
        dialogue_text = " ".join(parts)
        font_size = 120
        margin_v = 320
        script_info = (
            "[Script Info]\r\n"
            "ScriptType: v4.00+\r\n"
            "PlayResX: 1080\r\n"
            "PlayResY: 1920\r\n"
            "\r\n"
        )
        styles = (
            "[V4+ Styles]\r\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
            "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding\r\n"
            f"Style: Default,{SUBS_FONT},{font_size},{REDDIT_KARAOKE_COLOUR_PRIMARY},{REDDIT_KARAOKE_COLOUR_SECONDARY},&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,4,2,2,80,80,{margin_v},1\r\n"
            "\r\n"
        )
        events = "[Events]\r\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\r\n"
        line = f"Dialogue: 0,0:00:00.00,0:00:03.00,Default,,0,0,0,,{dialogue_text}\r\n"
        ass_path.write_text(script_info + styles + events + line, encoding="utf-8")
        ass_esc = _reddit_escape_subtitle_path_for_ffmpeg(ass_path)
        cmd = [
            FFMPEG_BIN, "-y",
            "-f", "lavfi", "-i", "color=c=black:s=1080x1920:d=3",
            "-vf", f"ass='{ass_esc}'",
            "-c:v", "libx264", "-preset", "fast",
            str(out_mp4),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, cwd=str(REPO_ROOT))
        if r.returncode == 0:
            _log(f"[REDDIT] debug_karaoke_test written path={out_mp4}")
        else:
            _log(f"[REDDIT] debug_karaoke_test ffmpeg failed: {r.stderr[:500] if r.stderr else r.returncode}")
    except Exception as e:
        _log(f"[REDDIT] debug_karaoke_test error: {e}")


def _reddit_try_karaoke_then_chunk_ass(
    ass_path: Path,
    text: str,
    caption_chunks: list,
    content_start: float,
    content_dur: float,
    duration: float,
    intro_dur: float,
    render_id: str,
    margin_v: Optional[int] = None,
) -> None:
    """Try karaoke ASS first (if REDDIT_SUBS_MODE_KARAOKE and words <= cap), else chunk ASS. Uses cache for both. Raises only if chunk write fails."""
    margin_v = margin_v if margin_v is not None else REDDIT_KARAOKE_MARGIN_V
    words = len((text or "").split())
    use_karaoke = REDDIT_SUBS_MODE_KARAOKE and 0 < words <= REDDIT_KARAOKE_MAX_WORDS
    if use_karaoke:
        cached = _reddit_cache_subs_get(text, duration, intro_dur, margin_v, render_id, karaoke=True)
        if cached:
            return
        if _write_reddit_ass_karaoke(ass_path, text or "", content_start, content_dur, margin_v):
            _reddit_cache_subs_set(text, duration, intro_dur, margin_v, ass_path, karaoke=True)
            return
        _log("[REDDIT] subs_mode=fallback_chunk reason=karaoke_write_or_validation_failed")
    cached = _reddit_cache_subs_get(text, duration, intro_dur, margin_v, render_id, karaoke=False)
    if not cached:
        _write_reddit_ass(ass_path, caption_chunks, content_start, content_dur, margin_v)
        _reddit_cache_subs_set(text, duration, intro_dur, margin_v, ass_path, karaoke=False)


def _write_reddit_ass(ass_path: Path, caption_chunks: list, content_start: float, content_dur: float, margin_v: Optional[int] = None) -> None:
    """Write ASS for TikTok meta: word-weighted, clamp 0.25s–1.10s, max 120 chunks, hook pacing first 3s. Raises if no chunks or content_dur<=0."""
    margin_v = margin_v if margin_v is not None else REDDIT_KARAOKE_MARGIN_V
    margin_v = max(400, min(640, margin_v))
    font_size = REDDIT_KARAOKE_FONT_SIZE
    script_info = (
        "[Script Info]\r\n"
        "ScriptType: v4.00+\r\n"
        "PlayResX: 1080\r\n"
        "PlayResY: 1920\r\n"
        "\r\n"
    )
    # PrimaryColour=white, OutlineColour=yellow, punchier outline/shadow. Alignment=2 bottom-center
    styles = (
        "[V4+ Styles]\r\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\r\n"
        f"Style: Default,{SUBS_FONT},{font_size},&H00FFFFFF,&H000000FF,&H0000FFFF,&H80000000,1,0,0,0,100,100,0,0,1,{SUBS_OUTLINE},{SUBS_SHADOW},2,80,80,{margin_v},1\r\n"
    )
    if REDDIT_DEBUG_SUBS:
        styles += "Style: Debug,Arial,36,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,7,20,20,30,1\r\n"
    styles += "\r\n"
    events = "[Events]\r\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\r\n"
    lines = [script_info, styles, events]
    chunks = []
    for c in (caption_chunks or []):
        text = (c.get("text") or "").strip().replace("\n", "\\N").replace("\r", "")
        if text:
            chunks.append({"text": text})
    if not chunks or content_dur <= 0:
        raise ValueError("[REDDIT] Cannot write ASS: no caption chunks or content_dur<=0")
    # Merge to <= 120 chunks: merge shortest neighboring pairs first
    while len(chunks) > REDDIT_SUBS_MAX_CHUNKS:
        best_i = 0
        best_sum = 999999
        for i in range(len(chunks) - 1):
            w1 = len((chunks[i].get("text") or "").split())
            w2 = len((chunks[i + 1].get("text") or "").split())
            if w1 + w2 < best_sum:
                best_sum = w1 + w2
                best_i = i
        merged_text = (chunks[best_i].get("text") or "").strip() + " " + (chunks[best_i + 1].get("text") or "").strip()
        chunks = chunks[:best_i] + [{"text": merged_text}] + chunks[best_i + 2 :]
    total_words = sum(len((c.get("text") or "").split()) for c in chunks) or 1
    min_dur = REDDIT_SUBS_MIN_DUR
    max_dur = REDDIT_SUBS_MAX_DUR
    raw_durs = []
    for c in chunks:
        w = len((c.get("text") or "").split())
        raw_durs.append(content_dur * (w / total_words))
    total_raw = sum(raw_durs)
    if total_raw <= 0:
        raw_durs = [content_dur / len(chunks)] * len(chunks)
    event_durs = [max(min_dur, min(max_dur, d)) for d in raw_durs]
    # Hook pacing: chunks starting in first 3s get shorter duration (faster)
    t_cur = content_start
    for i in range(len(event_durs)):
        if t_cur < content_start + REDDIT_SUBS_HOOK_SEC:
            event_durs[i] *= REDDIT_SUBS_HOOK_SCALE
        t_cur += event_durs[i]
    scale = content_dur / sum(event_durs) if sum(event_durs) > 0 else 1.0
    event_durs = [d * scale for d in event_durs]
    t = content_start
    for i, c in enumerate(chunks):
        dur = event_durs[i] if i < len(event_durs) else (content_dur / len(chunks))
        end_sec = t + dur
        text = (c.get("text") or "").strip().replace("\n", "\\N").replace("\r", "")
        if text:
            lines.append(f"Dialogue: 0,{_ass_time_reddit(t)},{_ass_time_reddit(end_sec)},Default,,0,0,0,,{text}\r\n")
        t = end_sec
    if REDDIT_DEBUG_SUBS:
        dbg_end = content_start + 2.0
        lines.append(f"Dialogue: 0,{_ass_time_reddit(content_start)},{_ass_time_reddit(dbg_end)},Debug,,0,0,0,,SUBS_OK\r\n")
    ass_path.write_text("".join(lines), encoding="utf-8")
    _log(f"[SUBS] font={SUBS_FONT} fontsize={font_size} outline={SUBS_OUTLINE} marginV={margin_v}")
    _log(f"[REDDIT] subs chunks={len(chunks)} words={total_words} min_dur={min_dur} max_dur={max_dur}")
    expected_end = content_start + content_dur
    ok_val, err_val, num_lines = _reddit_validate_ass(ass_path, expected_end_sec=expected_end, tolerance_sec=2.0)
    if not ok_val:
        raise RuntimeError(f"[REDDIT] ASS validation failed: {err_val}")


def _reddit_escape_subtitle_path_for_ffmpeg(path: Path) -> str:
    """Return path string safe for ffmpeg filter (ass= or subtitles=): forward slashes, escaped colons and single quotes for Windows."""
    raw = str(path.resolve())
    s = raw.replace("\\", "/")
    s = s.replace("'", "'\\''")
    s = s.replace(":", "\\:")
    return s


def _reddit_generate_tag_overlay(render_id: str, title: str) -> Optional[Path]:
    """Generate Reddit-style tag PNG (lower-left card) matching TikTok meta: Snoo in black circle, @handle, title, upvotes/comments/Share."""
    if not REDDIT_TAG_ENABLED:
        return None
    out_path = OVERLAYS_DIR / f"{render_id}_tag.png"
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        _log("[REDDIT] tag_overlay=failed reason=Pillow not installed")
        return None
    try:
        OVERLAYS_DIR.mkdir(parents=True, exist_ok=True)
        w, h = 500, 230
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        margin = 16
        radius = 14
        bg = (32, 32, 35, 240)
        def round_rect(x1, y1, x2, y2, r):
            draw.rectangle([x1 + r, y1, x2 - r, y2], fill=bg)
            draw.rectangle([x1, y1 + r, x2, y2 - r], fill=bg)
            draw.pieslice([x1, y1, x1 + 2 * r, y1 + 2 * r], 180, 270, fill=bg)
            draw.pieslice([x2 - 2 * r, y1, x2, y1 + 2 * r], 270, 360, fill=bg)
            draw.pieslice([x1, y2 - 2 * r, x1 + 2 * r, y2], 90, 180, fill=bg)
            draw.pieslice([x2 - 2 * r, y2 - 2 * r, x2, y2], 0, 90, fill=bg)
        round_rect(0, 0, w, h, radius)
        # Reddit Snoo: black circle, white head, antenna nubs, eyes, smile (matches reference)
        icon_size = 54
        icon_x, icon_y = margin, (h - icon_size) // 2
        draw.ellipse([icon_x, icon_y, icon_x + icon_size, icon_y + icon_size], fill=(0, 0, 0, 255))
        pad = 9
        draw.ellipse([icon_x + pad, icon_y + pad, icon_x + icon_size - pad, icon_y + icon_size - pad], fill=(255, 255, 255, 255))
        draw.ellipse([icon_x + 18, icon_y + 8, icon_x + 24, icon_y + 14], fill=(255, 255, 255, 255))
        draw.ellipse([icon_x + 32, icon_y + 8, icon_x + 38, icon_y + 14], fill=(255, 255, 255, 255))
        draw.ellipse([icon_x + 16, icon_y + 20, icon_x + 24, icon_y + 28], fill=(40, 40, 40, 255))
        draw.ellipse([icon_x + 32, icon_y + 20, icon_x + 40, icon_y + 28], fill=(40, 40, 40, 255))
        draw.arc([icon_x + 14, icon_y + 30, icon_x + 40, icon_y + 46], 0, 180, fill=(40, 40, 40, 255), width=2)
        font_handle = font_title = font_small = None
        try:
            font_handle = ImageFont.truetype("arial.ttf", 20)
            font_title = ImageFont.truetype("arial.ttf", 22)
            font_small = ImageFont.truetype("arial.ttf", 15)
        except Exception:
            try:
                font_handle = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 20)
                font_title = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 22)
                font_small = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 15)
            except Exception:
                font_handle = ImageFont.load_default()
                font_title = font_handle
                font_small = font_handle
        left = icon_x + icon_size + margin
        # @handle (TikTok style: @RequestedReads)
        raw_handle = (REDDIT_TAG_HANDLE or "u/RedditStories").strip()
        display_handle = ("@" + raw_handle.lstrip("u/")) if not raw_handle.startswith("@") else raw_handle
        display_handle = display_handle[:28]
        y_handle = margin + 2
        draw.text((left, y_handle), display_handle, fill=(255, 255, 255, 255), font=font_handle)
        # Verified: small blue circle + check (simple)
        try:
            handle_w = int(font_handle.getlength(display_handle)) if hasattr(font_handle, "getlength") else len(display_handle) * 11
        except Exception:
            handle_w = len(display_handle) * 11
        check_x = left + handle_w + 6
        draw.ellipse([check_x, y_handle + 2, check_x + 14, y_handle + 16], outline=(59, 130, 246, 255), fill=(59, 130, 246, 255), width=1)
        draw.line([(check_x + 3, y_handle + 8), (check_x + 6, y_handle + 12), (check_x + 11, y_handle + 5)], fill=(255, 255, 255, 255), width=2)
        # Story title (multiline)
        title_str = (title or "Reddit Story").strip()
        title_short = title_str[:85] + ("…" if len(title_str) > 85 else "")
        y_title = y_handle + 26
        draw.text((left, y_title), title_short, fill=(255, 255, 255, 255), font=font_title)
        # Bottom row: upvotes, comments, Share
        y_bottom = h - margin - 24
        draw.text((left, y_bottom), "\u2191 " + REDDIT_TAG_UPVOTES, fill=(180, 180, 180, 255), font=font_small)
        draw.text((left + 90, y_bottom), "\u2014 " + REDDIT_TAG_COMMENTS, fill=(180, 180, 180, 255), font=font_small)
        draw.text((w - margin - 42, y_bottom), "Share", fill=(180, 180, 180, 255), font=font_small)
        img.save(out_path, "PNG")
        _log(f"[REDDIT] tag_overlay=on handle={display_handle} png={out_path.name}")
        return out_path
    except Exception as e:
        _log(f"[REDDIT] tag_overlay=failed reason={e}")
        return None


def _reddit_render(
    background_path: Path,
    intro_duration: float,
    audio_path: Path,
    ass_path: Optional[Path],
    srt_shifted_path: Optional[Path],
    title: str,
    output_path: Path,
    duration_sec: float,
    tag_png_path: Optional[Path] = None,
    tag_duration: float = 0,
) -> Tuple[bool, str]:
    """FFmpeg render only. Returns (success, error_message). Optional tag overlay (lower-left) for tag_duration seconds."""
    bg_ffmpeg = str(background_path.resolve()).replace("\\", "/")
    audio_sec = duration_sec
    cmd_inputs = [
        "-stream_loop", "-1", "-i", bg_ffmpeg,
        "-i", str(audio_path),
        "-f", "lavfi", "-i", f"color=c=black@0:d={intro_duration}:s=1080x1920",
    ]
    intro_idx = 2
    tag_idx = 3
    logo_idx = len(cmd_inputs)
    logo_path = BRAND_LOGO_PATH.resolve() if BRAND_LOGO_PATH else None
    use_logo_reddit = BRAND_LOGO_ENABLE_REDDIT and logo_path and logo_path.is_file()
    logo_filter = _brand_logo_overlay_filter(logo_idx, "reddit") if use_logo_reddit else None
    if logo_filter:
        cmd_inputs.extend(["-loop", "1", "-i", str(logo_path)])
    tag_idx = 3 + (1 if logo_filter else 0)
    if tag_png_path and tag_png_path.is_file() and tag_duration > 0:
        cmd_inputs.extend(["-loop", "1", "-i", str(tag_png_path)])
    _log(f"[VIDEO] fill_mode={VERTICAL_FILL_MODE} fit_mode={FIT_MODE}")
    if FIT_MODE == "COVER":
        bed_filter = f"[0:v]trim=start=0:end={audio_sec:.3f},setpts=PTS-STARTPTS,{COVER_CROP}"
        if REDDIT_BG_ZOOM:
            zoom_expr = f"min(1.04,1+0.03*on/(30*{max(1, int(audio_sec))}))"
            bed_filter += f",zoompan=z='{zoom_expr}':d=1:s=1080x1920"
        bed_filter += "[bed]"
    else:
        # FIT_MODE=BLUR_BANDS: blurred background + sharp centered foreground (shared chain for Reddit/YouTube).
        base = f"[0:v]trim=start=0:end={audio_sec:.3f},setpts=PTS-STARTPTS,split[bg][fg]"
        blur_bg = f"[bg]{BLUR_BANDS_BG}[bg]"
        fg_scale = f"[fg]{BLUR_BANDS_FG}[fg]"
        bed_filter = base + ";" + blur_bg + ";" + fg_scale + ";[bg][fg]overlay[bed]"
        if REDDIT_BG_ZOOM:
            zoom_expr = f"min(1.04,1+0.03*on/(30*{max(1, int(audio_sec))}))"
            bed_filter = base + ";" + blur_bg + ";" + fg_scale + ";[bg][fg]overlay[bedz];[bedz]zoompan=z='{zoom_expr}':d=1:s=1080x1920[bed]"
    _log(f"[REDDIT] render duration_sec={audio_sec:.1f} bg_zoom={REDDIT_BG_ZOOM} vignette={REDDIT_VIGNETTE} progress_bar={REDDIT_PROGRESS_BAR}")
    title_esc = _escape_drawtext(title)
    intro_filter = f"[{intro_idx}:v]drawbox=x=110:y=770:w=860:h=380:t=fill:color=black@0.85,drawtext=text='r/Reddit':fontsize=28:fontcolor=0xAAAAAA:x=(w-text_w)/2:y=820,drawtext=text='{title_esc}':fontsize=36:fontcolor=white:x=(w-text_w)/2:y=880,fade=t=out:st={intro_duration - 0.5:.2f}:d=0.5[intro]"
    if ass_path and ass_path.is_file():
        ass_resolved = str(ass_path.resolve())
        ass_esc = _reddit_escape_subtitle_path_for_ffmpeg(ass_path)
        subs_filter = f"ass='{ass_esc}'"
        _log(f"[REDDIT] ffmpeg_ass_path={ass_resolved}")
    elif srt_shifted_path and srt_shifted_path.is_file():
        srt_esc = _reddit_escape_subtitle_path_for_ffmpeg(srt_shifted_path)
        style = f"FontName={SUBS_FONT},FontSize={REDDIT_KARAOKE_FONT_SIZE},Bold=1,Outline={SUBS_OUTLINE},Shadow={SUBS_SHADOW},Alignment=2,MarginV={REDDIT_KARAOKE_MARGIN_V},PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000"
        subs_filter = f"subtitles='{srt_esc}':force_style='{style}'"
    else:
        return (False, "No ASS or SRT subtitles file")
    cap_band = _caption_band_filter()
    wm_filt = _watermark_filter()
    if (BRANDING or WATERMARK_ENABLE) and not wm_filt:
        return (False, "WATERMARK NOT APPLIED")
    filter_video = bed_filter + ";" + intro_filter + ";[bed][intro]overlay=enable='between(t,0," + str(intro_duration) + ")'[v1];"
    filter_video += "[v1]" + (cap_band if cap_band else "scale=iw:ih") + "[v1b];"
    filter_video += "[v1b]" + (wm_filt if wm_filt else "scale=iw:ih") + "[v1c];"
    filter_video += "[v1c]" + subs_filter + "[vout]"
    render_id_stamp = output_path.stem
    if DEBUG_RENDER_STAMP:
        parts = filter_video.rsplit("[vout]", 1)
        filter_video = parts[0] + "[vout_s];[vout_s]" + _debug_stamp_filter(render_id_stamp) + "[vout]" + (parts[1] if len(parts) > 1 else "")
    if REDDIT_VIGNETTE:
        filter_video += ";[vout]vignette=angle=PI/6[vout]"
    if REDDIT_PROGRESS_BAR:
        filter_video += ";[vout]drawbox=x=0:y=100:w='min(1080,1080*t/" + str(audio_sec) + ")':h=3:t=fill:color=white@0.6[vout]"
    if tag_png_path and tag_png_path.is_file() and tag_duration > 0:
        tag_x, tag_y = 32, 1350
        # Tag visible for full video (tag_duration = full length when passed from pipeline)
        filter_video += ";[vout][" + str(tag_idx) + ":v]overlay=x=" + str(tag_x) + ":y=" + str(tag_y) + ":enable='between(t,0," + str(tag_duration) + ")'[vout]"
    if logo_filter:
        # Last filter output was [vout]; make it [vout_pre] and overlay logo -> [vout]
        filter_video = filter_video.rsplit("[vout]", 1)[0] + "[vout_pre]" + filter_video.rsplit("[vout]", 1)[1]
        filter_video += ";" + logo_filter
    if FIT_MODE == "COVER" and COVER_CROP not in filter_video:
        _log("[RENDER] COVER CROP NOT APPLIED — STOP")
        return (False, "COVER CROP NOT APPLIED — STOP")
    _render_filter_checks(filter_video, "REDDIT_A", None)
    ok, err = build_and_run_final_ffmpeg_render(
        cmd_inputs, filter_video, output_path, audio_sec, map_audio="1:a", filter_audio=None,
    )
    if not ok and err:
        if "No such file" in err or "Invalid data" in err or "could not open" in err.lower():
            err = "Could not open gameplay background file. Check assets/gameplay/ has a valid .mp4. " + err
    return (ok, err or "")


def _reddit_spoken_script_postprocess(script: str) -> str:
    """Make script more natural for TTS: shorter sentences, pause punctuation, line breaks, remove symbols TTS reads aloud."""
    if not (script or script.strip()):
        return script
    s = script.strip()
    # Remove symbols and patterns TTS would read literally
    s = re.sub(r"https?://\S+", "", s)
    s = re.sub(r"@\w+", "", s)
    s = re.sub(r"#\w+", "", s)
    s = re.sub(r"\*+", " ", s)
    s = re.sub(r"[_\-\u2013\u2014]{2,}", ", ", s)
    s = re.sub(r"\.{4,}", "…", s)
    s = re.sub(r"\s+", " ", s).strip()
    # Split on sentence boundaries
    sentences = re.split(r"(?<=[.!?])\s+", s)
    out = []
    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        words = sent.split()
        if len(words) <= 18:
            out.append(sent)
            continue
        # Break long sentence at commas
        parts = re.split(r"\s*,\s*", sent)
        current = ""
        for part in parts:
            part = part.strip()
            if not part:
                continue
            if not current:
                current = part
            else:
                candidate = current + ", " + part
                if len(candidate.split()) <= 16:
                    current = candidate
                else:
                    if current:
                        out.append((current.rstrip(".,") or current) + ".")
                    current = part
        if current:
            out.append((current.rstrip(".,") or current) + ".")
    # Join with space; line break every 1-2 sentences
    blocks = []
    for i in range(0, len(out), 2):
        blocks.append(" ".join(out[i : i + 2]))
    result = "\n\n".join(blocks).strip()
    return result if result else s


def _reddit_chunks_from_script(narration_script: str) -> list:
    """Deterministic caption chunks for TikTok meta: 5-8 words (cap 10), split on punctuation, avoid 1-2 word orphans. Returns list of {text: str}."""
    if not (narration_script or narration_script.strip()):
        return []
    text = narration_script.strip()
    target_lo, target_hi, hard_cap = 5, 8, 10
    # Split on sentence boundaries, then on comma/semicolon for phrases
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks = []
    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        # Sub-phrases on comma/semicolon
        phrases = re.split(r"\s*[,;]\s+", sent)
        for phrase in phrases:
            phrase = phrase.strip()
            if not phrase:
                continue
            words = phrase.split()
            i = 0
            while i < len(words):
                remaining = len(words) - i
                if remaining <= 0:
                    break
                # Orphan 1-2 words: merge into previous chunk if possible (unless punch word)
                if remaining <= 2 and chunks:
                    prev = (chunks[-1].get("text") or "").strip()
                    prev_n = len(prev.split())
                    punch = remaining == 1 and phrase.strip().endswith((".", "!", "?"))
                    if not punch and prev_n + remaining <= hard_cap:
                        chunks[-1]["text"] = prev + " " + " ".join(words[i:])
                        i = len(words)
                        continue
                size = min(hard_cap, max(target_lo, min(target_hi, remaining)))
                if remaining < target_lo and remaining <= 2 and chunks:
                    prev = (chunks[-1].get("text") or "").strip()
                    if len(prev.split()) + remaining <= hard_cap:
                        chunks[-1]["text"] = prev + " " + " ".join(words[i:])
                        i = len(words)
                        continue
                chunk_words = words[i : i + size]
                i += size
                if chunk_words:
                    chunks.append({"text": " ".join(chunk_words)})
    return chunks


def _reddit_openai_enhance(story_text: str) -> Optional[dict]:
    """Call OpenAI to enhance story: title, hook_line, narration_script, hashtags (TikTok Drama Mode). Returns None on failure."""
    if not OPENAI_ENABLED or not story_text or len(story_text) > REDDIT_MAX_STORY_CHARS:
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        truncated = story_text[:REDDIT_MAX_STORY_CHARS] if len(story_text) > REDDIT_MAX_STORY_CHARS else story_text
        system = (
            "You are a TikTok drama script writer. You rewrite Reddit stories into aggressive, high-retention, SPOKEN drama scripts. "
            "Output valid JSON only, no markdown. No caption timings or chunk arrays.\n\n"
            "Required JSON keys:\n"
            "- title (string): short hook for the video.\n"
            "- hook_line (string): the first line spoken. Max 10-12 words. Create tension immediately. No filler, no context buildup. Examples: 'I caught my girlfriend at a hotel.' 'My principal expelled me for this.' 'My neighbor was digging at 3AM.'\n"
            "- narration_script (string): full script for voiceover. STRICT RULES:\n"
            "  1) HOOK: First line = hook_line. Max 10-12 words, immediate tension.\n"
            "  2) SENTENCE STYLE: Short, punchy. 3-10 words per sentence. Break long sentences into fragments. Use line breaks frequently. Spoken style, not written. No formal Reddit tone.\n"
            "  3) ESCALATION STRUCTURE: Hook -> Context (fast) -> Escalation -> Tension spike -> MICRO CLIFFHANGER (e.g. 'And that's when I saw it.' 'That's when everything changed.' 'I wish I never opened it.') -> Reveal -> Aftershock line.\n"
            "  4) Before the reveal, insert a tension beat like 'And that's when I saw it.' or 'That's when everything changed.'\n"
            "  5) DRAMA STYLE: Allow 1-3 word sentences for impact. Isolate punch words. Remove fluff. No long paragraphs. No overly descriptive filler.\n"
            "  6) PUNCH WORD ISOLATION: Important moments on their own line. Example: 'At a hotel.' 'With another guy.' 'At 3:17 AM.'\n"
            "  7) LENGTH: Aim for 50-70 seconds at 1.20x speed. Do not exceed unless the story truly requires it.\n"
            "- hashtags (array of 10-15 strings).\n\n"
            "Do not change the core facts. No profanity unless in source. Narration_script must be the exact text to be read aloud; short sentences and line breaks for pacing."
        )
        resp = client.chat.completions.create(
            model=OPENAI_ENHANCE_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": truncated},
            ],
            max_tokens=4000,
            temperature=0.3,
        )
        content = (resp.choices[0].message.content or "").strip()
        if not content:
            return None
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()
        data = json.loads(content)
        if not isinstance(data, dict):
            return None
        _log("[REDDIT] rewrite_mode=tiktok_drama")
        return data
    except Exception as e:
        _log(f"[REDDIT] OpenAI enhance failed: {e}")
        return None


def _reddit_openai_tts(script: str, out_path: Path, voice: Optional[str] = None) -> Tuple[bool, Optional[float], str]:
    """Synthesize script with OpenAI Audio TTS. Save to out_path (mp3). Return (success, duration_sec, error)."""
    if not OPENAI_ENABLED or not script or not out_path:
        return (False, None, "OpenAI not configured or empty script")
    voice = (voice or REDDIT_TTS_VOICE).strip().lower() or "nova"
    _log(f"[REDDIT] tts voice={voice}")
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.audio.speech.create(
            model="tts-1-hd",
            voice=voice,
            input=script[:4096],
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(response.content)
        duration = _get_audio_duration_ffprobe(out_path)
        return (True, duration, "")
    except Exception as e:
        err = str(e).strip()[:200]
        _log(f"[REDDIT] OpenAI TTS failed: {e}")
        return (False, None, err)


def _run_reddit_pipeline(job_id: str, story_text: str, gameplay_id: str, options: dict) -> None:
    """Background pipeline: clean -> (OpenAI enhance optional) -> TTS -> subtitles/ASS -> render. Updates reddit_jobs[job_id]."""
    intro_dur = REDDIT_INTRO_DURATION
    use_openai = OPENAI_ENABLED
    story_limited = story_text[:REDDIT_MAX_STORY_CHARS] if len(story_text) > REDDIT_MAX_STORY_CHARS else story_text
    render_id = job_id
    with reddit_jobs_lock:
        reddit_jobs[job_id] = {"stage": "clean", "progress": 5, "message": "Cleaning text…", "done": False, "error": None, "mp4_url": None, "render_id": render_id}
    try:
        title, body = _parse_story_title_body(story_limited)
        text = _clean_story_text(body)
        if not text:
            with reddit_jobs_lock:
                reddit_jobs[job_id].update({"stage": "error", "done": True, "error": "Story text is empty after cleaning.", "progress": 0})
            return
        enhanced = None
        if use_openai:
            with reddit_jobs_lock:
                reddit_jobs[job_id].update({"stage": "clean", "progress": 8, "message": "Enhancing with OpenAI…"})
            enhanced = _reddit_cache_enhance_get(story_limited)
            if not enhanced:
                enhanced = _reddit_openai_enhance(story_limited)
                if enhanced:
                    _reddit_cache_enhance_set(story_limited, enhanced)
            if enhanced:
                title = (enhanced.get("title") or title or "Reddit Story")[:120]
                text = (enhanced.get("narration_script") or text).strip()
                if not text:
                    text = _clean_story_text(body)
                    enhanced = None
                else:
                    text = _reddit_spoken_script_postprocess(text)
                    _log(f"[REDDIT] enhanced words={len(text.split())} chars={len(text)}")

        with reddit_jobs_lock:
            reddit_jobs[job_id].update({"stage": "tts", "progress": 15, "message": "Generating voice…"})

        backgrounds = _list_gameplay_backgrounds()
        entry = next((e for e in backgrounds if isinstance(e, dict) and e.get("id") == gameplay_id), None)
        if not entry or not entry.get("file"):
            with reddit_jobs_lock:
                reddit_jobs[job_id].update({"stage": "error", "done": True, "error": "No gameplay background selected or file missing in assets/gameplay/", "progress": 0})
            return
        gameplay_path = (GAMEPLAY_DIR / entry["file"]).resolve()
        if not gameplay_path.is_file() or gameplay_path.stat().st_size < 1000:
            with reddit_jobs_lock:
                reddit_jobs[job_id].update({"stage": "error", "done": True, "error": "No gameplay background selected or file missing in assets/gameplay/", "progress": 0})
            return
        bg_path_ffmpeg = str(gameplay_path).replace("\\", "/")
        _log(f"[REDDIT] start render_id={render_id} openai={use_openai} background={gameplay_path.name}")

        for d in (TTS_DIR, SUBS_DIR, RENDERS_DIR):
            d.mkdir(parents=True, exist_ok=True)
        audio_path = TTS_DIR / f"{render_id}.wav"
        use_ass = False
        ass_path = SUBS_DIR / f"{render_id}.ass"
        caption_chunks = _reddit_chunks_from_script(text) if (text and text.strip()) else None

        if enhanced and use_openai:
            cached = _reddit_cache_tts_get(text, render_id)
            if cached:
                audio_path, duration, used_normalized = cached
                _log(f"[REDDIT] tts_speed={REDDIT_TTS_SPEED}")
                duration_source = "normalized" if used_normalized else "raw"
                normalized_path = TTS_DIR / f"{render_id}_normalized.wav"
                if not used_normalized:
                    ok_chain, chain_dur, chain_stages = _reddit_apply_audio_chain(audio_path, normalized_path, duration, render_id)
                    _log(f"[REDDIT] audio_chain={'|'.join(chain_stages) or 'none'}")
                    if ok_chain and normalized_path.is_file():
                        audio_path = normalized_path
                        if chain_dur and chain_dur > 0:
                            duration = chain_dur
                        used_normalized = True
                        duration_source = "normalized"
                        _log(f"[REDDIT] audio=normalized file={normalized_path.name}")
                    else:
                        duration = _get_audio_duration_ffprobe(audio_path) if audio_path.is_file() else duration
                        duration_source = "raw"
                content_start = intro_dur
                content_dur = max(0.1, duration - intro_dur)
                if caption_chunks:
                    _reddit_try_karaoke_then_chunk_ass(
                        ass_path, text, caption_chunks, content_start, content_dur,
                        duration, intro_dur, render_id,
                    )
                use_ass = bool(caption_chunks)
                _log(f"[REDDIT] tts=ok file={audio_path.name}")
                if REDDIT_TTS_SPEED <= 1.0 and used_normalized:
                    _log(f"[REDDIT] audio=normalized file={audio_path.name}")
                _log(f"[REDDIT] duration={duration:.2f}s source={duration_source}")
                if use_ass:
                    _log(f"[REDDIT] subs chunks={len(caption_chunks)} ass={ass_path.name}")
            else:
                if REDDIT_TTS_VOICE_LIST:
                    hook_line = (enhanced.get("hook_line") or "").strip() if enhanced else ""
                    hook_words = (hook_line or " ".join(text.split()[:18])).split()[:18]
                    hook_text = " ".join(hook_words)
                    if hook_text:
                        TTS_SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
                        for voice in REDDIT_TTS_VOICE_LIST[:3]:
                            sample_path = TTS_SAMPLES_DIR / f"{render_id}_hook_{voice}.mp3"
                            _reddit_openai_tts(hook_text, sample_path, voice)
                ok_tts, duration, err_tts = _reddit_openai_tts(text, TTS_DIR / f"{render_id}.mp3")
                if ok_tts and duration and duration > 0:
                    audio_path = TTS_DIR / f"{render_id}.mp3"
                    normalized_path = TTS_DIR / f"{render_id}_normalized.wav"
                    _log(f"[REDDIT] tts_speed={REDDIT_TTS_SPEED}")
                    ok_chain, chain_dur, chain_stages = _reddit_apply_audio_chain(audio_path, normalized_path, duration, render_id)
                    _log(f"[REDDIT] audio_chain={'|'.join(chain_stages) or 'none'}")
                    used_normalized = False
                    duration_source = "raw"
                    if ok_chain and normalized_path.is_file():
                        audio_path = normalized_path
                        if chain_dur and chain_dur > 0:
                            duration = chain_dur
                        used_normalized = True
                        duration_source = "normalized"
                        _log(f"[REDDIT] audio=normalized file={normalized_path.name}")
                    else:
                        duration = _get_audio_duration_ffprobe(audio_path) if audio_path.is_file() else duration
                    _reddit_cache_tts_set(text, TTS_DIR / f"{render_id}.mp3", normalized_path if used_normalized else None)
                    content_start = intro_dur
                    content_dur = max(0.1, duration - intro_dur)
                    if caption_chunks:
                        _reddit_try_karaoke_then_chunk_ass(
                            ass_path, text, caption_chunks, content_start, content_dur,
                            duration, intro_dur, render_id,
                        )
                    use_ass = bool(caption_chunks)
                    _log(f"[REDDIT] tts=ok file={audio_path.name}")
                    _log(f"[REDDIT] duration={duration:.2f}s source={duration_source}")
                    if use_ass:
                        _log(f"[REDDIT] subs chunks={len(caption_chunks)} ass={ass_path.name}")
                else:
                    enhanced = None
                    caption_chunks = _reddit_chunks_from_script(text) if (text and text.strip()) else None
                    _log(f"[REDDIT] OpenAI TTS fallback to local: {err_tts or 'no duration'}")

        if not use_ass:
            p_bin, p_model = _get_piper_paths()
            if not p_model or not p_model.is_file():
                with reddit_jobs_lock:
                    reddit_jobs[job_id].update({"stage": "error", "done": True, "error": "Piper not configured", "progress": 0})
                return
            voice_model = str(p_model)
            ok, err = _run_piper_to_file(text, audio_path, voice_model)
            if not ok:
                with reddit_jobs_lock:
                    reddit_jobs[job_id].update({"stage": "error", "done": True, "error": err or "TTS failed", "progress": 0})
                return
            _log("[REDDIT] engine=local (Piper)")

        duration = _get_audio_duration_ffprobe(audio_path)
        if not use_ass and duration:
            _log(f"[REDDIT] duration={duration:.2f}s source=raw")
        if not use_ass and caption_chunks and duration and duration > 0:
            content_start = intro_dur
            content_dur = max(0.1, duration - intro_dur)
            try:
                _reddit_try_karaoke_then_chunk_ass(
                    ass_path, text, caption_chunks, content_start, content_dur,
                    duration, intro_dur, render_id,
                )
                use_ass = True
                _log(f"[REDDIT] subs from script ass={ass_path.name}")
            except Exception as e:
                _log(f"[REDDIT] ASS write failed (will try Whisper): {e}")
        if duration is None or duration <= 0:
            with reddit_jobs_lock:
                reddit_jobs[job_id].update({"stage": "error", "done": True, "error": "Could not get audio duration", "progress": 0})
            return
        if use_ass:
            ok_val, err_val, num_lines = _reddit_validate_ass(ass_path)
            if not ok_val:
                _log(f"[REDDIT] ASS validation failed before render: {err_val}")
                with reddit_jobs_lock:
                    reddit_jobs[job_id].update({"stage": "error", "done": True, "error": f"Subtitles failed: {err_val}", "progress": 0})
                return
            _log(f"[REDDIT] subs_written path={ass_path.name} bytes={ass_path.stat().st_size} lines={num_lines}")
        if not use_ass:
            with reddit_jobs_lock:
                reddit_jobs[job_id].update({"stage": "subtitles", "progress": 45, "message": "Generating subtitles…"})
            srt_path = SUBS_DIR / f"{render_id}.srt"
            ok_srt, err_srt = _whisper_to_srt(audio_path, srt_path)
            if not ok_srt:
                with reddit_jobs_lock:
                    reddit_jobs[job_id].update({
                        "stage": "error", "done": True,
                        "error": "Subtitle generation failed" + (f": {err_srt}" if err_srt else ""),
                        "progress": 0,
                    })
                return
            srt_shifted = SUBS_DIR / f"{render_id}_shifted.srt"
            _shift_srt(srt_path, intro_dur, srt_shifted)
        with reddit_jobs_lock:
            reddit_jobs[job_id].update({"stage": "render", "progress": 60, "message": "Rendering video…"})

        mp4_path = RENDERS_DIR / f"{render_id}.mp4"
        srt_shifted = SUBS_DIR / f"{render_id}_shifted.srt" if not use_ass else None
        tag_png = _reddit_generate_tag_overlay(render_id, title)
        ok_render, err_render = _reddit_render(
            gameplay_path, intro_dur, audio_path,
            ass_path if use_ass else None,
            srt_shifted,
            title, mp4_path, duration,
            tag_png_path=tag_png,
            tag_duration=duration if tag_png else 0,
        )
        if not ok_render:
            with reddit_jobs_lock:
                reddit_jobs[job_id].update({"stage": "error", "done": True, "error": err_render, "progress": 0})
            return
        if not mp4_path.is_file():
            with reddit_jobs_lock:
                reddit_jobs[job_id].update({"stage": "error", "done": True, "error": "Render succeeded but output file not found", "progress": 0})
            return
        mp4_url = f"/outputs/renders/{render_id}.mp4"
        out_abs = str(mp4_path.resolve())
        _log(f"[RENDER] out_abs={out_abs}")
        _log(f"[REDDIT] render=ok out={mp4_path.name} out_abs={out_abs}")
        with reddit_jobs_lock:
            reddit_jobs[job_id].update({"stage": "done", "progress": 100, "message": "Done", "done": True, "mp4_url": mp4_url, "render_id": render_id})
    except Exception as e:
        _log(f"[REDDIT] pipeline error: {e}")
        with reddit_jobs_lock:
            if job_id in reddit_jobs:
                reddit_jobs[job_id].update({"stage": "error", "done": True, "error": str(e)[:300], "progress": 0})


@app.get("/api/reddit/config")
async def reddit_config():
    """Return { openai_enabled: bool } for Reddit Video Builder UI (e.g. show/default Use OpenAI checkbox)."""
    return {"openai_enabled": OPENAI_ENABLED}


@app.post("/api/reddit/enhance")
async def api_reddit_enhance(request: Request):
    """
    Enhance story with OpenAI: title, intro_line, narration_script, post_caption, hashtags. caption_chunks are derived deterministically from narration_script.
    Body: { story_text: string }. Returns JSON or 503 if OpenAI not configured / call fails.
    """
    if not OPENAI_ENABLED:
        return JSONResponse(content={"error": "OpenAI not configured", "detail": "Set OPENAI_API_KEY."}, status_code=503)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON body required")
    story_text = (body.get("story_text") or body.get("text") or "").strip()
    if not story_text:
        raise HTTPException(status_code=400, detail="story_text is required")
    if len(story_text) > REDDIT_MAX_STORY_CHARS:
        return JSONResponse(
            content={"error": "Story too long", "detail": f"Max {REDDIT_MAX_STORY_CHARS} characters. Shorten and try again."},
            status_code=400,
        )
    enhanced = _reddit_openai_enhance(story_text)
    if not enhanced:
        return JSONResponse(content={"error": "Enhance failed", "detail": "OpenAI call failed or returned invalid data."}, status_code=503)
    script = enhanced.get("narration_script") or ""
    return {
        "title": enhanced.get("title") or "Reddit Story",
        "hook_line": enhanced.get("hook_line") or "",
        "intro_line": enhanced.get("hook_line") or enhanced.get("intro_line") or "",
        "narration_script": script,
        "caption_chunks": _reddit_chunks_from_script(script),
        "post_caption": "",
        "hashtags": enhanced.get("hashtags") or [],
    }


@app.get("/api/reddit/status/{job_id}")
async def reddit_status(job_id: str):
    """Poll Reddit generation progress. Returns { stage, progress, message, done, error, mp4_url, render_id, output_file, output_abs_path, success }."""
    with reddit_jobs_lock:
        j = reddit_jobs.get(job_id)
    if not j:
        return JSONResponse(content={"error": "Job not found"}, status_code=404)
    out = {
        "stage": j.get("stage", "unknown"),
        "progress": j.get("progress", 0),
        "message": j.get("message", ""),
        "done": j.get("done", False),
        "error": j.get("error"),
        "mp4_url": j.get("mp4_url"),
        "render_id": j.get("render_id"),
    }
    if j.get("done") and j.get("render_id"):
        out["output_file"] = f"{j['render_id']}.mp4"
        out["output_abs_path"] = str(RENDERS_DIR.resolve() / out["output_file"])
        out["success"] = not j.get("error")
    return out


@app.post("/api/reddit/generate")
async def api_reddit_generate(request: Request):
    """
    Start Reddit video generation. Body: { story_text: string, gameplay: string, options?: { horror?: bool } }.
    Returns { job_id, status_url }. Poll GET /api/reddit/status/<job_id> for progress.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON body required")
    story_text = (body.get("story_text") or body.get("text") or "").strip()
    if not story_text:
        raise HTTPException(status_code=400, detail="story_text is required")
    gameplay = (body.get("gameplay") or body.get("gameplay_id") or "").strip()
    if not gameplay:
        raise HTTPException(status_code=400, detail="No gameplay background selected or file missing in assets/gameplay/")
    options = body.get("options") or {}
    options["use_openai"] = OPENAI_ENABLED
    backgrounds = _list_gameplay_backgrounds()
    entry = next((e for e in backgrounds if isinstance(e, dict) and e.get("id") == gameplay), None)
    if not entry or not entry.get("file"):
        raise HTTPException(status_code=400, detail="No gameplay background selected or file missing in assets/gameplay/")
    gameplay_path = GAMEPLAY_DIR / entry["file"] if GAMEPLAY_DIR else Path()
    try:
        gameplay_resolved = gameplay_path.resolve()
        if not gameplay_resolved.is_file() or gameplay_resolved.stat().st_size < 1000:
            raise HTTPException(status_code=400, detail="No gameplay background selected or file missing in assets/gameplay/")
        if GAMEPLAY_DIR and not str(gameplay_resolved).startswith(str(GAMEPLAY_DIR.resolve())):
            raise HTTPException(status_code=400, detail="No gameplay background selected or file missing in assets/gameplay/")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="No gameplay background selected or file missing in assets/gameplay/")
    ok, err_msg = _piper_tts_available()
    if not ok:
        return JSONResponse(content={"error": "tts_unavailable", "detail": err_msg}, status_code=503)
    job_id = uuid.uuid4().hex[:12]
    with reddit_jobs_lock:
        reddit_jobs[job_id] = {"stage": "queued", "progress": 0, "message": "Starting…", "done": False, "error": None, "mp4_url": None, "render_id": None}
    thread = threading.Thread(target=_run_reddit_pipeline, args=(job_id, story_text, gameplay, options))
    thread.daemon = True
    thread.start()
    return JSONResponse(content={
        "job_id": job_id,
        "status_url": f"/api/reddit/status/{job_id}",
    })


@app.get("/api/gameplay")
async def api_gameplay():
    """Return list of gameplay backgrounds from manifest + scanned .mp4 files."""
    return {"gameplay": _list_gameplay_backgrounds()}


@app.get("/api/backgrounds")
async def api_backgrounds():
    """Return list of available gameplay .mp4 files (manifest + disk). Same shape as /api/gameplay."""
    return {"gameplay": _list_gameplay_backgrounds()}


def _slug_for_background(s: str, max_len: int = 60) -> str:
    """Safe filesystem slug: lowercase, alphanumeric and underscore/hyphen only."""
    if not (s or "").strip():
        return "background"
    slug = re.sub(r"[^a-z0-9_-]", "_", (s or "").strip().lower())
    slug = slug.strip("_")[:max_len] or "background"
    return slug


@app.post("/api/backgrounds/add")
async def api_backgrounds_add(request: Request):
    """
    Download a video from URL into assets/gameplay/ as .mp4. Body: { url: string, name?: string }.
    Returns { id, name, file } for the new background. Uses yt-dlp (prefer mp4).
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON body required")
    url = (body.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="url is required")
    custom_name = (body.get("name") or "").strip()
    if not GAMEPLAY_DIR:
        raise HTTPException(status_code=500, detail="Gameplay assets directory not configured")
    GAMEPLAY_DIR.mkdir(parents=True, exist_ok=True)
    ytdlp_bin = "yt-dlp"
    try:
        title_out = subprocess.run(
            [ytdlp_bin, "--print", "title", "--no-warnings", "--no-download", "--no-playlist", url],
            capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
        )
        title = (title_out.stdout or "").strip() if title_out.returncode == 0 else ""
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="yt-dlp not found. Install it (e.g. pip install yt-dlp).")
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="URL timed out (title fetch).")
    base_slug = _slug_for_background(custom_name or title or "gameplay")
    stem = base_slug
    out_path = GAMEPLAY_DIR / (stem + ".mp4")
    n = 0
    while out_path.exists():
        n += 1
        stem = f"{base_slug}_{n}"
        out_path = GAMEPLAY_DIR / (stem + ".mp4")
    cmd = [
        ytdlp_bin,
        "--merge-output-format", "mp4",
        "-o", str(out_path),
        "--no-warnings",
        "--socket-timeout", "30",
        "--retries", "3",
        "--no-playlist",
        url,
    ]
    _log(f"[BACKGROUNDS] yt-dlp download starting (may take 1–2 min for long videos): {url[:60]}...")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, cwd=str(REPO_ROOT))
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "Unknown error").strip()[:400]
        _log(f"[BACKGROUNDS] yt-dlp failed: {err}")
        raise HTTPException(status_code=422, detail=f"Download failed: {err}")
    if not out_path.is_file() or out_path.stat().st_size < 1000:
        raise HTTPException(status_code=422, detail="Download did not produce a valid video file.")
    _log(f"[BACKGROUNDS] download complete: {stem}.mp4")
    return {"id": stem, "name": stem, "file": out_path.name}


@app.post("/api/render_reddit_video")
async def api_render_reddit_video(request: Request):
    """
    Render full Reddit story video: TTS -> Whisper SRT -> FFmpeg with gameplay background.
    Body: { text, gameplay_id, max_sec (default 240), autofit (default true) }.
    Returns { ok, render_id, mp4_url, filename }.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON body required")
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    gameplay_id = (body.get("gameplay_id") or "").strip()
    max_sec = float(body.get("max_sec") or 240)
    autofit = bool(body.get("autofit", True))

    manifest = _load_gameplay_manifest()
    entry = next((e for e in manifest if isinstance(e, dict) and e.get("id") == gameplay_id), None)
    if not entry or not entry.get("file"):
        raise HTTPException(status_code=400, detail="gameplay_id not found in manifest")
    gameplay_file = entry["file"]
    gameplay_path = GAMEPLAY_DIR / gameplay_file if GAMEPLAY_DIR else Path()
    if not gameplay_path.is_file() or gameplay_path.stat().st_size < 1000:
        return JSONResponse(
            content={"error": "Gameplay file missing or placeholder", "detail": "Replace webapp/assets/gameplay/*.mp4 with real videos."},
            status_code=400,
        )

    ok, err_msg = _piper_tts_available()
    if not ok:
        return JSONResponse(content={"error": "tts_unavailable", "detail": err_msg}, status_code=503)
    _, p_model = _get_piper_paths()
    if not p_model or not p_model.is_file():
        return JSONResponse(content={"error": "PIPER_MODEL not set or missing."}, status_code=503)
    voice_model = str(p_model)

    render_id = uuid.uuid4().hex[:12]
    for d in (TTS_DIR, SUBS_DIR, RENDERS_DIR):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            _log(f"[RENDER] mkdir {d}: {e}")
            return JSONResponse(content={"error": "Could not create output dirs"}, status_code=500)

    wav_path = TTS_DIR / f"{render_id}.wav"
    ok, err = _run_piper_to_file(text, wav_path, voice_model)
    if not ok:
        return JSONResponse(content={"error": "TTS failed", "detail": err}, status_code=500)

    duration = _get_audio_duration_ffprobe(wav_path)
    if duration is None or duration <= 0:
        return JSONResponse(content={"error": "Could not get audio duration"}, status_code=500)
    tempo = 1.0
    if duration > max_sec and autofit:
        tempo = min(2.0, duration / max_sec)
    final_duration = duration / tempo
    if final_duration > max_sec:
        return JSONResponse(
            content={"error": "Too long", "detail": f"After speed-up still {final_duration:.0f}s. Trim text or increase max_sec."},
            status_code=400,
        )

    srt_path = SUBS_DIR / f"{render_id}.srt"
    ok_srt, err_srt = _whisper_to_srt(wav_path, srt_path)
    if not ok_srt:
        return JSONResponse(content={"error": "Subtitle generation failed", "detail": err_srt or "Whisper failed."}, status_code=500)
    srt_use = srt_path
    if tempo != 1.0:
        srt_scaled = SUBS_DIR / f"{render_id}_scaled.srt"
        _scale_srt(srt_path, 1.0 / tempo, srt_scaled)
        srt_use = srt_scaled

    try:
        bg_dur_out = subprocess.run(
            [FFPROBE_BIN, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(gameplay_path)],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(REPO_ROOT),
        )
        bg_dur = float(bg_dur_out.stdout.strip()) if bg_dur_out.returncode == 0 and bg_dur_out.stdout.strip() else 60.0
    except Exception:
        bg_dur = 60.0
    audio_sec = final_duration
    bg_sec = max(1.0, bg_dur)
    start_offset = random.uniform(0, max(0, bg_sec - 10)) if bg_sec > 10 else 0
    first_seg_len = bg_sec - start_offset
    xfade_dur = 0.4
    target_bed_sec = audio_sec + 2.0
    seg_len_effective = bg_sec - xfade_dur
    if seg_len_effective <= 0:
        num_segments = 2
    else:
        num_segments = max(2, min(10, int((target_bed_sec - first_seg_len) / seg_len_effective) + 2))
    num_segments = max(2, num_segments)

    cmd_inputs = []
    for i in range(num_segments):
        if i == 0:
            cmd_inputs.extend(["-ss", str(start_offset), "-t", str(first_seg_len), "-i", str(gameplay_path)])
        else:
            cmd_inputs.extend(["-ss", "0", "-t", str(bg_sec), "-i", str(gameplay_path)])
    cmd_inputs.extend(["-i", str(wav_path)])
    logo_idx_yt = num_segments + 1
    logo_path_yt = BRAND_LOGO_PATH.resolve() if BRAND_LOGO_PATH else None
    use_logo_youtube = BRAND_LOGO_ENABLE_YOUTUBE and logo_path_yt and logo_path_yt.is_file()
    logo_filter_yt = _brand_logo_overlay_filter(logo_idx_yt, "youtube") if use_logo_youtube else None
    if logo_filter_yt:
        cmd_inputs.extend(["-loop", "1", "-i", str(logo_path_yt)])

    # Per-segment: COVER = scale+crop 9:16; BLUR_BANDS = blur bg + sharp centered fg (same as Reddit).
    if FIT_MODE == "COVER":
        segment_filters = ";".join([f"[{i}:v]{COVER_CROP}[{i}c]" for i in range(num_segments)])
    else:
        seg_parts = []
        for i in range(num_segments):
            seg_parts.append(f"[{i}:v]split[{i}bg][{i}fg];[{i}bg]{BLUR_BANDS_BG}[{i}bg];[{i}fg]{BLUR_BANDS_FG}[{i}fg];[{i}bg][{i}fg]overlay[{i}c]")
        segment_filters = ";".join(seg_parts)
    xfade_parts = []
    offset_accum = first_seg_len - xfade_dur
    xfade_parts.append(f"[0c][1c]xfade=transition=fade:duration={xfade_dur}:offset={offset_accum:.3f}[o1]")
    for i in range(2, num_segments):
        offset_accum += bg_sec - xfade_dur
        prev = "o" + str(i - 1)
        curr = "o" + str(i)
        xfade_parts.append(f"[{prev}][{i}c]xfade=transition=fade:duration={xfade_dur}:offset={offset_accum:.3f}[{curr}]")
    last_label = "o" + str(num_segments - 1)
    xfade_parts.append(f"[{last_label}]trim=start=0:end={audio_sec:.3f},setpts=PTS-STARTPTS[bed]")

    mp4_path = RENDERS_DIR / f"{render_id}.mp4"
    srt_esc = str(srt_use.resolve()).replace("\\", "/").replace(":", "\\:")
    _log(f"[VIDEO] fill_mode={VERTICAL_FILL_MODE}")
    style_yt = "FontName=%s,FontSize=%s,Outline=%s,Shadow=%s,Alignment=2,MarginV=%s,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Bold=1" % (SUBS_FONT, SUBS_FONT_SIZE, SUBS_OUTLINE, SUBS_SHADOW, SUBS_MARGIN_V)
    _log(f"[SUBS] font={SUBS_FONT} fontsize={SUBS_FONT_SIZE} outline={SUBS_OUTLINE} marginV={SUBS_MARGIN_V}")
    subs_part_yt = f"subtitles='{srt_esc}':force_style='{style_yt}'"
    cap_band_yt = _caption_band_filter()
    wm_filt_yt = _watermark_filter()
    if (BRANDING or WATERMARK_ENABLE) and not wm_filt_yt:
        return JSONResponse(content={"error": "WATERMARK NOT APPLIED", "detail": "Watermark enabled but not in filter_complex."}, status_code=500)
    # [bed] is already 1080x1920 from per-segment COVER or BLUR_BANDS
    filter_video = segment_filters + ";" + ";".join(xfade_parts) + ";" + "[bed]" + (cap_band_yt if cap_band_yt else "scale=iw:ih") + "[v1];"
    filter_video += "[v1]" + (wm_filt_yt if wm_filt_yt else "scale=iw:ih") + "[v2];"
    filter_video += "[v2]" + subs_part_yt + "[vout]"
    if DEBUG_RENDER_STAMP:
        parts = filter_video.rsplit("[vout]", 1)
        filter_video = parts[0] + "[vout_s];[vout_s]" + _debug_stamp_filter(render_id) + "[vout]" + (parts[1] if len(parts) > 1 else "")
    if logo_filter_yt:
        filter_video = filter_video.rsplit("[vout]", 1)[0] + "[vout_pre]" + filter_video.rsplit("[vout]", 1)[1]
        filter_video += ";" + logo_filter_yt
    if FIT_MODE == "COVER" and COVER_CROP not in filter_video:
        _log("[RENDER] COVER CROP NOT APPLIED — STOP")
        return JSONResponse(content={"error": "COVER CROP NOT APPLIED — STOP", "detail": "Cover crop missing from filter_complex."}, status_code=500)
    _render_filter_checks(filter_video, "YOUTUBE_B", None)
    filter_audio_yt = f"[{num_segments}:a]atempo={tempo}[a]" if tempo != 1.0 else None
    map_audio_yt = "[a]" if filter_audio_yt else f"{num_segments}:a"
    ok, err = build_and_run_final_ffmpeg_render(
        cmd_inputs, filter_video, mp4_path, final_duration, map_audio=map_audio_yt, filter_audio=filter_audio_yt,
    )
    if not ok:
        return JSONResponse(content={"error": "Render failed", "detail": (err or "FFmpeg failed.")[:500]}, status_code=500)

    return JSONResponse(content={
        "ok": True,
        "render_id": render_id,
        "mp4_url": f"/outputs/renders/{render_id}.mp4",
        "filename": f"{render_id}.mp4",
    })


@app.post("/api/tts")
async def api_tts():
    """Server TTS disabled. Use browser voice or /api/tts_offline (Piper) in Reddit Video Builder."""
    return JSONResponse(
        content={"error": "Server TTS disabled (no API key). Use browser voice or offline Piper.", "detail": "Server TTS disabled (no API key). Use browser voice or offline Piper."},
        status_code=410,
    )


def _unlink_with_retry(path: Path, retries: int = 8, delay: float = 0.25) -> bool:
    """Try to unlink a file; on PermissionError / WinError 32 (in use) or EACCES, retry then give up. Returns True if deleted."""
    for _ in range(retries):
        try:
            path.unlink()
            return True
        except (PermissionError, OSError) as e:
            winerr = getattr(e, "winerror", None)
            errno = getattr(e, "errno", None)
            if winerr == 32 or (errno and errno in (13, 26)):
                time.sleep(delay)
                continue
            raise
    return False


@app.delete("/api/jobs/all")
async def delete_all_jobs():
    """Delete all job folders under outputs/jobs/ and all Reddit renders under outputs/renders/. Never touches server.log.
    Files that are locked (e.g. open in another app) are skipped after retries; their paths are returned in skipped_in_use."""
    _log("[API] DELETE /api/jobs/all hit")
    deleted_jobs = []
    deleted_clips = 0
    deleted_renders = 0
    skipped_in_use = []
    try:
        import shutil
        if JOBS_DIR.is_dir():
            for job_dir in list(JOBS_DIR.iterdir()):
                if not job_dir.is_dir() or job_dir.name in ("", ".", ".."):
                    continue
                clips_dir = job_dir / "clips"
                # Delete files first with retry so locked files don't abort the whole run
                all_files = []
                for p in job_dir.rglob("*"):
                    if p.is_file():
                        all_files.append(p)
                for p in all_files:
                    if _unlink_with_retry(p):
                        if "clips" in p.parts and p.suffix.lower() == ".mp4":
                            deleted_clips += 1
                    else:
                        skipped_in_use.append(str(p))
                # Remove directories (bottom-up: deepest first)
                dirs = sorted([d for d in job_dir.rglob("*") if d.is_dir()], key=lambda d: len(d.parts), reverse=True)
                for d in dirs:
                    try:
                        d.rmdir()
                    except OSError:
                        pass
                try:
                    job_dir.rmdir()
                except OSError:
                    pass
                else:
                    deleted_jobs.append(job_dir.name)
        if RENDERS_DIR.is_dir():
            for f in list(RENDERS_DIR.glob("*.mp4")):
                if f.is_file():
                    if _unlink_with_retry(f):
                        deleted_renders += 1
                    else:
                        skipped_in_use.append(str(f))
    except OSError as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)
    if deleted_renders:
        _log(f"[API] delete_all_jobs deleted_renders={deleted_renders}")
    if skipped_in_use:
        _log(f"[API] delete_all_jobs skipped_in_use={len(skipped_in_use)} paths")
    with _clips_cache_lock:
        _clips_cache["clips"] = []
        _clips_cache["jobs_meta"] = {}
    return {
        "ok": True,
        "deleted_clips": deleted_clips,
        "deleted_jobs": len(deleted_jobs),
        "deleted_renders": deleted_renders,
        "skipped_locked": len(skipped_in_use),
        "skipped_in_use": skipped_in_use,
    }


@app.delete("/api/jobs/{job_id}/clips/{filename:path}")
async def delete_job_clip(job_id: str, filename: str):
    if not job_id or ".." in job_id or "/" in job_id or "\\" in job_id:
        return JSONResponse(content={"error": "Invalid job_id"}, status_code=400)
    if not filename or ".." in filename or "\\" in filename or "/" in filename:
        return JSONResponse(content={"error": "Invalid filename"}, status_code=400)
    clips_dir = (JOBS_DIR / job_id / "clips").resolve()
    try:
        clips_dir.relative_to(JOBS_DIR.resolve())
    except ValueError:
        return JSONResponse(content={"error": "Not found"}, status_code=404)
    mp4_path = (clips_dir / filename).resolve()
    try:
        mp4_path.relative_to(clips_dir)
    except ValueError:
        return JSONResponse(content={"error": "Not found"}, status_code=404)
    if not mp4_path.is_file():
        return JSONResponse(content={"error": "Not found"}, status_code=404)
    stem = mp4_path.stem
    to_delete = [
        mp4_path,
        clips_dir / (stem + ".tiktok.json"),
        clips_dir / (stem + ".subs.json"),
        clips_dir / (stem + ".meta.json"),
        clips_dir / (stem + ".srt"),
    ]
    deleted = []
    skipped_in_use = []
    for p in to_delete:
        if p.is_file():
            if _unlink_with_retry(p):
                deleted.append(str(p.relative_to(clips_dir)))
            else:
                skipped_in_use.append(str(p))
    skipped_locked = len(skipped_in_use)
    return {"ok": True, "deleted": deleted, "skipped_locked": skipped_locked, "skipped_in_use": skipped_in_use}


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str):
    if not job_id or ".." in job_id or "/" in job_id or "\\" in job_id:
        return JSONResponse(content={"error": "Invalid job_id"}, status_code=400)
    job_dir = (JOBS_DIR / job_id).resolve()
    try:
        job_dir.relative_to(JOBS_DIR.resolve())
    except ValueError:
        return JSONResponse(content={"error": "Not found"}, status_code=404)
    if not job_dir.is_dir():
        return JSONResponse(content={"error": "Not found"}, status_code=404)
    skipped_in_use = []
    all_files = [p for p in job_dir.rglob("*") if p.is_file()]
    for p in all_files:
        if not _unlink_with_retry(p):
            skipped_in_use.append(str(p))
    dirs = sorted([d for d in job_dir.rglob("*") if d.is_dir()], key=lambda d: len(d.parts), reverse=True)
    for d in dirs:
        try:
            d.rmdir()
        except OSError:
            pass
    try:
        job_dir.rmdir()
    except OSError:
        pass
    skipped_locked = len(skipped_in_use)
    return {"ok": True, "deleted": [job_id], "skipped_locked": skipped_locked, "skipped_in_use": skipped_in_use}


@app.get("/outputs/{filename:path}")
async def serve_output(filename: str):
    if not filename or ".." in filename or "\\" in filename:
        return JSONResponse(content={"error": "Invalid path"}, status_code=400)
    path = (OUTPUTS_DIR / filename).resolve()
    try:
        path.relative_to(OUTPUTS_DIR.resolve())
    except ValueError:
        return JSONResponse(content={"error": "Not found"}, status_code=404)
    if not path.exists() or not path.is_file():
        return JSONResponse(content={"error": "Not found"}, status_code=404)
    return FileResponse(
        path,
        media_type="video/mp4",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


@app.get("/{full_path:path}", response_class=HTMLResponse)
async def serve_spa(full_path: str):
    """SPA fallback: serve index.html for any frontend route so browser refresh works on /library, /reddit, etc."""
    if full_path.startswith("api/") or full_path.startswith("web/") or full_path.startswith("assets/") or full_path.startswith("outputs/"):
        raise HTTPException(status_code=404, detail="Not found")
    p = WEB_DIR / "index.html"
    if not p.is_file():
        return HTMLResponse("<h1>Not found</h1><p>web/index.html missing</p>", status_code=404)
    html = p.read_text(encoding="utf-8")
    _api_base = (APP_BASE_URL or "http://127.0.0.1:8000").replace("\\", "/")
    html = html.replace("__API_BASE_PLACEHOLDER__", _api_base)
    version = _get_web_asset_version()
    html = html.replace('href="/web/styles.css"', f'href="/web/styles.css?v={version}"')
    html = html.replace('src="/web/app.js"', f'src="/web/app.js?v={version}"')
    return HTMLResponse(html)


def _shutdown_log(reason: str):
    _log(f"[SERVER] pid={os.getpid()} shutting down ({reason})")


def _pid_alive(pid: int) -> bool:
    """Return True if process pid exists (Windows and Unix)."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False
    except ValueError:
        pass
    if sys.platform == "win32":
        try:
            import ctypes
            kernel = ctypes.windll.kernel32  # type: ignore[attr-defined]
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h = kernel.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if h:
                kernel.CloseHandle(h)
                return True
        except Exception:
            pass
    return False


def _acquire_single_instance_lock() -> bool:
    """Create runtime.lock if no other instance is running. Return True if we got the lock."""
    if RUNTIME_LOCK.is_file():
        try:
            data = json.loads(RUNTIME_LOCK.read_text(encoding="utf-8"))
            other_pid = data.get("pid")
            if other_pid is not None and _pid_alive(int(other_pid)):
                return False
        except Exception:
            pass
        try:
            RUNTIME_LOCK.unlink()
        except OSError:
            pass
    try:
        RUNTIME_LOCK.parent.mkdir(parents=True, exist_ok=True)
        RUNTIME_LOCK.write_text(json.dumps({
            "pid": os.getpid(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "port": PORT,
        }, indent=2), encoding="utf-8")
        return True
    except OSError:
        return False


def _release_single_instance_lock():
    try:
        if RUNTIME_LOCK.is_file():
            RUNTIME_LOCK.unlink()
    except OSError:
        pass


EXIT_ALREADY_RUNNING = 2
atexit.register(lambda: _shutdown_log("exit"))
atexit.register(_release_single_instance_lock)

if __name__ == "__main__":
    try:
        if not _acquire_single_instance_lock():
            other_pid = "?"
            try:
                if RUNTIME_LOCK.is_file():
                    d = json.loads(RUNTIME_LOCK.read_text(encoding="utf-8"))
                    other_pid = d.get("pid", "?")
            except Exception:
                pass
            _log("")
            _log("Another Clipper server is already running (pid=%s)." % other_pid)
            _log("")
            sys.exit(EXIT_ALREADY_RUNNING)
        url = f"http://{HOST}:{PORT}"
        _log(f"[SERVER] started url={url} pid={os.getpid()}")
        try:
            RUNTIME_JSON.parent.mkdir(parents=True, exist_ok=True)
            RUNTIME_JSON.write_text(json.dumps({
                "host": HOST,
                "port": PORT,
                "pid": os.getpid(),
                "started_at": datetime.now(timezone.utc).isoformat(),
            }, indent=2), encoding="utf-8")
        except OSError:
            pass
        try:
            import uvicorn
            uvicorn.run(app, host=HOST, port=PORT)
        except OSError as e:
            _release_single_instance_lock()
            _log("")
            _log("Port %s is already in use. Close other Clipper server windows and try again." % PORT)
            _log(str(e))
            _log("")
            _shutdown_log("reason=bind_failed")
            sys.exit(1)
        except Exception as e:
            _log(f"[SERVER] pid={os.getpid()} shutting down (reason: failed to start - {e})")
            _log(traceback.format_exc())
            sys.exit(1)
    except BaseException as e:
        _log(f"[SERVER] pid={os.getpid()} shutting down (reason: startup error - {e})")
        _log(traceback.format_exc())
        sys.exit(1)
