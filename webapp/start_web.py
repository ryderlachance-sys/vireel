"""
Start the clipper web UI server. Finds a free port (8011..8025), starts uvicorn, opens browser.
Windows-only; run from project root via webapp\\run_web.bat.
"""

import os
import socket
import sys
import traceback
import webbrowser
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _check_deps():
    try:
        import fastapi
        import uvicorn
    except ImportError as e:
        print("Missing dependency:", e, file=sys.stderr)
        print("Run: pip install -r webapp\\requirements_web.txt", file=sys.stderr)
        sys.exit(1)


def _free_port(start=8011, end=8025):
    for port in range(start, end + 1):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    return None


def main():
    _check_deps()
    port = _free_port()
    if port is None:
        print("No free port in 8011..8025. Close another app or change range.", file=sys.stderr)
        sys.exit(1)

    url = f"http://127.0.0.1:{port}"
    print("WEB UI:", url)

    try:
        os.chdir(PROJECT_ROOT)
        webbrowser.open(url)
    except Exception:
        pass

    try:
        import uvicorn
        uvicorn.run(
            "webapp.server:app",
            host="127.0.0.1",
            port=port,
            reload=False,
            log_level="info",
        )
    except Exception:
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
