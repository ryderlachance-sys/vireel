"""
YouTube short generator: download, transcribe, select best moments, export 3-5 clips.
Each clip: 45s window, burned captions, 9:16 vertical. Windows-safe (no shell, list args).
"""

import argparse
import hashlib
import json
import math
import os
import random
import re
import site
import struct
import sys
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PROJECT_ROOT
DOWNLOADS_DIR = REPO_ROOT / "downloads"
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
WORK = str(PROJECT_ROOT)
VIDEO_MP4 = os.path.join(WORK, "video.mp4")  # legacy default; pipeline uses source_path (downloads/<video_id>.mp4) per job

# Baked subs: cv2/numpy availability (checked once)
_SUBS_CV2_NUMPY_AVAILABLE = None
_SUBS_CV2_WARNED = False


def _subs_cv2_numpy_available():
    """Check once whether cv2 and numpy are importable. Used for baked caption detection."""
    global _SUBS_CV2_NUMPY_AVAILABLE
    if _SUBS_CV2_NUMPY_AVAILABLE is not None:
        return _SUBS_CV2_NUMPY_AVAILABLE
    try:
        import cv2  # noqa: F401
        import numpy as np  # noqa: F401
        _SUBS_CV2_NUMPY_AVAILABLE = True
    except ImportError:
        _SUBS_CV2_NUMPY_AVAILABLE = False
    return _SUBS_CV2_NUMPY_AVAILABLE


def _warn_cv2_numpy_once():
    """Print install hint once if cv2/numpy are missing."""
    global _SUBS_CV2_WARNED
    if _SUBS_CV2_WARNED or _subs_cv2_numpy_available():
        return
    _SUBS_CV2_WARNED = True
    py = getattr(sys, "executable", "python")
    print(
        f"  [SUBS] WARN: cv2/numpy missing; baked detection disabled; defaulting to burn.",
        flush=True,
    )
    print(
        f"  [SUBS] Install in this Python:  {py!s} -m pip install numpy opencv-python-headless",
        flush=True,
    )


# Config
CLIP_WINDOW_SECONDS = 45
WINDOW_SECONDS = 45  # alias for scoring
MIN_CLIPS = 3
MAX_CLIPS = 6
# Speed vs quality: "tiny" or "base" = faster transcribe (less accurate); "small" = default
WHISPER_MODEL = os.environ.get("CLIP_WHISPER_MODEL", "small")
# NVENC preset: p1 = fastest encode, p7 = slowest/best quality. p3 = good speed/quality tradeoff
NVENC_PRESET = os.environ.get("CLIP_NVENC_PRESET", "p3")
SUBS_MODE = "auto"  # auto|on|off  auto=burn unless HIGH confidence baked
SUBS_DEFAULT_POS = "bottom"  # bottom|top, used when burning
SUBS_DEBUG = False  # True: print baked_captions_likely metrics per frame and summary
# Baked detection: only skip burn when HIGH confidence (avoid false positives)
BAKED_CONFIDENCE_THRESHOLD = 0.8
BAKED_EDGE_BOTTOM_MIN = 0.045  # edge_density in bottom ROI must exceed this
BAKED_FRAMES_VOTES_MIN = 4  # at least N of 6 frames must vote baked
# Initial decision: skip burn only when confidence >= this (verification + fallback catch false positives)
INITIAL_SKIP_CONFIDENCE = 0.90
VERIFY_FRAMES = 5  # sample N frames for post-render caption verification
VERIFY_VOTES_MIN = 2  # at least N frames must vote caption_present
CLIP_DEBUG = False  # True: print WIN_SCORE lines for top candidate windows
AUDIO_NORMALIZE = True
TARGET_I = -14
MIN_SPEECH_COVERAGE = 0.6
DIVERSITY_JACCARD_THRESHOLD = 0.55
MIN_OUTPUT_SIZE_MB = 1
CACHE_VIDEO_MIN_MB = 10
# Download safeguards: hard timeout and no-output stall (kill yt-dlp only if truly stuck)
# Use generous values so slow-but-working downloads (e.g. long video, slow connection) are not killed.
DOWNLOAD_TIMEOUT_SEC = 600
DOWNLOAD_STALL_NO_OUTPUT_SEC = 120
MAX_OVERLAP_RATIO = 0.3  # reject candidate if overlap with selected > 30% of candidate length
HOOK_KEYWORDS = [
    "wait", "what", "bro", "dude", "no way", "crazy", "insane",
    "look", "watch", "hold on", "nah", "oh my god",
]
FILLER_PHRASES = [
    "you know", "like", "i mean", "sort of", "kind of", "uh", "um",
    "anyway", "so yeah", "right", "okay so",
]
SPONSOR_INDICATORS = [
    "sponsored", "sponsor", "thanks to", "link in the description",
    "use code", "promo code", "brought to you by",
]
SILENCE_GAP_THRESHOLD = 1.2   # seconds; single gap above this penalized
SILENCE_GAP_PENALTY_PER_SEC = 1.5  # (gap - threshold) * this
SILENCE_TOTAL_PENALTY_PER_SEC = 0.6  # total_silence * this
HOOK_IN_FIRST_3_BONUS = 2.0
EVENTS_PER_10S_BONUS = 0.8
FILLER_PENALTY_PER = 0.4
FILLER_CAP = 8
INTRO_OUTRO_PENALTY = 3.0
SPONSOR_PENALTY = 4.0
INTRO_CUTOFF_SEC = 45
OUTRO_CUTOFF_SEC = 60

# Subtitle timing: stay close to Whisper so subs are on-point (no long "stuck" holds)
SUBS_MERGE_GAP_SEC = 0.15   # only merge when segments are almost back-to-back
SUBS_MIN_DURATION_SEC = 0.25  # avoid flicker only; don't hold short phrases long
SUBS_FILL_GAP_SEC = 0.0     # disabled: don't extend segment into silence
SUBS_MAX_DURATION_SEC = 3.5  # cap so one line never stays on screen too long
SUBS_MAX_WORDS_BEFORE_CHUNK = 10
SUBS_CHUNK_WORDS_MIN, SUBS_CHUNK_WORDS_MAX = 5, 7

# Burned caption style (ASS): normal TikTok look — readable, not stretched
# CAPTION_MARGIN_V: pixels from bottom (larger = higher on screen). 420 = TikTok safe zone above bottom UI.
CAPTION_MARGIN_V = 420
CAPTION_FONT_SIZE = 68
CAPTION_OUTLINE = 4
CAPTION_SHADOW = 2
# No box: BorderStyle=1 outline+shadow only; BackColour transparent so no background box
CAPTION_BACK_COLOUR = "&H00000000"
CAPTION_BORDER_STYLE = 1
# Karaoke: highlight (filled) = yellow, unfilled = white. ASS &HAABBGGRR.
CAPTION_KARAOKE_PRIMARY = "&H0000FFFF"   # yellow highlight
CAPTION_KARAOKE_SECONDARY = "&H00FFFFFF"  # white before fill
# Negative = captions appear slightly before speech so they feel on point.
SUBTITLE_OFFSET_SEC = -0.35

# Caption emphasis (reaction clips): inline ASS overrides per line. Base = CAPTION_FONT_SIZE/CAPTION_OUTLINE.
CAPTION_EMPH_MEDIUM_FS = 72
CAPTION_EMPH_MEDIUM_BORD = 5
CAPTION_EMPH_HIGH_FS = 78
CAPTION_EMPH_HIGH_BORD = 6
AUDIO_SPIKE_HIGH_THRESHOLD = 2.0
CAPTION_AUDIO_MIN_OVERLAP_SEC = 0.35
CAPTION_AUDIO_MIN_OVERLAP_RATIO = 0.25

# Adaptive TikTok caption styles and templates
CAPTION_STYLE_HOOK_ONLY = "hook_only"
CAPTION_STYLE_HOOK_QUESTION = "hook_question"
CAPTION_STYLE_HOOK_CTA = "hook_cta"
CAPTION_STYLE_REDDIT_LABEL = "reddit_label"
CAPTION_STYLE_STORY_LABEL = "story_label"

REDDIT_MARKERS = [
    "aita", "tifu", "r/", "subreddit", "op", "upvotes", "downvotes",
    "am i the asshole", "throwaway", "edit:",
]

CAPTION_TEMPLATES = {
    CAPTION_STYLE_HOOK_ONLY: [
        "Wait for the ending 💀",
        "This gets crazy…",
        "Nah this is wild 😭",
        "You won't believe this",
    ],
    CAPTION_STYLE_HOOK_QUESTION: [
        "Would you have done this?",
        "What would you do here?",
        "Is this actually normal?",
    ],
    CAPTION_STYLE_HOOK_CTA: [
        "Part 2 if this hits 10k",
        "Like for part 2",
        "This didn't end how I expected…",
    ],
    CAPTION_STYLE_REDDIT_LABEL: [
        "Reddit storytime",
        "Another Reddit story",
        "This Reddit story is insane",
    ],
    CAPTION_STYLE_STORY_LABEL: [
        "Storytime",
        "Quick story",
        "This story is insane",
        "Wait for this story…",
    ],
}
DEFAULT_HASHTAGS = "#viral #shorts #fyp #trending #clips #youtube"
QUESTION_WORDS_LAST3 = ["why", "would", "should", "what", "how"]


def is_reddit_story(window_text):
    """Return True if window text contains Reddit-specific markers (case-insensitive)."""
    if not window_text or not isinstance(window_text, str):
        return False
    t = window_text.lower()
    return any(m in t for m in REDDIT_MARKERS)


def classify_caption_style(duration_sec, word_count, ends_with_strong_punctuation, bad_end_score, has_question_words, looks_like_story, window_text=None):
    """Choose caption style. Use reddit_label only when is_reddit_story; story_label for long generic stories (no Reddit)."""
    if is_reddit_story(window_text or ""):
        return CAPTION_STYLE_REDDIT_LABEL
    if looks_like_story:
        return CAPTION_STYLE_STORY_LABEL
    if has_question_words:
        return CAPTION_STYLE_HOOK_QUESTION
    if bad_end_score < 0.3 and duration_sec > 20:
        return CAPTION_STYLE_HOOK_CTA
    return CAPTION_STYLE_HOOK_ONLY


def get_caption_for_style(style):
    """Return a random caption from the style's templates. Never empty."""
    templates = CAPTION_TEMPLATES.get(style, CAPTION_TEMPLATES[CAPTION_STYLE_HOOK_ONLY])
    return (random.choice(templates) if templates else "You won't believe this").strip()


def title_from_window_text(window_text: str, max_len: int = 70) -> str:
    """
    Derive a short, readable title from the clip's transcript (window text).
    Uses first sentence, strips filler words, truncates. Never returns filename-like text.
    """
    if not (window_text or "").strip():
        return "Short"
    text = re.sub(r"\s+", " ", window_text.strip())
    # First sentence or first chunk up to first strong punctuation
    parts = re.split(r"[.!?]\s+", text, maxsplit=1)
    frag = (parts[0] + ".").strip() if parts else text
    frag_lower = frag.lower()
    for filler in FILLER_PHRASES:
        frag_lower = re.sub(re.escape(filler), " ", frag_lower)
    frag = re.sub(r"\s+", " ", frag_lower).strip()
    if not frag or len(frag) < 4:
        frag = text.strip()
    frag = re.sub(r"\s+", " ", frag).strip()
    if len(frag) > max_len:
        frag = frag[: max_len - 1].rsplit(" ", 1)[0] + "…"
    result = frag[:1].upper() + frag[1:] if frag else "Short"
    # Avoid returning something that looks like a filename (e.g. run_20260210_short_4)
    if re.match(r"^run_\d+_short_\d+$", result.replace(" ", "_").lower()):
        return "Short"
    return result[:max_len]


def write_clip_tiktok_json(out_path, caption, caption_style, hashtags, upload_filename, job_id=None, source_video_id=None, source_url=None, suggested_title=None):
    """Write <clip>.tiktok.json with caption, style, hashtags, upload_filename, and optional job/source meta and suggested_title from transcript."""
    try:
        data = {
            "caption": caption,
            "caption_style": caption_style,
            "hashtags": hashtags,
            "upload_filename": upload_filename,
        }
        if suggested_title is not None:
            data["suggested_title"] = suggested_title[:100].strip()
        if job_id is not None:
            data["job_id"] = job_id
        if source_video_id is not None:
            data["source_video_id"] = source_video_id
        if source_url is not None:
            data["source_url"] = source_url
        out_path = Path(out_path)
        out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


