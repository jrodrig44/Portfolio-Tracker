from __future__ import annotations

import atexit
import subprocess
import sys
import threading
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKER_PATH = PROJECT_ROOT / "supplemental_research_worker.py"
_LOCK = threading.Lock()
_worker: subprocess.Popen[bytes] | None = None


def start_research_worker() -> None:
    """Start exactly one Supplemental Research child owned by this server.

    A child process isolates long-running AI work from NiceGUI's UI/event loop.
    This is not the in-process Yahoo Market Refresh worker; persisted leases and
    enrollment/due state provide coordination beyond this local process handle.
    """
    global _worker
    with _LOCK:
        if _worker is not None and _worker.poll() is None:
            return
        _worker = subprocess.Popen([sys.executable, str(WORKER_PATH)], cwd=PROJECT_ROOT)


def stop_research_worker() -> None:
    """Stop the tracked worker child without targeting unrelated processes."""
    global _worker
    with _LOCK:
        worker = _worker
        _worker = None
    if worker is None or worker.poll() is not None:
        return
    worker.terminate()
    try:
        worker.wait(timeout=10)
    except subprocess.TimeoutExpired:
        worker.kill()
        worker.wait()


atexit.register(stop_research_worker)
