"""
Clear stale runtime.lock (if the PID in it is not running) then run server.py.
Use this as the entry point from run_server_loop.bat so every start recovers from crashed runs.
"""
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_LOCK = REPO_ROOT / "webapp" / "runtime.lock"


def _pid_alive(pid: int) -> bool:
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
            kernel = ctypes.windll.kernel32
            h = kernel.OpenProcess(0x1000, False, pid)
            if h:
                kernel.CloseHandle(h)
                return True
        except Exception:
            pass
    return False


def main():
    os.chdir(REPO_ROOT)
    if RUNTIME_LOCK.is_file():
        try:
            data = json.loads(RUNTIME_LOCK.read_text(encoding="utf-8"))
            other_pid = data.get("pid")
            if other_pid is not None:
                if not _pid_alive(int(other_pid)):
                    try:
                        RUNTIME_LOCK.unlink()
                    except OSError:
                        pass
        except Exception:
            try:
                RUNTIME_LOCK.unlink()
            except OSError:
                pass
    server_py = REPO_ROOT / "webapp" / "server.py"
    os.execv(sys.executable, [sys.executable, str(server_py)] + sys.argv[1:])


if __name__ == "__main__":
    main()
