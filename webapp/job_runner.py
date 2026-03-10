"""
Runs clip.py in this process and writes progress to job dir.
Used by the web server so the server never attaches to clip.py's pipes
(avoids deadlock: only this process reads stdout/stderr).
"""
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = REPO_ROOT


def main():
    if len(sys.argv) < 2:
        sys.exit(2)
    job_id = sys.argv[1]
    job_dir = Path(os.environ.get("OUTPUTS_DIR_JOB", str(REPO_ROOT / "outputs" / "jobs" / job_id)))
    url = os.environ.get("CLIP_URL", "")
    max_clips = os.environ.get("CLIP_MAX_CLIPS")
    clip_seconds = os.environ.get("CLIP_SECONDS")
    job_dir.mkdir(parents=True, exist_ok=True)
    output_log = job_dir / "output.log"
    progress_file = job_dir / "progress.json"
    done_file = job_dir / "done.json"

    py = sys.executable
    clip_py = PROJECT_ROOT / "clip.py"
    args = [py, str(clip_py), "--url", url]
    if max_clips:
        args.extend(["--max-clips", max_clips])
    if clip_seconds:
        args.extend(["--clip-seconds", clip_seconds])

    proc = subprocess.Popen(
        args,
        cwd=str(PROJECT_ROOT),
        env=os.environ.copy(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    log_lines = []
    max_log = 500
    progress = {"stage": "download", "progress": 0, "message": "Starting..."}
    lock = threading.Lock()

    def write_progress():
        try:
            progress_file.write_text(json.dumps(progress, indent=2), encoding="utf-8")
        except OSError:
            pass

    def process_line(line):
        nonlocal progress
        line = (line or "").rstrip()
        with lock:
            log_lines.append(line)
            if len(log_lines) > max_log:
                log_lines.pop(0)
            try:
                output_log.write_text("\n".join(log_lines[-max_log:]), encoding="utf-8")
            except OSError:
                pass
        m = re.match(r"\[PROGRESS\]\s+stage=(\S+)\s+pct=(\d+)\s+msg=(.*)", line.strip())
        if m:
            with lock:
                progress = {"stage": m.group(1), "progress": int(m.group(2)), "message": m.group(3)}
            write_progress()

    def drain(stream, label):
        try:
            for line in stream:
                process_line(line)
        except Exception:
            pass
        finally:
            try:
                stream.close()
            except Exception:
                pass

    t1 = threading.Thread(target=drain, args=(proc.stdout, "stdout"), daemon=True)
    t2 = threading.Thread(target=drain, args=(proc.stderr, "stderr"), daemon=True)
    t1.start()
    t2.start()
    proc.wait()
    t1.join(timeout=5)
    t2.join(timeout=5)
    code = proc.returncode
    try:
        done_file.write_text(json.dumps({"exit_code": code}), encoding="utf-8")
    except OSError:
        pass
    sys.exit(0 if code is not None else 1)


if __name__ == "__main__":
    main()
