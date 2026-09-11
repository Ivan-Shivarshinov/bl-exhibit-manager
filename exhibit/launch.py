"""Start the local server independently of a terminal or Codex session."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen
import webbrowser

WORKSPACE = Path(__file__).resolve().parent.parent


def healthy(port):
    try:
        with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as response:
            return json.load(response).get("application") == "bl-exhibit-manager"
    except (URLError, OSError, ValueError):
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Port must be between 1024 and 65535")
    if not (WORKSPACE / "web/dist/index.html").is_file():
        raise SystemExit("Build the interface first: npm ci --prefix web && npm run build --prefix web")
    if not healthy(args.port):
        options = {"start_new_session": True} if os.name != "nt" else {
            "creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "exhibit.app:app", "--host", "127.0.0.1",
             "--port", str(args.port), "--no-access-log", "--log-level", "critical"],
            cwd=WORKSPACE, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, close_fds=True, **options)
        for _ in range(40):
            if healthy(args.port):
                break
            if process.poll() is not None:
                raise SystemExit("Server could not start. Port may be occupied. Run scripts/start.ps1 for diagnostics.")
            time.sleep(.25)
        else:
            process.terminate()
            raise SystemExit("Server did not become ready. Run the server in a terminal for diagnostics.")
        print(f"Server PID: {process.pid}. It remains running after this launcher exits.")
    url = f"http://127.0.0.1:{args.port}"
    print(url)
    if not args.no_browser:
        webbrowser.open(url)


if __name__ == "__main__":
    main()