def _post_run_open_outputs(output_dir):
    """Print output path banner, list newest mp4s, open folder and newest 3 mp4s. No files written."""
    try:
        out = Path(output_dir).resolve()
        print("\n" + "=" * 60)
        print("OUTPUT VIDEOS SAVED HERE:")
        print(str(out))
        print("=" * 60 + "\n")
        mp4s = sorted(Path(output_dir).rglob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
        if mp4s:
            print("NEWEST MP4s:")
            for p in mp4s[:5]:
                print(f"  {p}")
            for p in mp4s[:3]:
                if p.exists():
                    try:
                        os.startfile(str(p))
                        print(f"  OPENING: {p}")
                    except Exception:
                        pass
        try:
            os.startfile(str(out))
        except Exception:
            pass
    except Exception as e:
        print(f"(Post-run: {e})")


def add_cuda_bin_to_path():
    """On Windows, prepend CUDA bin and pip-installed NVIDIA DLL folders to PATH for WhisperModel."""
    if sys.platform != "win32":
        return
    # 1) CUDA_PATH or Program Files CUDA toolkit
    cuda_path = os.environ.get("CUDA_PATH")
    if cuda_path:
        cuda_bin = os.path.join(cuda_path, "bin")
        if os.path.isdir(cuda_bin):
            os.environ["PATH"] = cuda_bin + os.pathsep + os.environ.get("PATH", "")
            print(f"  CUDA runtime added to PATH: {cuda_bin}")
    else:
        base = os.path.join(
            os.environ.get("ProgramFiles", "C:\\Program Files"),
            "NVIDIA GPU Computing Toolkit",
            "CUDA",
        )
        for ver in ("v12.3", "v12.2", "v12.1", "v12.0"):
            cuda_bin = os.path.join(base, ver, "bin")
            if os.path.isdir(cuda_bin):
                os.environ["PATH"] = cuda_bin + os.pathsep + os.environ.get("PATH", "")
                print(f"  CUDA runtime added to PATH: {cuda_bin}")
                break
    # 2) Pip-installed NVIDIA wheels (cublas, cudnn, cuda_runtime) so GPU works without full toolkit
    site_dirs = list(site.getsitepackages())
    try:
        user_site = site.getusersitepackages()
        if user_site and user_site not in site_dirs:
            site_dirs.append(user_site)
    except Exception:
        pass
    for nvidia_sub in ("nvidia/cublas/bin", "nvidia/cudnn/bin", "nvidia/cuda_runtime/bin"):
        for sp in site_dirs:
            dll_path = os.path.join(sp, nvidia_sub)
            if os.path.isdir(dll_path):
                os.environ["PATH"] = dll_path + os.pathsep + os.environ.get("PATH", "")
                print(f"  Added NVIDIA DLL path: {dll_path}")


def nvenc_available():
    """Return True if ffmpeg has h264_nvenc (NVIDIA GPU encoding)."""
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            cwd=WORK,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        return r.returncode == 0 and "h264_nvenc" in (r.stderr or "") + (r.stdout or "")
    except Exception:
        return False


def run(cmd, step_name, timeout_sec=None):
    """Run a command (list of args); exit with message on failure. timeout_sec: abort if command runs longer (e.g. 360 for download)."""
    print(f"  Running: {' '.join(cmd)}")
    if cmd and cmd[0] == "ffmpeg":
        print(f"  [FFMPEG] cmd={' '.join(cmd)}", flush=True)
    try:
        result = subprocess.run(
            cmd,
            cwd=WORK,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired:
        print(f"\n[ERROR] Step failed: {step_name} (timed out after {timeout_sec}s)", file=sys.stderr)
        print("  Try again or a different video. If YouTube often fails, update yt-dlp: pip install -U yt-dlp", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print(f"\n[ERROR] Step failed: {step_name}", file=sys.stderr)
        print(f"  Command not found. Check PATH: {cmd[0]}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] Step failed: {step_name}", file=sys.stderr)
        print(f"  {e}", file=sys.stderr)
        sys.exit(1)

    if result.returncode != 0:
        print(f"\n[ERROR] Step failed: {step_name} (exit code {result.returncode})", file=sys.stderr)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        if result.stdout:
            print(result.stdout, file=sys.stderr)
        sys.exit(1)


def _get_cookies_path():
    """Return path to cookies file if it exists; prefer YT_DLP_COOKIES env, else REPO_ROOT/cookies.txt."""
    env_path = (os.environ.get("YT_DLP_COOKIES") or "").strip()
    if env_path:
        p = Path(env_path)
        if p.is_file():
            return p
    p = REPO_ROOT / "cookies.txt"
    return p if p.is_file() else None


def _is_ytdlp_signin_challenge(text):
    """True if yt-dlp output indicates YouTube bot/sign-in challenge."""
    if not text:
        return False
    lower = text.lower()
    # Exact and partial phrases (yt-dlp / YouTube wording)
    if "sign in to confirm" in lower and "bot" in lower:
        return True
    if "confirm you're not a bot" in lower or "confirm you are not a bot" in lower:
        return True
    if "use --cookies-from-browser or --cookies" in lower or "use --cookies" in lower:
        return True
    if "--cookies" in lower and ("bot" in lower or "sign in" in lower):
        return True
    # [youtube] ... Sign in to confirm ...
    if "[youtube]" in lower and "sign in" in lower and "confirm" in lower:
        return True
    return False


def _run_ytdlp_download(url, output_template, timeout_sec, stall_sec, progress_cb=None, cookies_path=None):
    """Run yt-dlp with streaming output, hard timeout, and no-output stall detection.
    Returns (True, output) on success; (False, output) on non-zero exit. Raises RuntimeError on timeout/stall.
    When cookies_path is set, adds --cookies before url.
    """
    start_ts = datetime.now().isoformat()
    print(f"[DL_DEBUG] url={url!r} output_template={output_template!r} timeout_sec={timeout_sec} stall_sec={stall_sec} start={start_ts}", flush=True)
    cmd = [
        "yt-dlp",
        "--merge-output-format", "mp4",
        "--write-info-json",
        "--no-overwrites",
        "-o", output_template,
        "--socket-timeout", "30",
        "--retries", "3",
    ]
    if cookies_path:
        cmd.extend(["--cookies", str(cookies_path)])
    cmd.append(url)
    proc = subprocess.Popen(
        cmd,
        cwd=WORK,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    last_output_time = [time.time()]
    collected = []

    def read_stream():
        try:
            for line in iter(proc.stdout.readline, ""):
                collected.append(line)
                print(line, end="", flush=True)
                last_output_time[0] = time.time()
                if progress_cb and line.strip():
                    progress_cb(line.strip())
        except Exception:
            pass
        finally:
            try:
                proc.stdout.close()
            except Exception:
                pass

    reader = threading.Thread(target=read_stream, daemon=True)
    reader.start()
    start_time = time.time()
    try:
        while proc.poll() is None:
            time.sleep(1)
            now = time.time()
            if now - start_time > timeout_sec:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                raise RuntimeError(
                    f"Download timed out after {timeout_sec} seconds. Try again or use cookies."
                )
            if now - last_output_time[0] > stall_sec:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                raise RuntimeError(
                    f"Download stalled (no output for {stall_sec} seconds). Try again or use cookies."
                )
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
    output = "".join(collected)
    if proc.returncode != 0:
        end_ts = datetime.now().isoformat()
        print(f"[DL_DEBUG] end={end_ts} failure=exit_code_{proc.returncode}", flush=True)
        return (False, output)
    end_ts = datetime.now().isoformat()
    print(f"[DL_DEBUG] end={end_ts} success", flush=True)
    return (True, output)


class _Seg:
    __slots__ = ("start", "end", "text", "words")
    def __init__(self, start, end, text, words=None): self.start, self.end, self.text, self.words = start, end, text, words


def get_video_duration(path):
    """Return duration in seconds via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", path,
    ]
    result = subprocess.run(cmd, cwd=WORK, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        print(f"\n[ERROR] Could not get video duration: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    return float(result.stdout.strip())


def get_video_resolution(path):
    """Return (width, height) via ffprobe. Exits on failure."""
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-of", "csv=p=0", path,
    ]
    result = subprocess.run(cmd, cwd=WORK, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        print(f"\n[ERROR] Could not get video resolution: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    parts = result.stdout.strip().split(",")
    if len(parts) != 2:
        print(f"\n[ERROR] Unexpected ffprobe output for resolution: {result.stdout!r}", file=sys.stderr)
        sys.exit(1)
    return int(parts[0]), int(parts[1])


def srt_time(secs):
    """Format seconds as SRT timestamp (HH:MM:SS,mmm)."""
    h = int(secs // 3600)
    m = int((secs % 3600) // 60)
    s = secs % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")


def _ass_time(secs):
    """Format seconds as ASS timestamp (H:MM:SS.cc centiseconds)."""
    h = int(secs // 3600)
    m = int((secs % 3600) // 60)
    s = secs % 60
    cs = int(round(s * 100)) % 100
    sec_int = int(s)
    return f"{h}:{m:02d}:{sec_int:02d}.{cs:02d}"


def _caption_line_emphasis(line_text, audio_bonus=None):
    """
    Classify caption line emphasis from text (and optional audio_spike) for styling.
    Returns (level, reasons_list): level in ("low", "medium", "high").
    Uses REACTION_PHRASES, PROFANITY_PATTERN, _segment_has_repeated_words. Deterministic.
    """
    text = (line_text or "").strip()
    t_lower = text.lower()
    reasons = []
    has_reaction = any(p in t_lower for p in REACTION_PHRASES)
    exc = text.count("!")
    qm = text.count("?")
    has_excl = exc > 0
    has_quest = qm > 0
    has_profanity = bool(PROFANITY_PATTERN.search(text))
    has_repeated = _segment_has_repeated_words(text)
    if has_reaction:
        reasons.append("reaction_phrase")
    if has_excl:
        reasons.append("excl")
    if has_quest:
        reasons.append("quest")
    if has_profanity:
        reasons.append("profanity")
    if has_repeated:
        reasons.append("repeated")
    if audio_bonus is not None and audio_bonus >= AUDIO_SPIKE_HIGH_THRESHOLD:
        reasons.append(f"audio_spike:{audio_bonus:.1f}")

    # high: reaction + exclamation, OR profanity + exclamation, OR audio_spike >= 2.0
    if (has_reaction and has_excl) or (has_profanity and has_excl):
        return ("high", reasons)
    if audio_bonus is not None and audio_bonus >= AUDIO_SPIKE_HIGH_THRESHOLD:
        return ("high", reasons)
    # medium: any reaction phrase, OR exclamation/question, OR repeated words
    if has_reaction or has_excl or has_quest or has_repeated:
        return ("medium", reasons)
    return ("low", reasons)


def write_ass(path, segments_rel, margin_v=None, use_karaoke=False, segment_audio_bonuses=None):
    """Write ASS file with fixed style: bottom-center, readable, for 1080x1920.
    margin_v: pixels from bottom. use_karaoke: if True, write word-level \\kf tags; Primary=yellow highlight, Secondary=white.
    segment_audio_bonuses: optional list of (t_start, t_end, bonus) in clip-relative sec for emphasis; if missing, text-only."""
    if margin_v is None:
        margin_v = CAPTION_MARGIN_V
    margin_v = max(CAPTION_MARGIN_V, min(800, int(margin_v)))
    offset = float(SUBTITLE_OFFSET_SEC)
    script_info = (
        "[Script Info]\r\n"
        "ScriptType: v4.00+\r\n"
        "PlayResX: 1080\r\n"
        "PlayResY: 1920\r\n"
        "WrapStyle: 2\r\n"
        "\r\n"
    )
    # Karaoke: PrimaryColour = highlight (yellow), SecondaryColour = unfilled (white). Bold=1.
    if use_karaoke:
        primary_c = CAPTION_KARAOKE_PRIMARY
        secondary_c = CAPTION_KARAOKE_SECONDARY
    else:
        primary_c = "&H00FFFFFF"
        secondary_c = "&H000000FF"
    bold_val = "1" if use_karaoke else "0"
    styles = (
        "[V4+ Styles]\r\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\r\n"
        f"Style: Default,Arial,{CAPTION_FONT_SIZE},{primary_c},{secondary_c},&H00000000,{CAPTION_BACK_COLOUR},{bold_val},0,0,0,100,100,0,0,{CAPTION_BORDER_STYLE},{CAPTION_OUTLINE},{CAPTION_SHADOW},2,80,80,{margin_v},1\r\n"
        "\r\n"
    )
    events = "[Events]\r\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\r\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(script_info)
        f.write(styles)
        f.write(events)
        for seg in segments_rel:
            _s, _e, text = seg[0], seg[1], seg[2]
            words_data = seg[3] if len(seg) >= 4 else None
            start_str = _ass_time(max(0.0, _s + offset))
            end_str = _ass_time(max(0.0, _e + offset))
            raw = (text or "").replace("\n", " ").replace("\r", "").strip()
            # Emphasis: optional audio bonus from overlapping hint (clip-relative time)
            line_start_sec = _s
            line_end_sec = _e
            line_duration_sec = _e - _s
            audio_bonus = None
            overlaps_debug = []
            if segment_audio_bonuses:
                for tb, te, b in segment_audio_bonuses:
                    if te > _s and tb < _e:
                        overlap_sec = min(_e, te) - max(_s, tb)
                        overlap_ratio = (overlap_sec / line_duration_sec) if line_duration_sec > 0 else 0.0
                        eligible = overlap_sec >= CAPTION_AUDIO_MIN_OVERLAP_SEC or overlap_ratio >= CAPTION_AUDIO_MIN_OVERLAP_RATIO
                        overlaps_debug.append({"seg_start": tb, "seg_end": te, "bonus": b, "overlap_sec": round(overlap_sec, 3), "eligible": eligible})
                        if eligible:
                            audio_bonus = b if audio_bonus is None else max(audio_bonus, b)
            chosen_audio_bonus = audio_bonus
            eligible_overlap_count = sum(1 for o in overlaps_debug if o["eligible"])
            level, emph_reasons = _caption_line_emphasis(raw, audio_bonus)
            reasons_str = ",".join(emph_reasons) if emph_reasons else ""
            snippet = (raw[:50] + "...") if len(raw) > 50 else raw
            print(f"[CAPTION_EMPH] text={snippet!r} level={level} reasons={reasons_str!s}", flush=True)
            print("[CAPTION_AUDIO_OVERLAP]", {"text": (raw[:120] + "...") if len(raw) > 120 else raw, "line_start": round(line_start_sec, 3), "line_end": round(line_end_sec, 3), "line_duration": round(line_duration_sec, 3), "overlaps": overlaps_debug[:10], "chosen_audio_bonus": chosen_audio_bonus, "audio_triggered_high": bool(chosen_audio_bonus is not None and chosen_audio_bonus >= AUDIO_SPIKE_HIGH_THRESHOLD), "eligible_overlap_count": eligible_overlap_count, "audio_bonus_applied": chosen_audio_bonus is not None}, flush=True)
            if use_karaoke and raw:
                start_cs = int(round((_s + offset) * 100))
                end_cs = int(round((_e + offset) * 100))
                line_dur_cs = max(0, end_cs - start_cs)
                if words_data and len(words_data) > 0:
                    # Word-level karaoke from timestamps
                    durs = []
                    prev_end_cs = start_cs
                    for w in words_data:
                        w_start_cs = int(round((w["start"] + offset) * 100))
                        w_end_cs = int(round((w["end"] + offset) * 100))
                        if abs(w_start_cs - prev_end_cs) <= 6:
                            w_start_cs = prev_end_cs
                        dur_cs = max(4, min(250, w_end_cs - w_start_cs))
                        durs.append((w["word"], dur_cs))
                        prev_end_cs = w_start_cs + dur_cs
                    total_cs = sum(d for _, d in durs)
                    if total_cs != line_dur_cs and durs:
                        last_word, last_dur = durs[-1]
                        last_dur = max(4, min(250, last_dur + (line_dur_cs - total_cs)))
                        durs[-1] = (last_word, last_dur)
                    line = " ".join("{\\kf%d}%s" % (d, wt) for wt, d in durs)
                else:
                    # Equal-split fallback
                    words = raw.split()
                    if words:
                        n = len(words)
                        base = line_dur_cs // n
                        rem = line_dur_cs - base * n
                        durs = [max(4, min(250, base + 1))] * rem + [max(4, min(250, base))] * (n - rem)
                        total_cs = sum(durs)
                        if total_cs != line_dur_cs and durs:
                            durs[-1] = max(4, min(250, durs[-1] + (line_dur_cs - total_cs)))
                        line = " ".join("{\\kf%d}%s" % (durs[i], w) for i, w in enumerate(words))
                    else:
                        line = raw
            else:
                line = raw.replace("\n", "\\N")
            # Apply emphasis styling via ASS override tags (no new animations)
            if level == "medium":
                line = "{\\fs%d\\bord%d}" % (CAPTION_EMPH_MEDIUM_FS, CAPTION_EMPH_MEDIUM_BORD) + line
            elif level == "high":
                line = "{\\fs%d\\bord%d\\b1}" % (CAPTION_EMPH_HIGH_FS, CAPTION_EMPH_HIGH_BORD) + line
            f.write(f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{line}\r\n")


def spoken_ratio(segments_list, start, end):
    """Fraction of window duration that is spoken (from segment coverage)."""
    if end <= start:
        return 0.0
    total = 0.0
    for seg in segments_list:
        if seg.end <= start or seg.start >= end:
            continue
        total += min(seg.end, end) - max(seg.start, start)
    return total / (end - start)


def _jaccard_tokens(text_a, text_b):
    """Jaccard similarity over lowercased word tokens; ignore very short words."""
    stop = {"a", "an", "the", "is", "it", "to", "of", "and", "in", "on", "for", "at"}
    def tokens(t):
        return {w for w in re.findall(r"\w+", t.lower()) if len(w) > 1 and w not in stop}
    a, b = tokens(text_a), tokens(text_b)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# Face-safe subtitle placement (vertical output 1080x1920)
OUT_H = 1920
OUT_W = 1080
# Vertical fill for Library clips (YouTube job): "cover" = 9:16 crop only; "blur" = legacy blur letterbox
CLIP_VERTICAL_FILL_MODE = (os.environ.get("CLIP_VERTICAL_FILL_MODE") or "cover").strip().lower()
if CLIP_VERTICAL_FILL_MODE not in ("cover", "blur"):
    CLIP_VERTICAL_FILL_MODE = "cover"
# Generate job render. Allowed: COVER, BLUR_TB. BLUR and BLUR_BANDS are aliases for BLUR_TB.
_ALLOWED_FIT_MODES = ("COVER", "BLUR_TB")
_raw_fit = (os.environ.get("FIT_MODE") or os.environ.get("CLIP_VERTICAL_FILL_MODE") or "BLUR_TB").strip().upper()
if _raw_fit in ("BLUR", "BLUR_BANDS"):
    _raw_fit = "BLUR_TB"
if _raw_fit not in _ALLOWED_FIT_MODES:
    print(f"[ERROR] FIT_MODE must be one of {_ALLOWED_FIT_MODES}; got {_raw_fit!r}", file=sys.stderr)
    sys.exit(1)
FIT_MODE = _raw_fit
_render_fit_mode = FIT_MODE
DEBUG_RENDER_STAMP = (os.environ.get("DEBUG_RENDER_STAMP") or "").strip().lower() in ("1", "true", "yes")
BAND_H = int(OUT_H * 0.22)
DEFAULT_SUB_Y = OUT_H - BAND_H - int(OUT_H * 0.04)
PLACEMENT_PADDING = int(OUT_H * 0.03)
PLACEMENT_MIN_Y = int(OUT_H * 0.10)
PLACEMENT_SAMPLE_FRAMES = 5


def compute_safe_subtitle_y(clip_path):
    """
    Estimate safe subtitle band Y for 1080x1920 vertical output. Samples frames from clip (horizontal),
    runs face detection, maps face positions to vertical coords, returns placement dict.
    """
    out = {"mode": "fallback", "y": DEFAULT_SUB_Y, "band_h": BAND_H, "faces": 0, "max_face_bottom_y": 0}
    try:
        import cv2
        import numpy as np
    except ImportError:
        return out
    cascade_path = getattr(cv2.data, "haarcascades", None)
    if cascade_path:
        cascade_path = os.path.join(cascade_path, "haarcascade_frontalface_default.xml")
    if not cascade_path or not os.path.isfile(cascade_path):
        return out
    try:
        classifier = cv2.CascadeClassifier(cascade_path)
        if classifier.empty():
            return out
    except Exception:
        return out
    clip_path = str(clip_path)
    try:
        duration = get_video_duration(clip_path)
        if duration <= 0:
            return out
        times = [duration * (i + 1) / (PLACEMENT_SAMPLE_FRAMES + 1) for i in range(PLACEMENT_SAMPLE_FRAMES)]
        max_face_bottom_out = 0
        faces_total = 0
        frame_h, frame_w = None, None
        for t in times:
            proc = subprocess.run(
                [
                    "ffmpeg", "-y", "-ss", str(t), "-i", clip_path,
                    "-vframes", "1", "-f", "image2pipe", "-c:v", "mjpeg", "pipe:1",
                ],
                cwd=WORK,
                capture_output=True,
                timeout=10,
            )
            if proc.returncode != 0 or not proc.stdout:
                continue
            buf = np.frombuffer(proc.stdout, dtype=np.uint8)
            img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
            if img is None:
                continue
            h, w = img.shape[:2]
            if frame_h is None:
                frame_h, frame_w = h, w
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            faces = classifier.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30))
            scale = min(OUT_W / w, OUT_H / h)
            content_w = w * scale
            content_h = h * scale
            content_left = (OUT_W - content_w) / 2
            content_top = (OUT_H - content_h) / 2
            for (fx, fy, fw, fh) in faces:
                faces_total += 1
                face_bottom_src = fy + fh
                face_bottom_out = int(content_top + face_bottom_src * scale)
                max_face_bottom_out = max(max_face_bottom_out, face_bottom_out)
        if frame_h is None or frame_w is None:
            return out
        out["faces"] = faces_total
        out["max_face_bottom_y"] = max_face_bottom_out
        if max_face_bottom_out <= DEFAULT_SUB_Y:
            out["mode"] = "bottom"
            out["y"] = DEFAULT_SUB_Y
            return out
        shifted_y = max(PLACEMENT_MIN_Y, max_face_bottom_out + PLACEMENT_PADDING - BAND_H)
        shifted_y = min(shifted_y, DEFAULT_SUB_Y)
        out["mode"] = "shifted"
        out["y"] = int(shifted_y)
        return out
    except Exception:
        return out


def _textlike_bottom(roi_gray):
    """Text-like morphology count in bottom ROI (0..1). Uses Otsu, morph open/close, connectedComponents."""
    try:
        import cv2
        import numpy as np
        blur = cv2.GaussianBlur(roi_gray, (3, 3), 0)
        _, thresh = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        kernel = np.ones((3, 3), np.uint8)
        morphed = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)
        morphed = cv2.morphologyEx(morphed, cv2.MORPH_CLOSE, kernel)
        num_labels, _, stats, _ = cv2.connectedComponentsWithStats(morphed, connectivity=8)
        count = 0
        for idx in range(1, num_labels):
            area = stats[idx, cv2.CC_STAT_AREA]
            w = stats[idx, cv2.CC_STAT_WIDTH]
            h = stats[idx, cv2.CC_STAT_HEIGHT]
            if 10 <= area <= 400 and w >= 1 and h >= 1 and (w / max(h, 1e-6) >= 1.2 or h / max(w, 1e-6) >= 1.2):
                count += 1
        return max(0.0, min(1.0, count / 80.0))
    except Exception:
        return 0.0


def detect_baked_captions(clip_path):
    """
    Multi-signal baked caption detection (v3). No disk writes; uses ffmpeg image2pipe.
    Returns dict: status, score, frames_total, frames_baked_votes, avg_bottom, avg_mid, avg_delta, avg_textlike, avg_whiteness.
    """
    if not _subs_cv2_numpy_available():
        _warn_cv2_numpy_once()
        return {"status": "uncertain", "score": 0.0, "frames_total": 0, "frames_baked_votes": 0, "avg_bottom": 0.0, "avg_mid": 0.0, "avg_delta": 0.0, "avg_textlike": 0.0, "avg_whiteness": 0.0}
    try:
        import cv2
        import numpy as np
    except ImportError:
        _warn_cv2_numpy_once()
        return {"status": "uncertain", "score": 0.0, "frames_total": 0, "frames_baked_votes": 0, "avg_bottom": 0.0, "avg_mid": 0.0, "avg_delta": 0.0, "avg_textlike": 0.0, "avg_whiteness": 0.0}
    clip_path = str(clip_path)
    try:
        duration = get_video_duration(clip_path)
        if duration <= 0:
            return {"status": "uncertain", "score": 0.0, "frames_total": 0, "frames_baked_votes": 0, "avg_bottom": 0.0, "avg_mid": 0.0, "avg_delta": 0.0, "avg_textlike": 0.0, "avg_whiteness": 0.0}
        times = [duration * (i + 1) / 7 for i in range(6)]
        frames_baked_votes = 0
        bottoms, mids, deltas = [], [], []
        textlikes, whiteness_raws = [], []
        for t in times:
            proc = subprocess.run(
                [
                    "ffmpeg", "-y", "-ss", str(t), "-i", clip_path,
                    "-vframes", "1", "-f", "image2pipe", "-c:v", "mjpeg", "pipe:1",
                ],
                cwd=WORK,
                capture_output=True,
                timeout=10,
            )
            if proc.returncode != 0 or not proc.stdout:
                continue
            buf = np.frombuffer(proc.stdout, dtype=np.uint8)
            img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
            if img is None:
                continue
            h, w = img.shape[:2]
            bottom_roi = img[int(h * 0.65) :, :]
            mid_roi = img[int(h * 0.40) : int(h * 0.75), :]
            gray_bottom = cv2.cvtColor(bottom_roi, cv2.COLOR_BGR2GRAY)
            def _edge_density(roi):
                g = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
                e = cv2.Canny(g, 50, 150)
                area = e.size
                return (e > 0).sum() / area if area else 0.0
            edge_bottom = _edge_density(bottom_roi)
            edge_mid = _edge_density(mid_roi)
            delta = edge_bottom - edge_mid
            bottoms.append(edge_bottom)
            mids.append(edge_mid)
            deltas.append(delta)
            textlike_bottom = _textlike_bottom(gray_bottom)
            textlikes.append(textlike_bottom)
            whiteness_raw = (gray_bottom > 230).sum() / gray_bottom.size if gray_bottom.size else 0.0
            whiteness_raws.append(whiteness_raw)
            vote_baked = (edge_bottom > 0.045 and delta > 0.015) and (textlike_bottom > 0.25 or whiteness_raw > 0.04)
            if vote_baked:
                frames_baked_votes += 1
            if SUBS_DEBUG:
                print(f"  [BAKED_DBG] t={t:.1f} bottom={edge_bottom:.3f} mid={edge_mid:.3f} delta={delta:.3f} textlike={textlike_bottom:.2f} white={whiteness_raw:.3f} like={vote_baked}")
        frames_total = len(bottoms)
        if frames_total < 3:
            print("  [SUBS] WARN: too few frames for baked detection; defaulting to burn", flush=True)
            return {"status": "uncertain", "score": 0.0, "frames_total": frames_total, "frames_baked_votes": frames_baked_votes, "avg_bottom": 0.0, "avg_mid": 0.0, "avg_delta": 0.0, "avg_textlike": 0.0, "avg_whiteness": 0.0}
        consistency = frames_baked_votes / frames_total
        avg_bottom = sum(bottoms) / len(bottoms)
        avg_mid = sum(mids) / len(mids)
        avg_delta = sum(deltas) / len(deltas)
        avg_textlike = sum(textlikes) / len(textlikes)
        avg_whiteness_raw = sum(whiteness_raws) / len(whiteness_raws)
        comp_edge = max(0.0, min(1.0, (avg_bottom - 0.030) / 0.030))
        comp_delta = max(0.0, min(1.0, (avg_delta - 0.012) / 0.020))
        comp_white = max(0.0, min(1.0, (avg_whiteness_raw - 0.03) / 0.08))
        score = max(0.0, min(1.0, 0.55 * consistency + 0.25 * comp_edge + 0.10 * comp_delta + 0.05 * avg_textlike + 0.05 * comp_white))
        if score >= BAKED_CONFIDENCE_THRESHOLD and avg_bottom >= BAKED_EDGE_BOTTOM_MIN and frames_baked_votes >= BAKED_FRAMES_VOTES_MIN:
            status = "baked"
        elif score <= 0.35:
            status = "clean"
        else:
            status = "uncertain"
        if SUBS_DEBUG:
            print(f"  [BAKED_DBG] caption_like_frames={frames_baked_votes} decoded_frames={frames_total} status={status} score={score:.2f}")
        return {"status": status, "score": score, "frames_total": frames_total, "frames_baked_votes": frames_baked_votes, "avg_bottom": avg_bottom, "avg_mid": avg_mid, "avg_delta": avg_delta, "avg_textlike": avg_textlike, "avg_whiteness": avg_whiteness_raw}
    except Exception:
        return {"status": "uncertain", "score": 0.0, "frames_total": 0, "frames_baked_votes": 0, "avg_bottom": 0.0, "avg_mid": 0.0, "avg_delta": 0.0, "avg_textlike": 0.0, "avg_whiteness": 0.0}


def verify_caption_present_rendered(rendered_mp4_path):
    """
    Fast OpenCV check on the rendered clip (1080x1920): sample N frames, inspect subtitle band (bottom),
    same text-like heuristics as baked detection. Returns True if caption-like content detected.
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        return False
    rendered_mp4_path = str(rendered_mp4_path)
    try:
        duration = get_video_duration(rendered_mp4_path)
        if duration <= 0:
            return False
        n = VERIFY_FRAMES
        times = [duration * (i + 1) / (n + 1) for i in range(n)]
        votes = 0
        for t in times:
            proc = subprocess.run(
                [
                    "ffmpeg", "-y", "-ss", str(t), "-i", rendered_mp4_path,
                    "-vframes", "1", "-f", "image2pipe", "-c:v", "mjpeg", "pipe:1",
                ],
                cwd=WORK,
                capture_output=True,
                timeout=10,
            )
            if proc.returncode != 0 or not proc.stdout:
                continue
            buf = np.frombuffer(proc.stdout, dtype=np.uint8)
            img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
            if img is None:
                continue
            h, w = img.shape[:2]
            bottom_roi = img[int(h * 0.65) :, :]
            mid_roi = img[int(h * 0.40) : int(h * 0.75), :]
            gray_bottom = cv2.cvtColor(bottom_roi, cv2.COLOR_BGR2GRAY)

            def _edge_density(roi):
                g = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
                e = cv2.Canny(g, 50, 150)
                return (e > 0).sum() / e.size if e.size else 0.0

            edge_bottom = _edge_density(bottom_roi)
            edge_mid = _edge_density(mid_roi)
            delta = edge_bottom - edge_mid
            textlike_bottom = _textlike_bottom(gray_bottom)
            whiteness_raw = (gray_bottom > 230).sum() / gray_bottom.size if gray_bottom.size else 0.0
            vote = (edge_bottom > 0.045 and delta > 0.015) and (textlike_bottom > 0.25 or whiteness_raw > 0.04)
            if vote:
                votes += 1
        return votes >= VERIFY_VOTES_MIN
    except Exception:
        return False


def snap_window_start(segments_list, start, end):
    """Snap start to nearest segment boundary within +/-1s; prefer capital/punct start."""
    candidates = []
    for seg in segments_list:
        if seg.start < start - 1 or seg.start > start + 1:
            continue
        if seg.start >= end:
            continue
        text = (seg.text or "").strip()
        cap_or_punct = bool(text and (text[0].isupper() or text[0] in ".!?"))
        candidates.append((seg.start, cap_or_punct))
    if not candidates:
        return start
    candidates.sort(key=lambda x: (-x[1], abs(x[0] - start)))
    return candidates[0][0]


def build_candidate_windows(segments_list, video_duration):
    """Yield (start, end) for each CLIP_WINDOW_SECONDS window starting at a segment boundary."""
    starts = sorted({seg.start for seg in segments_list})
    for start in starts:
        end = min(start + CLIP_WINDOW_SECONDS, video_duration)
        if end - start < 20:  # skip tiny tail windows
            continue
        yield (start, end)


def _text_in_range(segments_list, t_start, t_end):
    """Return concatenated transcript text for segments overlapping [t_start, t_end]."""
    parts = []
    for seg in segments_list:
        if seg.end <= t_start or seg.start >= t_end:
            continue
        parts.append((seg.text or "").strip())
    return " ".join(parts)


# Hook/payoff/bad-end keyword sets for clip selection v2
HOOK_STRONG = ["listen", "watch", "here's", "this is", "the truth", "you won't", "stop", "crazy", "wild", "insane", "real reason", "no one"]
PAYOFF_WORDS = ["so", "therefore", "that's why", "the point is", "which means", "and that's", "bottom line", "in the end"]
BAD_END_PHRASES = ["uh", "um", "like", "anyway", "so yeah", "i guess", "whatever", "kind of", "sort of", "you know", "alright", "okay"]
RESET_PHRASES = ["anyway", "but yeah", "so like", "moving on", "new topic"]
KEYWORD_STOP = {"a", "an", "the", "is", "it", "to", "of", "and", "in", "on", "for", "at", "that", "this", "with", "as", "or", "but", "if", "so", "be", "are", "was", "were", "have", "has", "had", "do", "does", "did", "will", "would", "can", "could", "i", "you", "we", "they", "he", "she"}

# Transcript-based reaction moment scoring (clip selection v3)
REACTION_PHRASES = [
    "no way", "what", "bro", "oh my god", "insane", "crazy", "wait", "watch this",
    "he did not", "are you serious", "let's go", "clip that", "nah", "wild",
]
PROFANITY_PATTERN = re.compile(
    r"\b(shit|damn|hell|wtf|omg|wtf|bs|bullshit|ass|wtf|freaking|fricking)\b",
    re.IGNORECASE,
)
REACTION_PHRASE_SCORE = 3.0
EXCLAMATION_BONUS = 0.8
QUESTION_BONUS = 0.6
SHORT_HIGH_ENERGY_WORDS_MAX = 6
SHORT_HIGH_ENERGY_BONUS = 1.0
PROFANITY_BONUS = 1.2
REPEATED_WORD_MIN_COUNT = 2
REPEATED_WORD_BONUS = 0.5
REACTION_MERGE_GAP_SEC = 8.0   # merge hot segments within this many seconds
REACTION_CONTEXT_BEFORE_SEC = 4.0
REACTION_CONTEXT_AFTER_SEC = 6.0
REACTION_TOP_N_SEGMENTS = 80   # rank and consider top N segments before merging

# Audio spike scoring (reaction clips: yelling/laughing/loud moments)
AUDIO_RMS_WINDOW_SEC = 0.5
AUDIO_CONTEXT_BEFORE_SEC = 5.0
AUDIO_CONTEXT_AFTER_SEC = 5.0
AUDIO_SPIKE_DB_SMALL = 2.0   # below this: +0
AUDIO_SPIKE_DB_MODERATE = 4.0  # below this: +1.0
AUDIO_SPIKE_DB_STRONG = 6.0   # below this: +2.0; else +3.0
AUDIO_RMS_MIN_DB = -60.0      # clamp silence to this
AUDIO_ANALYSIS_TIMEOUT_SEC = 120


def _get_audio_rms_timeline(video_path, window_sec, duration_sec):
    """
    Extract mono 16-bit PCM and compute RMS (dB) per window. Returns list of (t_start, rms_db)
    or None on failure. Deterministic; no new dependencies.
    """
    if not video_path or not os.path.isfile(video_path):
        return None
    try:
        sample_rate = 8000
        bytes_per_sample = 2
        samples_per_window = int(sample_rate * window_sec)
        cmd = ["ffmpeg", "-y", "-i", str(video_path)]
        if duration_sec is not None and duration_sec > 0:
            cmd.extend(["-t", str(duration_sec)])
        cmd.extend(["-vn", "-acodec", "pcm_s16le", "-ar", str(sample_rate), "-ac", "1", "-f", "s16le", "-"])
        proc = subprocess.run(
            cmd,
            cwd=WORK,
            capture_output=True,
            timeout=AUDIO_ANALYSIS_TIMEOUT_SEC,
        )
        if proc.returncode != 0 or not proc.stdout:
            return None
        raw = proc.stdout
        n = len(raw) // bytes_per_sample
        samples = []
        for i in range(0, n, bytes_per_sample):
            chunk = raw[i : i + bytes_per_sample]
            if len(chunk) == bytes_per_sample:
                samples.append(struct.unpack("<h", chunk)[0])
        if not samples:
            return None
        timeline = []
        t = 0.0
        i = 0
        while i + samples_per_window <= len(samples):
            window = samples[i : i + samples_per_window]
            i += samples_per_window
            sq_sum = sum(s * s for s in window)
            rms = math.sqrt(sq_sum / len(window)) if window else 0
            if rms <= 0:
                rms_db = AUDIO_RMS_MIN_DB
            else:
                rms_db = 20.0 * math.log10(rms / 32768.0)
                rms_db = max(AUDIO_RMS_MIN_DB, rms_db)
            if duration_sec is not None and t >= duration_sec:
                break
            timeline.append((t, rms_db))
            t += window_sec
        return timeline if timeline else None
    except (subprocess.TimeoutExpired, OSError, ValueError, struct.error):
        return None


def _segment_audio_spike_bonus(seg_start, seg_end, rms_timeline, context_before, context_after, duration_sec):
    """
    Compare segment loudness to local context. Returns (bonus 0..3, reason_str or None).
    bonus = 0 if spike small, 1 moderate, 2 strong, 3 very strong.
    """
    if not rms_timeline or seg_end <= seg_start:
        return (0.0, None)
    seg_energies = [rms_db for t, rms_db in rms_timeline if seg_start <= t < seg_end]
    ctx_start = max(0.0, seg_start - context_before)
    ctx_end = min(duration_sec, seg_end + context_after) if duration_sec else seg_end + context_after
    ctx_energies = [rms_db for t, rms_db in rms_timeline if (ctx_start <= t < seg_start) or (seg_end <= t < ctx_end)]
    if not seg_energies:
        return (0.0, None)
    segment_energy = sum(seg_energies) / len(seg_energies)
    if not ctx_energies:
        return (0.0, None)
    context_energy = sum(ctx_energies) / len(ctx_energies)
    spike_db = segment_energy - context_energy
    if spike_db < AUDIO_SPIKE_DB_SMALL:
        bonus = 0.0
    elif spike_db < AUDIO_SPIKE_DB_MODERATE:
        bonus = 1.0
    elif spike_db < AUDIO_SPIKE_DB_STRONG:
        bonus = 2.0
    else:
        bonus = 3.0
    return (bonus, f"audio_spike:{bonus:.1f}")


def hook_score(text_first2):
    """Score 0..1 for hook strength in first ~2s of window."""
    if not text_first2 or not text_first2.strip():
        return 0.0
    t = text_first2.strip().lower()
    s = 0.0
    if "?" in text_first2 or t.startswith("why ") or t.startswith("how ") or t.startswith("what "):
        s += 0.35
    if any(kw in t for kw in HOOK_STRONG):
        s += 0.25
    if re.search(r"\d+", text_first2) or any(w in t for w in ["first", "second", "third"]):
        s += 0.20
    if text_first2.strip() and text_first2.strip()[0].islower():
        s += 0.20
    return max(0.0, min(1.0, s))


def payoff_score(text_last2):
    """Score 0..1 for payoff/conclusion strength in last ~2s of window."""
    if not text_last2 or not text_last2.strip():
        return 0.0
    t = text_last2.strip().lower()
    s = 0.0
    if "." in text_last2 or "!" in text_last2:
        s += 0.40
    if any(pw in t for pw in PAYOFF_WORDS):
        s += 0.30
    words = len(t.split())
    if words >= 8:
        s += 0.30
    elif words >= 4:
        s += 0.15
    return max(0.0, min(1.0, s))


def bad_end_penalty(text_last2):
    """Penalty 0..1 for bad/filler ending in last ~2s (1 = bad)."""
    if not text_last2 or not text_last2.strip():
        return 0.0
    t = text_last2.strip().lower()
    p = 0.0
    for phrase in BAD_END_PHRASES:
        if phrase in t:
            p += 0.15
    if text_last2.strip() and text_last2.strip()[-1] not in ".!?":
        p += 0.25
    return max(0.0, min(1.0, p))


def _top_keywords(text, k=8):
    """Tokenize, lowercase, remove stopwords; return set of top-k by frequency."""
    tokens = re.findall(r"\w+", text.lower())
    tokens = [w for w in tokens if len(w) > 1 and w not in KEYWORD_STOP]
    if not tokens:
        return set()
    from collections import Counter
    counts = Counter(tokens)
    return {w for w, _ in counts.most_common(k)}


def topic_continuity_penalty(segments_list, start, end, window_text):
    """Penalty 0..0.6 for topic switch (low first-half vs second-half keyword overlap) + reset phrases."""
    penalty = 0.0
    try:
        mid = (start + end) / 2
        text_first_half = _text_in_range(segments_list, start, mid)
        text_second_half = _text_in_range(segments_list, mid, end)
        k1 = _top_keywords(text_first_half, 8)
        k2 = _top_keywords(text_second_half, 8)
        if k1 and k2:
            jaccard = len(k1 & k2) / len(k1 | k2)
            if jaccard < 0.15:
                penalty += 0.60
            elif jaccard < 0.25:
                penalty += 0.35
        wt_lower = window_text.lower()
        reset_count = sum(1 for r in RESET_PHRASES if r in wt_lower)
        penalty += min(0.40, 0.10 * reset_count)
    except Exception:
        pass
    return min(0.60, penalty)


def _segment_has_repeated_words(text):
    """True if text has same word repeated at least REPEATED_WORD_MIN_COUNT times."""
    if not text or not text.strip():
        return False
    from collections import Counter
    tokens = re.findall(r"\w+", text.lower())
    tokens = [t for t in tokens if len(t) > 1]
    if not tokens:
        return False
    counts = Counter(tokens)
    return any(c >= REPEATED_WORD_MIN_COUNT for c in counts.values())


def score_segment(seg):
    """
    Score a single transcript segment for reaction-moment quality.
    Returns (score, reasons_list) for logging. Deterministic.
    """
    text = (getattr(seg, "text", None) or "").strip()
    reasons = []
    score = 0.0
    t_lower = text.lower()
    words = text.split()
    word_count = len(words)

    # Reaction phrases
    for phrase in REACTION_PHRASES:
        if phrase in t_lower:
            score += REACTION_PHRASE_SCORE
            reasons.append(f"phrase:{phrase}")

    # Exclamation marks
    exc = text.count("!")
    if exc:
        score += exc * EXCLAMATION_BONUS
        reasons.append(f"excl:{exc}")

    # Question marks
    qm = text.count("?")
    if qm:
        score += qm * QUESTION_BONUS
        reasons.append(f"quest:{qm}")

    # Short high-energy line (few words, often punchy)
    if 1 <= word_count <= SHORT_HIGH_ENERGY_WORDS_MAX and (exc or qm or any(p in t_lower for p in REACTION_PHRASES)):
        score += SHORT_HIGH_ENERGY_BONUS
        reasons.append("short_energy")

    # Profanity
    if PROFANITY_PATTERN.search(text):
        score += PROFANITY_BONUS
        reasons.append("profanity")

    # Repeated words (e.g. "no no no", "what what")
    if _segment_has_repeated_words(text):
        score += REPEATED_WORD_BONUS
        reasons.append("repeated")

    return (max(0.0, score), reasons)


def get_reaction_candidates(segments_list, video_duration, video_path=None):
    """
    Score all segments, rank by score, merge nearby/overlapping hot spans,
    build clip windows with context (start a few sec before, end a few sec after).
    If video_path is set, add audio spike bonus per segment. Returns
    (candidates_list, segment_audio_bonuses_video). segment_audio_bonuses_video is
    list of (video_start, video_end, audio_bonus) or None if no audio analysis.
    """
    rms_timeline = None
    if video_path:
        try:
            rms_timeline = _get_audio_rms_timeline(
                video_path, AUDIO_RMS_WINDOW_SEC, video_duration
            )
        except Exception:
            rms_timeline = None
    segment_audio_bonuses_video = []
    # Score each segment and log [CLIP_SCORE]
    scored = []
    for seg in segments_list:
        s, reasons = score_segment(seg)
        text = (getattr(seg, "text", None) or "").strip()
        audio_bonus = 0.0
        audio_reason = None
        if rms_timeline:
            audio_bonus, audio_reason = _segment_audio_spike_bonus(
                seg.start, seg.end, rms_timeline,
                AUDIO_CONTEXT_BEFORE_SEC, AUDIO_CONTEXT_AFTER_SEC, video_duration,
            )
            s += audio_bonus
            if audio_reason:
                reasons = list(reasons) + [audio_reason]
            segment_audio_bonuses_video.append((seg.start, seg.end, audio_bonus))
        if s > 0:
            snippet = (text[:60] + "...") if len(text) > 60 else text
            reasons_str = ",".join(reasons) if reasons else ""
            print(f"[CLIP_SCORE] segment={snippet!r} start={seg.start:.2f} end={seg.end:.2f} score={s:.2f} reasons={reasons_str!s}", flush=True)
        scored.append((seg.start, seg.end, s, text, reasons))

    # Rank by score descending
    scored.sort(key=lambda x: -x[2])
    top = scored[:REACTION_TOP_N_SEGMENTS]
    # Filter to segments that actually have positive score
    hot = [(s, e, sc, txt, reasons) for s, e, sc, txt, reasons in top if sc > 0]
    if not hot:
        return ([], None if not segment_audio_bonuses_video else segment_audio_bonuses_video)

    # Merge overlapping or nearby (within REACTION_MERGE_GAP_SEC) into spans (start, end, combined_score, combined_reasons)
    hot.sort(key=lambda x: x[0])
    merged = []
    cur_start, cur_end, cur_score, cur_texts, cur_reasons = hot[0][0], hot[0][1], hot[0][2], [hot[0][3]], list(hot[0][4])
    for i in range(1, len(hot)):
        s, e, sc, txt, reasons = hot[i]
        if s <= cur_end + REACTION_MERGE_GAP_SEC:
            cur_end = max(cur_end, e)
            cur_score += sc
            cur_texts.append(txt)
            cur_reasons = list(dict.fromkeys(cur_reasons + reasons))
        else:
            merged.append((cur_start, cur_end, cur_score, " ".join(cur_texts), cur_reasons))
            cur_start, cur_end, cur_score, cur_texts, cur_reasons = s, e, sc, [txt], list(reasons)
    merged.append((cur_start, cur_end, cur_score, " ".join(cur_texts), cur_reasons))

    # Build clip windows with context
    candidates = []
    for start, end, score, window_text, reasons in merged:
        win_start = max(0.0, start - REACTION_CONTEXT_BEFORE_SEC)
        win_end = min(video_duration, end + REACTION_CONTEXT_AFTER_SEC)
        # Enforce minimum and max window length (use CLIP_WINDOW_SECONDS as cap)
        if win_end - win_start < 20:
            win_end = min(win_start + CLIP_WINDOW_SECONDS, video_duration)
            win_start = max(0.0, win_end - CLIP_WINDOW_SECONDS)
        if win_end - win_start > CLIP_WINDOW_SECONDS:
            win_end = win_start + CLIP_WINDOW_SECONDS
        if win_end - win_start < 20:
            continue
        reasons_str = ",".join(reasons[:5]) if reasons else "reaction"
        audio_in_first = any("audio_spike:" in r for r in reasons[:5])
        if not audio_in_first and reasons:
            audio_extra = [r for r in reasons if "audio_spike:" in r]
            if audio_extra:
                reasons_str = reasons_str + "," + audio_extra[0]
        candidates.append((win_start, win_end, score, window_text, reasons_str))
    # Sort by combined score descending
    candidates.sort(key=lambda x: -x[2])
    audio_out = segment_audio_bonuses_video if segment_audio_bonuses_video else None
    return (candidates, audio_out)


def score_window(start, end, segments_list, video_duration):
    """
    Score a window for "moment" quality. Returns (score, total_words, window_text, total_silence_sec, max_gap_sec).
    """
    window_duration = end - start
    total_words = 0
    window_text_parts = []
    score = 0.0

    # Segments that intersect [start, end]
    segs_in_window = []
    for seg in segments_list:
        if seg.end <= start or seg.start >= end:
            continue
        segs_in_window.append(seg)
        text = seg.text.strip()
        window_text_parts.append(text)
        total_words += len(text.split())
        score += text.count("?") + text.count("!")
        for kw in HOOK_KEYWORDS:
            score += len(re.findall(re.escape(kw), text, re.IGNORECASE))

    window_text = " ".join(window_text_parts)
    text_lower = window_text.lower()

    # A) Hook in first 3 seconds bonus
    first_3_text = " ".join(
        seg.text.strip() for seg in segs_in_window
        if seg.end > start and seg.start < start + 3
    ).lower()
    if "?" in first_3_text or any(kw in first_3_text for kw in HOOK_KEYWORDS):
        score += HOOK_IN_FIRST_3_BONUS

    # B) Dead-air / sparse transcript penalty (strong)
    segs_in_window.sort(key=lambda s: s.start)
    total_silence_sec = 0.0
    max_gap_sec = 0.0
    for i in range(1, len(segs_in_window)):
        gap = segs_in_window[i].start - segs_in_window[i - 1].end
        if gap > 0:
            total_silence_sec += gap
            max_gap_sec = max(max_gap_sec, gap)
            if gap > SILENCE_GAP_THRESHOLD:
                score -= (gap - SILENCE_GAP_THRESHOLD) * SILENCE_GAP_PENALTY_PER_SEC
    score -= total_silence_sec * SILENCE_TOTAL_PENALTY_PER_SEC

    # C) Event density bonus
    events = window_text.count(".") + window_text.count("!") + window_text.count("?") + window_text.count("\n")
    events_per_10s = events / (window_duration / 10.0) if window_duration > 0 else 0
    score += events_per_10s * EVENTS_PER_10S_BONUS

    # D) Anti-filler penalty
    filler_count = sum(len(re.findall(re.escape(fp), text_lower)) for fp in FILLER_PHRASES)
    score -= min(filler_count, FILLER_CAP) * FILLER_PENALTY_PER

    # E) Intro/outro/sponsor skip penalty
    if start < INTRO_CUTOFF_SEC:
        score -= INTRO_OUTRO_PENALTY
    if end > video_duration - OUTRO_CUTOFF_SEC:
        score -= INTRO_OUTRO_PENALTY
    if any(ind in text_lower for ind in SPONSOR_INDICATORS):
        score -= SPONSOR_PENALTY

    # Base words-per-second (existing)
    words_per_second = total_words / window_duration if window_duration > 0 else 0
    score += words_per_second

    return (score, total_words, window_text, total_silence_sec, max_gap_sec)


def overlap_ratio(a_start, a_end, b_start, b_end):
    """Overlap of [a_start,a_end] with [b_start,b_end] as fraction of first interval length."""
    overlap = max(0, min(a_end, b_end) - max(a_start, b_start))
    length_a = max(a_end - a_start, 1e-6)
    return overlap / length_a


def select_windows(segments_list, video_duration, video_path=None):
    """Return list of (start, end, score, total_silence_sec, max_gap_sec, window_text) for top N non-overlapping windows.
    Uses transcript-based reaction scoring first (with optional audio spike), then fallback to segment-boundary windows."""
    candidates = []
    reasons_by_key = {}  # (start, end) -> reasons_str for [CLIP_PICK]

    def add_candidate(start, end, window_text, total_silence, max_gap, old_score, hook, payoff, bad_end, topic_pen, reaction_bonus, reasons_str):
        new_score = old_score + 0.60 * hook + 0.50 * payoff - 0.70 * bad_end - 0.60 * topic_pen + reaction_bonus
        key = (round(start, 2), round(end, 2))
        reasons_by_key[key] = reasons_str or ""
        candidates.append((new_score, start, end, total_silence, max_gap, window_text, old_score, hook, payoff, bad_end, topic_pen, reasons_str))

    # 1) Reaction-based candidates (transcript + optional audio spike)
    react_candidates, segment_audio_bonuses_video = get_reaction_candidates(segments_list, video_duration, video_path=video_path)
    for start, end, react_score, window_text, reasons_str in react_candidates:
        if spoken_ratio(segments_list, start, end) < MIN_SPEECH_COVERAGE:
            continue
        old_score, words, wt, total_silence, max_gap = score_window(start, end, segments_list, video_duration)
        if words < 10:
            continue
        try:
            text_first2 = _text_in_range(segments_list, start, start + 2)
            text_last2 = _text_in_range(segments_list, end - 2, end)
            hook = hook_score(text_first2)
            payoff = payoff_score(text_last2)
            bad_end = bad_end_penalty(text_last2)
            topic_pen = topic_continuity_penalty(segments_list, start, end, window_text)
        except Exception:
            hook = payoff = bad_end = topic_pen = 0.0
        add_candidate(start, end, window_text, total_silence, max_gap, old_score, hook, payoff, bad_end, topic_pen, react_score, reasons_str)

    # 2) Fallback: segment-boundary windows (existing behavior)
    for start, end in build_candidate_windows(segments_list, video_duration):
        if spoken_ratio(segments_list, start, end) < MIN_SPEECH_COVERAGE:
            continue
        old_score, words, window_text, total_silence, max_gap = score_window(start, end, segments_list, video_duration)
        if words < 10:
            continue
        try:
            text_first2 = _text_in_range(segments_list, start, start + 2)
            text_last2 = _text_in_range(segments_list, end - 2, end)
            hook = hook_score(text_first2)
            payoff = payoff_score(text_last2)
            bad_end = bad_end_penalty(text_last2)
            topic_pen = topic_continuity_penalty(segments_list, start, end, window_text)
            new_score = old_score + 0.60 * hook + 0.50 * payoff - 0.70 * bad_end - 0.60 * topic_pen
        except Exception:
            new_score = old_score
            hook = payoff = bad_end = topic_pen = 0.0
        key = (round(start, 2), round(end, 2))
        if key not in reasons_by_key:
            reasons_by_key[key] = ""
            candidates.append((new_score, start, end, total_silence, max_gap, window_text, old_score, hook, payoff, bad_end, topic_pen, ""))

    candidates.sort(key=lambda x: -x[0])
    top_candidates = []
    for c in candidates[:10]:
        total, start, end, _, _, _, old_score, hook, payoff, bad_end, topic_pen = c[0], c[1], c[2], c[3], c[4], c[5], c[6], c[7], c[8], c[9], c[10]
        top_candidates.append({
            "t0": round(start, 2),
            "t1": round(end, 2),
            "old_score": round(old_score, 4),
            "hook": round(hook, 4),
            "payoff": round(payoff, 4),
            "bad_end": round(bad_end, 4),
            "topic_penalty": round(topic_pen, 4),
            "total_score": round(total, 4),
        })
    if CLIP_DEBUG or SUBS_DEBUG:
        for c in candidates[:10]:
            total, start, end, _, _, _, old_score, hook, payoff, bad_end, topic_pen = c[0], c[1], c[2], c[3], c[4], c[5], c[6], c[7], c[8], c[9], c[10]
            print(f"  [WIN_SCORE] t0={start:.1f} t1={end:.1f} old={old_score:.2f} hook={hook:.2f} payoff={payoff:.2f} badend={bad_end:.2f} topic={topic_pen:.2f} total={total:.2f}")
    selected = []
    for c in candidates:
        score, start, end, total_silence, max_gap, window_text = c[0], c[1], c[2], c[3], c[4], c[5]
        reasons_str = c[11] if len(c) > 11 else ""
        if len(selected) >= MAX_CLIPS:
            break
        start = snap_window_start(segments_list, start, end)
        end = min(start + CLIP_WINDOW_SECONDS, video_duration)
        if any(overlap_ratio(start, end, s, e) > MAX_OVERLAP_RATIO for s, e in [(x[0], x[1]) for x in selected]):
            continue
        if any(_jaccard_tokens(window_text, wtext) > DIVERSITY_JACCARD_THRESHOLD for (_, _, _, _, _, wtext) in selected):
            continue
        rank = len(selected) + 1
        top_reasons = reasons_str or "hook,payoff,coverage"
        print(f"[CLIP_PICK] rank={rank} start={start:.2f} end={end:.2f} reason={top_reasons!s}", flush=True)
        selected.append((start, end, score, total_silence, max_gap, window_text))
        if len(selected) >= MIN_CLIPS and score <= 0:
            break
    # Final ranking diagnostics: for each selected clip, log nearby non-selected competitors
    NEARBY_SEC = 15
    for rank, s in enumerate(selected[:MAX_CLIPS], 1):
        s_start, s_end, s_score, _, _, s_text = s[0], s[1], s[2], s[3], s[4], s[5]
        s_text_snippet = (s_text[:120] + "...") if len(s_text or "") > 120 else (s_text or "")
        # Candidate is "in selected" if it overlaps almost entirely with some selected window
        def _candidate_in_selected(c_start, c_end):
            for os_start, os_end in [(x[0], x[1]) for x in selected]:
                if overlap_ratio(c_start, c_end, os_start, os_end) > 0.95:
                    return True
            return False

        def _nearby(c_start, c_end):
            if c_end > s_start and c_start < s_end:
                return True
            return (
                abs(c_start - s_start) <= NEARBY_SEC or abs(c_start - s_end) <= NEARBY_SEC
                or abs(c_end - s_start) <= NEARBY_SEC or abs(c_end - s_end) <= NEARBY_SEC
            )

        nearby_competitors = []
        for c in candidates:
            c_score, c_start, c_end, c_text = c[0], c[1], c[2], c[5]
            if _candidate_in_selected(c_start, c_end):
                continue
            if not _nearby(c_start, c_end):
                continue
            c_overlap = c_end > s_start and c_start < s_end
            score_gap = s_score - c_score
            c_text_snippet = (c_text[:120] + "...") if len(c_text or "") > 120 else (c_text or "")
            entry = {"start": round(c_start, 3), "end": round(c_end, 3), "score": round(c_score, 4), "score_gap": round(score_gap, 4), "text": c_text_snippet, "overlap": c_overlap}
            if len(c) >= 11:
                entry["old_score"] = round(c[6], 4)
                entry["hook"] = round(c[7], 4)
                entry["payoff"] = round(c[8], 4)
                entry["bad_end"] = round(c[9], 4)
                entry["topic_penalty"] = round(c[10], 4)
            nearby_competitors.append(entry)
        nearby_competitors.sort(key=lambda x: -x["score"])
        nearby_competitors = nearby_competitors[:5]
        selected_entry = {"selected_start": round(s_start, 3), "selected_end": round(s_end, 3), "selected_score": round(s_score, 4), "selected_text": s_text_snippet, "rank": rank, "nearby_competitors": nearby_competitors}
        print("[CLIP_RANK]", selected_entry, flush=True)
    return selected[:MAX_CLIPS], top_candidates, segment_audio_bonuses_video


def segments_in_window(segments_list, start, end):
    """Segments that intersect [start, end], with times clipped to window. Optional 4th: words list with start/end relative to clip."""
    out = []
    for seg in segments_list:
        if seg.end <= start or seg.start >= end:
            continue
        clip_start = max(seg.start, start) - start
        clip_end = min(seg.end, end) - start
        words_rel = None
        seg_words = getattr(seg, "words", None)
        if seg_words:
            words_rel = []
            for w in seg_words:
                if isinstance(w, dict):
                    wa, we, wt = w["start"], w["end"], w["word"]
                else:
                    wa, we, wt = w.start, w.end, w.word
                if wa < end and we > start:
                    words_rel.append({"word": wt, "start": wa - start, "end": we - start})
            if not words_rel:
                words_rel = None
        if words_rel is not None:
            out.append((clip_start, clip_end, seg.text.strip(), words_rel))
        else:
            out.append((clip_start, clip_end, seg.text.strip()))
    return out


def smooth_subtitle_segments(segments_rel):
    """
    Make subtitles continuous and readable: merge adjacent segments, enforce min duration,
    fill short gaps, and optionally split long phrases into 4-6 word chunks.
    segments_rel: list of (start, end, text) with start/end in seconds (e.g. relative to clip).
    Returns list of (start, end, text). Empty or failed input returns unchanged.
    """
    if not segments_rel:
        return segments_rel
    try:
        segs = [list(x) for x in segments_rel]
        # A) Merge consecutive if gap <= SUBS_MERGE_GAP_SEC
        merged = []
        i = 0
        while i < len(segs):
            s, e, t = segs[i][0], segs[i][1], segs[i][2]
            words_merged = list(segs[i][3]) if len(segs[i]) >= 4 and segs[i][3] else []
            j = i + 1
            while j < len(segs) and (segs[j][0] - e) <= SUBS_MERGE_GAP_SEC:
                e = segs[j][1]
                t = (t + " " + (segs[j][2] or "")).strip()
                if len(segs[j]) >= 4 and segs[j][3]:
                    words_merged.extend(segs[j][3])
                j += 1
            row = [s, e, t]
            if words_merged:
                row.append(words_merged)
            merged.append(row)
            i = j

        # D) Gap fill: only if SUBS_FILL_GAP_SEC > 0 (disabled for accurate timing)
        if SUBS_FILL_GAP_SEC > 0:
            for i in range(len(merged) - 1):
                next_start = merged[i + 1][0]
                if (next_start - merged[i][1]) <= SUBS_FILL_GAP_SEC:
                    merged[i][1] = next_start

        # B) Min duration: slight extension to avoid flicker only
        for i in range(len(merged)):
            s, e, t = merged[i]
            next_start = merged[i + 1][0] if i + 1 < len(merged) else (e + 2.0)
            if (e - s) < SUBS_MIN_DURATION_SEC:
                e = min(s + SUBS_MIN_DURATION_SEC, next_start)
                merged[i][1] = e

        # E) Split long segments so no gap: one line stays max SUBS_MAX_DURATION_SEC, but we add more lines to cover the full time
        expanded = []
        for row in merged:
            s, e, t = row[0], row[1], row[2]
            dur = e - s
            if dur <= SUBS_MAX_DURATION_SEC:
                expanded.append(row)
                continue
            words = (t or "").split()
            nw = len(words)
            num_slices = max(1, int((dur + SUBS_MAX_DURATION_SEC - 0.01) // SUBS_MAX_DURATION_SEC))
            for k in range(num_slices):
                start_k = s + k * SUBS_MAX_DURATION_SEC
                end_k = min(s + (k + 1) * SUBS_MAX_DURATION_SEC, e)
                if nw == 0:
                    expanded.append([start_k, end_k, t or ""])
                    continue
                i0 = (k * nw) // num_slices
                i1 = ((k + 1) * nw) // num_slices if k + 1 < num_slices else nw
                chunk_text = " ".join(words[i0:i1]) if i1 > i0 else (words[i0] if i0 < nw else "")
                expanded.append([start_k, end_k, chunk_text])
        merged = expanded

        # C) Split into 4-6 word chunks if text > SUBS_MAX_WORDS_BEFORE_CHUNK words
        out = []
        for row in merged:
            s, e, t = row[0], row[1], row[2]
            words = (t or "").split()
            if len(words) <= SUBS_MAX_WORDS_BEFORE_CHUNK:
                out.append((s, e, t) if len(row) == 3 else (s, e, t, row[3]))
                continue
            # Chunk size 4-6 words; distribute evenly across duration (no word timestamps on chunks)
            n = len(words)
            num_chunks = max(1, (n + SUBS_CHUNK_WORDS_MAX - 1) // SUBS_CHUNK_WORDS_MAX)
            chunk_size = max(SUBS_CHUNK_WORDS_MIN, (n + num_chunks - 1) // num_chunks)
            chunk_size = min(SUBS_CHUNK_WORDS_MAX, chunk_size)
            num_chunks = max(1, (n + chunk_size - 1) // chunk_size)
            dur = max(0.01, e - s)
            step = dur / num_chunks
            for k in range(num_chunks):
                start_k = s + k * step
                end_k = s + (k + 1) * step if k < num_chunks - 1 else e
                chunk_words = words[k * chunk_size : min((k + 1) * chunk_size, n)]
                if chunk_words:
                    out.append((start_k, end_k, " ".join(chunk_words)))
        return out if out else segments_rel
    except Exception:
        return segments_rel


def write_srt(path, segments_rel):
    """Write SRT file with shifted segments (relative to clip start)."""
    with open(path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments_rel, 1):
            s, e, text = seg[0], seg[1], seg[2]
            f.write(f"{i}\n{srt_time(s)} --> {srt_time(e)}\n{text}\n\n")


def _progress(stage: str, pct: int, msg: str) -> None:
    print(f"[PROGRESS] stage={stage} pct={pct} msg={msg}", flush=True)


def _is_valid_youtube_id(video_id):
    """YouTube IDs are exactly 11 chars, alphanumeric plus - and _."""
    if not video_id or not isinstance(video_id, str):
        return False
    s = video_id.strip()
    if len(s) != 11:
        return False
    return all(c.isalnum() or c in "-_" for c in s)


def _extract_youtube_video_id(url):
    """Extract video ID from YouTube URL (v=, youtu.be/, embed/, shorts/)."""
    if not url or not isinstance(url, str):
        return ""
    url = url.strip()
    if "youtu.be/" in url:
        try:
            return url.split("youtu.be/")[1].split("?")[0].split("/")[0].strip() or ""
        except IndexError:
            pass
    if "shorts/" in url:
        try:
            return url.split("shorts/")[1].split("?")[0].split("/")[0].strip() or ""
        except IndexError:
            pass
    for prefix in ("v=", "embed/"):
        if prefix in url:
            try:
                return url.split(prefix)[1].split("&")[0].split("?")[0].split("/")[0].strip() or ""
            except IndexError:
                pass
    return ""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="")
    parser.add_argument("--max-clips", type=int, default=None)
    parser.add_argument("--clip-seconds", type=int, default=None)
    args = parser.parse_args()

    # argv wins; env is fallback only (server always passes --url)
    final_url = (args.url or "").strip() or (os.environ.get("CLIP_URL") or "").strip()
    if not final_url:
        print("YouTube Short Generator (multi-clip)")
        print("------------------------------------")
        final_url = input("\nPaste YouTube link: ").strip()
    if not final_url:
        print("No URL provided. Exiting.")
        sys.exit(1)
    print(f"[RUN] url={final_url}", flush=True)
    url = final_url

    video_id = os.environ.get("SOURCE_VIDEO_ID", "").strip() or _extract_youtube_video_id(url) or ""
    if not video_id or video_id.lower() == "unknown" or not _is_valid_youtube_id(video_id):
        print("Could not extract video_id from URL. Refusing to continue to prevent reuse.", file=sys.stderr)
        sys.exit(1)
    # Only downloads/<video_id>.mp4 is allowed; no constant or unknown
    source_path = DOWNLOADS_DIR / f"{video_id}.mp4"
    output_template = str(DOWNLOADS_DIR / f"{video_id}.%(ext)s")

    job_id = os.environ.get("CLIP_JOB_ID", "")
    source_video_id = os.environ.get("SOURCE_VIDEO_ID") or video_id or "unknown"
    source_url = os.environ.get("SOURCE_URL") or url
    # Job-scoped output: when server sets OUTPUTS_DIR_JOB, write all clips under that job's clips/ subdir
    out_dir_env = os.environ.get("OUTPUTS_DIR_JOB")
    OUT_DIR = Path(out_dir_env or str(OUTPUTS_DIR)).resolve()
    CLIPS_DIR = (OUT_DIR / "clips") if out_dir_env else OUT_DIR
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    if job_id and out_dir_env:
        try:
            (OUT_DIR / "job.meta.json").write_text(json.dumps({
                "job_id": job_id,
                "source_url": source_url,
                "source_video_id": source_video_id or "unknown",
                "created_at": datetime.now().isoformat() + "Z",
            }, indent=2), encoding="utf-8")
        except OSError:
            pass

    global MAX_CLIPS, CLIP_WINDOW_SECONDS, WINDOW_SECONDS
    if args.max_clips is not None:
        MAX_CLIPS = args.max_clips
    if args.clip_seconds is not None:
        CLIP_WINDOW_SECONDS = args.clip_seconds
        WINDOW_SECONDS = args.clip_seconds

    clip_prefix = os.environ.get("CLIP_PREFIX", "")

    try:
        _progress("idle", 0, "Starting")
        _warn_cv2_numpy_once()  # one-time message if cv2/numpy missing for baked subs
        run_state_path = PROJECT_ROOT / "run_state.json"
        run_state = {}
        if run_state_path.exists():
            try:
                run_state = json.loads(run_state_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        # 1) Download (video_id-scoped; no cross-video reuse)
        FORCE_REDOWNLOAD = os.environ.get("FORCE_REDOWNLOAD", "").strip().lower() in ("1", "true", "yes")
        if FORCE_REDOWNLOAD:
            if source_path.exists():
                source_path.unlink()
            for f in DOWNLOADS_DIR.glob(f"{video_id}.*"):
                if f.is_file():
                    f.unlink()

        if source_path.exists() and source_path.stat().st_size >= CACHE_VIDEO_MIN_MB * 1024 * 1024:
            print(f"\n[1/6] Using cached {source_path.name}")
            _progress("download", 15, "Using cached video")
            print(f"[DL] video_id={video_id} source=downloads/{video_id}.mp4 cached=True", flush=True)
        else:
            _progress("download", 5, "Downloading video")
            print(f"\n[1/6] Downloading to {output_template}...")
            COOKIES_MSG = "This YouTube video requires cookies or a local download environment."
            try:
                def _download_progress_line(line):
                    if line and len(line) <= 120:
                        _progress("download", 5, line)
                    elif line:
                        _progress("download", 5, line[:117] + "...")
                print(f"[YTDLP_ATTEMPT] mode=normal", flush=True)
                ok, out = _run_ytdlp_download(
                    url,
                    output_template,
                    timeout_sec=DOWNLOAD_TIMEOUT_SEC,
                    stall_sec=DOWNLOAD_STALL_NO_OUTPUT_SEC,
                    progress_cb=_download_progress_line,
                    cookies_path=None,
                )
                if not ok:
                    signin = _is_ytdlp_signin_challenge(out)
                    if signin:
                        print(f"[YTDLP_BLOCKED] detected_signin_challenge=true", flush=True)
                    # If output hints at bot/cookies even without exact match, treat as sign-in for message
                    if not signin and out and ("bot" in out.lower() or "cookies" in out.lower()) and "[youtube]" in out.lower():
                        signin = True
                        print(f"[YTDLP_BLOCKED] detected_signin_challenge=true (fallback)", flush=True)
                    cookies_path = _get_cookies_path()
                    if signin and cookies_path:
                        print(f"[YTDLP_COOKIES] using={cookies_path}", flush=True)
                        print(f"[YTDLP_ATTEMPT] mode=cookies", flush=True)
                        ok2, out2 = _run_ytdlp_download(
                            url,
                            output_template,
                            timeout_sec=DOWNLOAD_TIMEOUT_SEC,
                            stall_sec=DOWNLOAD_STALL_NO_OUTPUT_SEC,
                            progress_cb=_download_progress_line,
                            cookies_path=cookies_path,
                        )
                        if not ok2:
                            print(f"[YTDLP_FAIL_FINAL] reason=cookies_retry_failed", flush=True)
                            print(f"\n[ERROR] {COOKIES_MSG}", file=sys.stderr)
                            sys.exit(1)
                    elif signin:
                        print(f"[YTDLP_FAIL_FINAL] reason=no_cookies", flush=True)
                        print(f"\n[ERROR] {COOKIES_MSG}", file=sys.stderr)
                        sys.exit(1)
                    else:
                        print(f"\n[ERROR] Download failed (exit code 1). Try again or use cookies.", file=sys.stderr)
                        sys.exit(1)
            except RuntimeError as e:
                err_msg = str(e)
                print(f"\n[ERROR] {err_msg}", file=sys.stderr)
                print(f"[DL_DEBUG] end={datetime.now().isoformat()} failure={err_msg!r}", flush=True)
                sys.exit(1)
            except Exception as e:
                err_msg = f"Download failed: {e}"
                print(f"\n[ERROR] {err_msg}", file=sys.stderr)
                print(f"[DL_DEBUG] end={datetime.now().isoformat()} failure={err_msg!r}", flush=True)
                sys.exit(1)
            # Resolve actual file (mp4/webm/mkv); normalize to source_path
            candidates = [f for f in DOWNLOADS_DIR.glob(f"{video_id}.*") if f.is_file() and f.suffix != ".json" and ".part" not in f.name]
            if not candidates:
                print(f"\n[ERROR] yt-dlp did not produce a video file under {DOWNLOADS_DIR} for {video_id}", file=sys.stderr)
                sys.exit(1)
            downloaded = max(candidates, key=lambda p: p.stat().st_mtime)
            if downloaded.suffix.lower() != ".mp4":
                run(
                    ["ffmpeg", "-y", "-i", str(downloaded), "-c", "copy", str(source_path)],
                    "Convert to mp4",
                )
                try:
                    downloaded.unlink()
                except OSError:
                    pass
            elif downloaded.resolve() != source_path.resolve():
                downloaded.rename(source_path)
            print(f"[DL] video_id={video_id} source=downloads/{video_id}.mp4 cached=False", flush=True)
            print(f"  Done: {source_path.name}")
            _progress("download", 15, "Download complete")
        # All downstream steps use source_path only (no global)
        video_path = source_path
        run_state["downloaded"] = True

        # Download integrity proof
        try:
            download_bytes = source_path.stat().st_size
            download_sha1 = hashlib.sha1(source_path.read_bytes()).hexdigest()
            print(f"[DL_DONE] video_id={video_id} bytes={download_bytes} sha1={download_sha1}", flush=True)
            if job_id and out_dir_env:
                meta_path = OUT_DIR / "job.meta.json"
                meta = {}
                if meta_path.exists():
                    try:
                        meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    except Exception:
                        pass
                meta["download_bytes"] = download_bytes
                meta["download_sha1"] = download_sha1
                try:
                    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
                except OSError:
                    pass
        except Exception as ex:
            print(f"[DL_DONE] video_id={video_id} integrity_error={ex!r}", flush=True)

        # 2) Transcribe (use cache if valid)
        cache_path = PROJECT_ROOT / "cache_transcript.json"
        video_mtime = video_path.stat().st_mtime
        video_size = video_path.stat().st_size
        segments_list = None
        if cache_path.exists():
            try:
                data = json.loads(cache_path.read_text(encoding="utf-8"))
                if data.get("video_mtime") == video_mtime and data.get("video_size") == video_size:
                    segments_list = [_Seg(s["start"], s["end"], s["text"], s.get("words")) for s in data.get("segments", [])]
                    print("\n[2/6] Using cached transcript.")
                    _progress("transcribe", 45, "Using cached transcript")
            except Exception:
                pass
        if segments_list is None:
            _progress("transcribe", 25, "Transcribing audio")
            print("\n[2/6] Transcribing...")
            add_cuda_bin_to_path()
            from faster_whisper import WhisperModel
            try:
                model = WhisperModel(WHISPER_MODEL, device="cuda", compute_type="float16")
                print(f"  Using GPU (CUDA), model={WHISPER_MODEL}")
            except Exception:
                model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
                print(f"  Using CPU (int8), model={WHISPER_MODEL}")
            try:
                segments_gen, _ = model.transcribe(str(source_path), vad_filter=True, word_timestamps=True)
                segments_list = list(segments_gen)
            except RuntimeError as e:
                err = str(e)
                if "cublas64_12.dll" in err or "cudnn" in err.lower() or "cuda" in err.lower():
                    print("  GPU failed, falling back to CPU")
                    model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
                    segments_gen, _ = model.transcribe(str(source_path), vad_filter=True, word_timestamps=True)
                    segments_list = list(segments_gen)
                else:
                    raise
            def _seg_to_dict(s):
                d = {"start": s.start, "end": s.end, "text": getattr(s, "text", "")}
                if getattr(s, "words", None):
                    d["words"] = [{"word": w.word, "start": w.start, "end": w.end} for w in s.words]
                return d
            cache_path.write_text(json.dumps({
                "video_mtime": video_mtime, "video_size": video_size,
                "segments": [_seg_to_dict(s) for s in segments_list]
            }), encoding="utf-8")
            _progress("transcribe", 45, "Transcription complete")
        print(f"  Done: {len(segments_list)} segments")

        video_duration = get_video_duration(str(source_path))
        print(f"  Video duration: {video_duration:.1f}s")

        # 3) Select windows
        _progress("select", 55, "Selecting best moments")
        print("\n[3/6] Selecting best moments...")
        windows, top_candidates, segment_audio_bonuses_video = select_windows(segments_list, video_duration, video_path=str(source_path))
        if not windows:
            print("  No suitable windows found. Exiting.")
            sys.exit(1)
        job_id = os.environ.get("CLIP_JOB_ID", "")
        win_scores_path = OUT_DIR / "job.win_scores.json" if job_id else OUTPUTS_DIR / "job.win_scores.json"
        try:
            win_scores_path.parent.mkdir(parents=True, exist_ok=True)
            win_scores_path.write_text(json.dumps({"version": "clip_score_v1", "top_candidates": top_candidates}, indent=2), encoding="utf-8")
        except Exception:
            pass
        for i, (_, _, _, _, _, wtext) in enumerate(windows, 1):
            text_flat = (wtext or "").replace("\r", " ").replace("\n", " ")[:500]
            fname = f"{clip_prefix}short_{i}.mp4"
            print(f"[CLIP_INFO] {fname}|{text_flat}", flush=True)
        print(f"  Selected {len(windows)} clip(s)")
        _progress("select", 65, f"Selected {len(windows)} clips")
        for i, (start, end, score, silence_total, max_gap, wtext) in enumerate(windows, 1):
            preview = (wtext[:120] + "..." if len(wtext) > 120 else wtext).replace("\n", " ")
            print(f"  Clip {i}: {srt_time(start)} -> {srt_time(end)}  score={score:.2f}  silence_total={silence_total:.1f}s  max_gap={max_gap:.1f}s")
            print(f"    \"{preview}\"")

        use_nvenc = nvenc_available()
        if use_nvenc:
            print("  Using NVIDIA NVENC for encoding.")
        else:
            print("  Using software encoding (libx264).")

        run_state["windows_selected"] = [[float(w[0]), float(w[1])] for w in windows]
        summary = []
        clips_subs_entries = []

        for i, item in enumerate(windows, 1):
            start, end = item[0], item[1]
            score = item[2]
            wtext = item[5]
            clip_start_sec = start
            clip_end_sec = end
            clip_duration = clip_end_sec - clip_start_sec
            segment_audio_bonuses = []
            if segment_audio_bonuses_video:
                for vs, ve, b in segment_audio_bonuses_video:
                    if ve <= clip_start_sec or vs >= clip_end_sec:
                        continue
                    relative_start = max(0.0, vs - clip_start_sec)
                    relative_end = min(clip_duration, ve - clip_start_sec)
                    if relative_end <= relative_start:
                        continue
                    segment_audio_bonuses.append((relative_start, relative_end, b))
            base_name = f"{clip_prefix}short_{i}.mp4"
            final_mp4 = CLIPS_DIR / base_name
            # Adaptive TikTok caption: classify clip and write .tiktok.json
            duration_sec = end - start
            word_count = len((wtext or "").split())
            wtext_stripped = (wtext or "").strip()
            ends_with_strong_punctuation = bool(wtext_stripped and wtext_stripped[-1] in ".!?")
            text_last2 = _text_in_range(segments_list, end - 2, end)
            bad_end_score = bad_end_penalty(text_last2)
            text_last3 = _text_in_range(segments_list, end - 3, end).lower()
            has_question_words = any(q in text_last3 for q in QUESTION_WORDS_LAST3)
            looks_like_story = word_count >= 80
            caption_style = classify_caption_style(
                duration_sec, word_count, ends_with_strong_punctuation,
                bad_end_score, has_question_words, looks_like_story,
                window_text=wtext,
            )
            caption = get_caption_for_style(caption_style)
            upload_filename = Path(base_name).stem
            suggested_title = title_from_window_text(wtext or "", max_len=70)
            tiktok_path = CLIPS_DIR / (upload_filename + ".tiktok.json")
            write_clip_tiktok_json(tiktok_path, caption, caption_style, DEFAULT_HASHTAGS, upload_filename, job_id=job_id, source_video_id=source_video_id or "unknown", source_url=source_url, suggested_title=suggested_title)

            rendered = run_state.get("clips_rendered") or []
            pct_render = 70 + int((i - 1) * (25.0 / max(len(windows), 1)))
            _progress("render", min(pct_render, 94), f"Rendering clip {i}/{len(windows)}")
            if i in rendered and final_mp4.exists() and final_mp4.stat().st_size >= MIN_OUTPUT_SIZE_MB * 1024 * 1024:
                print(f"\n[4/6] Clip {i}/{len(windows)}: (cached) {srt_time(start)} --> {srt_time(end)}")
                summary.append((start, end, final_mp4, None, None))
                continue

            print(f"\n[4/6] Clip {i}/{len(windows)}: {srt_time(start)} --> {srt_time(end)}")

            clip_mp4 = os.path.join(WORK, f"clip_{i}.mp4")
            captions_srt = os.path.join(WORK, f"captions_{i}.srt")
            short_mp4 = CLIPS_DIR / base_name

            # 4a) Extract clip
            run(
                ["ffmpeg", "-y", "-ss", str(start), "-to", str(end), "-i", str(source_path), "-c", "copy", clip_mp4],
                f"Extract clip_{i}",
            )

            # 4b) SRT and ASS for this clip (smooth: light merge, min duration, no gap fill, cap max)
            segs_rel = segments_in_window(segments_list, start, end)
            segs_rel = smooth_subtitle_segments(segs_rel)
            write_srt(captions_srt, segs_rel)
            captions_ass = os.path.join(WORK, f"captions_{i}.ass")

            # Automatic subs: skip when creator already has captions (high confidence), else burn
            sub_res = detect_baked_captions(clip_mp4)
            status = sub_res["status"]
            score = sub_res["score"]
            if SUBS_MODE == "off":
                initial_action = "skip"
            elif SUBS_MODE == "on":
                initial_action = "burn"
            else:
                initial_action = "skip" if score >= INITIAL_SKIP_CONFIDENCE else "burn"
            burn_subs = initial_action == "burn"
            placement = None
            sub_margin_v = CAPTION_MARGIN_V
            if burn_subs:
                placement = compute_safe_subtitle_y(clip_mp4)
                if placement and placement.get("mode") in ("bottom", "shifted"):
                    sub_margin_v = OUT_H - placement["y"] - placement["band_h"] + 100
                sub_margin_v = max(CAPTION_MARGIN_V, min(800, sub_margin_v))
                pass_audio = segment_audio_bonuses if segment_audio_bonuses else None
                print("[CAPTION_AUDIO_MAP]", {"clip_start": clip_start_sec, "clip_end": clip_end_sec, "count": len(segment_audio_bonuses), "windows": segment_audio_bonuses[:10]}, flush=True)
                write_ass(captions_ass, segs_rel, margin_v=sub_margin_v, use_karaoke=True, segment_audio_bonuses=pass_audio)
                ass_abs = str(Path(captions_ass).resolve())
                print(f"  [SUBS] karaoke=1 ass={ass_abs} primary={CAPTION_KARAOKE_PRIMARY} secondary={CAPTION_KARAOKE_SECONDARY}", flush=True)
                print(f"  [SUBS_PLACEMENT] clip={base_name} mode={placement['mode']} y={placement['y']} band_h={placement['band_h']} faces={placement['faces']} margin_v={sub_margin_v}")
            else:
                placement = {"mode": "skipped"}

            def do_render(burn):
                # Normalize PTS to 0 so ASS subtitle times (0-based for clip) stay in sync with video
                # FIT_MODE: COVER = crop 9:16; BLUR_TB/BLUR_BANDS = no-crop full-width, blur top/bottom only, 1080x1920
                print(f"  [VIDEO] fit_mode_env={FIT_MODE}", flush=True)
                print(f"  [VIDEO] fit_mode_active={_render_fit_mode}", flush=True)
                if _render_fit_mode == "COVER":
                    vbase = "[0:v]setpts=PTS-STARTPTS[v0];[v0]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920[vbase]"
                    print(f"  [VIDEO] no_crop_blur_tb=0", flush=True)
                else:
                    # BLUR_TB / BLUR_BANDS: full frame at 1080 width, blur bars top/bottom only (no side bars)
                    print(f"  [VIDEO] no_crop_blur_tb=1", flush=True)
                    print(f"  [VIDEO] blur_bands_fg=scale=1080:-2", flush=True)
                    print(f"  [VIDEO] blur_bands_bg=cover_crop_blur", flush=True)
                    vbase = "[0:v]setpts=PTS-STARTPTS,split[fg_in][bg_in];[bg_in]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=30:1,eq=brightness=-0.10:saturation=0.88[bg];[fg_in]scale=1080:-2:flags=lanczos[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2[vbase]"
                if DEBUG_RENDER_STAMP:
                    stamp_text = f"PIPELINE_ACTIVE:VIREEL:{FIT_MODE}:{base_name}".replace("'", "'\\''")
                    vbase += f";[vbase]drawtext=text='{stamp_text}':fontsize=56:x=20:y=20:box=1:boxcolor=black@0.4[vbase]"
                if burn:
                    ass_rel = f"captions_{i}.ass"
                    ass_esc = ass_rel.replace("\\", "\\\\").replace("'", "\\'")
                    filter_complex = vbase + f";[vbase]ass='{ass_esc}'[vout]"
                    print(f"  [SUBS] applied_on=post_composite node=vbase", flush=True)
                else:
                    filter_complex = vbase + ";[vbase]null[vout]"
                print(f"  [FFMPEG] filter_complex={filter_complex}", flush=True)
                if DEBUG_RENDER_STAMP and "PIPELINE_ACTIVE" not in filter_complex:
                    print("[ERROR] DEBUG_RENDER_STAMP=1 but PIPELINE_ACTIVE missing from filter_complex", file=sys.stderr)
                    sys.exit(1)
                clip_dur = get_video_duration(clip_mp4)
                af = []
                if AUDIO_NORMALIZE:
                    af.append(f"loudnorm=I={TARGET_I}:LRA=11:TP=-1.5")
                if clip_dur > 0.24:
                    af.append(f"afade=t=in:st=0:d=0.12,afade=t=out:st={clip_dur - 0.12}:d=0.12")
                af_str = ",".join(af) if af else None
                final_cmd = ["ffmpeg", "-y", "-i", clip_mp4, "-filter_complex", filter_complex, "-map", "[vout]", "-map", "0:a?"]
                if af_str:
                    final_cmd.extend(["-af", af_str])
                if use_nvenc:
                    final_cmd.extend(["-c:v", "h264_nvenc", "-preset", NVENC_PRESET, "-cq", "19"])
                else:
                    final_cmd.extend(["-c:v", "libx264", "-crf", "23"])
                final_cmd.extend(["-c:a", "aac", "-b:a", "160k", str(short_mp4)])
                run(final_cmd, f"Vertical + subs clip_{i}")
                # Verify output resolution 1080x1920
                w, h = get_video_resolution(short_mp4)
                print(f"  [OUT] res={w}x{h}", flush=True)
                if w != 1080 or h != 1920:
                    print(f"[ERROR] Output resolution {w}x{h} is not 1080x1920", file=sys.stderr)
                    sys.exit(1)

            # 1) Render using initial_action
            do_render(burn_subs)

            out_abs = Path(short_mp4).resolve()
            if not out_abs.exists():
                print(f"  [ERROR] FFmpeg finished but MP4 NOT FOUND at: {out_abs}", file=sys.stderr)
                sys.exit(1)
            if out_abs.stat().st_size < MIN_OUTPUT_SIZE_MB * 1024 * 1024:
                print(f"  [ERROR] Output too small (< {MIN_OUTPUT_SIZE_MB}MB): {out_abs}", file=sys.stderr)
                sys.exit(1)
            print(f"  MP4 CREATED: {out_abs}")

            # 2) Verify output: fast OpenCV check on rendered clip (subtitle band)
            caption_present = verify_caption_present_rendered(short_mp4)

            # 3) Fallback: if we skipped but no captions detected, rerender with burn (avoid no-subs)
            fallback = False
            if initial_action == "skip" and not caption_present:
                fallback = True
                final_action = "burn"
                if not placement or placement.get("mode") == "skipped":
                    placement = compute_safe_subtitle_y(clip_mp4)
                sub_margin_v = CAPTION_MARGIN_V
                if placement and placement.get("mode") in ("bottom", "shifted"):
                    sub_margin_v = OUT_H - placement["y"] - placement["band_h"] + 100
                sub_margin_v = max(CAPTION_MARGIN_V, min(800, sub_margin_v))
                pass_audio = segment_audio_bonuses if segment_audio_bonuses else None
                write_ass(captions_ass, segs_rel, margin_v=sub_margin_v, use_karaoke=True, segment_audio_bonuses=pass_audio)
                print(f"  [SUBS] fallback: initial skip but verify=false; rerendering with burn (margin_v={sub_margin_v})", flush=True)
                do_render(True)
            else:
                final_action = initial_action

            print(f"  [SUBS] baked_conf={score:.2f} initial={initial_action} verify={str(caption_present).lower()} final={final_action} fallback={str(fallback).lower()}", flush=True)
            print(f"[CLIP_SUBS] file={base_name} status={status} score={score:.2f} burn={final_action == 'burn'} initial_action={initial_action} verify={caption_present} fallback={fallback} final_action={final_action}", flush=True)

            run_state.setdefault("clips_rendered", []).append(i)
            summary.append((start, end, short_mp4, status, final_action == "burn"))

            # Observability: .subs.json and clips_subs for job.meta.json
            telemetry_path = CLIPS_DIR / f"{base_name}.subs.json"
            subs_entry = {
                "file": base_name,
                "initial_action": initial_action,
                "confidence": round(score, 2),
                "verify": caption_present,
                "fallback": fallback,
                "final_action": final_action,
            }
            try:
                telem = {
                    "clip": base_name,
                    "baked_detection": {
                        "status": sub_res["status"],
                        "score": sub_res["score"],
                        "frames_total": sub_res["frames_total"],
                        "frames_baked_votes": sub_res["frames_baked_votes"],
                        "avg_bottom": sub_res["avg_bottom"],
                        "avg_mid": sub_res["avg_mid"],
                        "avg_delta": sub_res["avg_delta"],
                        "avg_textlike": sub_res["avg_textlike"],
                        "avg_whiteness": sub_res["avg_whiteness"],
                    },
                    "subtitle_action": "burned" if final_action == "burn" else "skipped",
                    "initial_action": initial_action,
                    "confidence": round(score, 2),
                    "verify": caption_present,
                    "fallback": fallback,
                    "final_action": final_action,
                    "version": "baked_det_v3",
                }
                if placement:
                    telem["placement"] = {k: v for k, v in placement.items()}
                telemetry_path.write_text(json.dumps(telem, indent=2), encoding="utf-8")
            except Exception:
                pass
            clips_subs_entries.append(subs_entry)

            try:
                os.remove(clip_mp4)
                os.remove(captions_srt)
                if os.path.isfile(captions_ass):
                    os.remove(captions_ass)
            except OSError:
                pass

        if job_id and out_dir_env and clips_subs_entries:
            try:
                meta_path = OUT_DIR / "job.meta.json"
                meta = {}
                if meta_path.exists():
                    try:
                        meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    except Exception:
                        pass
                meta["clips_subs"] = clips_subs_entries
                meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
            except OSError:
                pass

        run_state_path.write_text(json.dumps(run_state), encoding="utf-8")
        _progress("finalize", 98, "Finalizing")

        # 5) Summary
        print("\n[5/6] Summary")
        for i, row in enumerate(summary, 1):
            start, end, path = row[0], row[1], row[2]
            print(f"    Clip {i}: {srt_time(start)} --> {srt_time(end)}  ->  {path}")

        # SUCCESS SUMMARY (Part A)
        glob_pat = f"{clip_prefix}short_*.mp4" if clip_prefix else "short_*.mp4"
        existing = sorted(CLIPS_DIR.glob(glob_pat), key=lambda p: p.name)
        existing = [p for p in existing if p.exists() and p.stat().st_size >= MIN_OUTPUT_SIZE_MB * 1024 * 1024]
        print("\n" + "=" * 60)
        if existing:
            print("SUCCESS SUMMARY — Final MP4 outputs:")
            for p in existing:
                print(f"  {p.resolve()}")
        else:
            print("[ERROR] No final mp4 outputs created.")
        print("=" * 60)
        print(f"\nDONE. Output folder: {CLIPS_DIR.resolve()}")

        # Cleanup: only delete files that explicitly match a whitelist of temp patterns (in clips dir only).
        ALLOWED_CLEANUP_EXTENSIONS = (".tmp",)
        ALLOWED_CLEANUP_PREFIXES = ("tmp_",)
        try:
            for f in Path(CLIPS_DIR).iterdir():
                if not f.is_file():
                    continue
                if f.suffix.lower() in ALLOWED_CLEANUP_EXTENSIONS:
                    f.unlink()
                elif any(f.name.startswith(p) for p in ALLOWED_CLEANUP_PREFIXES):
                    f.unlink()
        except Exception:
            pass

        _progress("done", 100, "Done")
        if summary and CLIPS_DIR.is_dir() and not os.environ.get("CLIP_URL"):
            _post_run_open_outputs(CLIPS_DIR)

    except SystemExit:
        raise
    except Exception as e:
        print(f"\n[ERROR] Unexpected error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
