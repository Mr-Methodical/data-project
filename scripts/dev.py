"""Start API and Vite together; Ctrl+C stops both child processes."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    npm = shutil.which("npm")
    if not npm:
        print("Node.js 24 and npm are required. See README.md.", file=sys.stderr)
        return 1
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "backend")
    children: list[subprocess.Popen] = []
    stopping = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        children.append(subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "rinkcheck.api:app", "--reload",
             "--reload-dir", "backend", "--host", "127.0.0.1", "--port", "8000"],
            cwd=ROOT, env=env,
        ))
        children.append(subprocess.Popen(
            [npm, "run", "dev", "--", "--host", "127.0.0.1"], cwd=ROOT / "frontend",
        ))
        print("RinkCheck: http://localhost:5173 | API docs: http://localhost:8000/docs")
        while not stopping:
            if any(child.poll() is not None for child in children):
                return next((child.returncode or 1 for child in children
                             if child.poll() is not None), 1)
            time.sleep(0.25)
        return 0
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == "__main__":
    raise SystemExit(main())

