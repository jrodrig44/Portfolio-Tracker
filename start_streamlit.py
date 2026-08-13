from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

from logging_setup import configure_structured_logging, log_event


LOGGER = configure_structured_logging(
    "portfolio_tracker",
    Path(__file__).resolve().parent / "logs" / "streamlit.jsonl",
    "streamlit",
)


def main() -> int:
    project_root = Path(__file__).resolve().parent
    app_path = project_root / "app.py"
    log_event(LOGGER, "launcher_started", "Portfolio Tracker launcher started")

    # Avoid noisy Proactor disconnect callbacks on Windows when clients drop.
    if sys.platform.startswith("win"):
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        except Exception:
            pass

    from streamlit.web import cli as stcli

    worker_path = project_root / "supplemental_research_worker.py"
    worker = subprocess.Popen([sys.executable, str(worker_path)], cwd=project_root)
    log_event(LOGGER, "worker_process_started", "Supplemental research worker process started")
    try:
        sys.argv = ["streamlit", "run", str(app_path)]
        log_event(LOGGER, "streamlit_starting", "Streamlit starting")
        return int(stcli.main())
    finally:
        log_event(LOGGER, "streamlit_stopped", "Streamlit stopped")
        if worker.poll() is None:
            log_event(LOGGER, "worker_termination_requested", "Worker termination requested")
            worker.terminate()
            try:
                worker.wait(timeout=10)
                log_event(LOGGER, "worker_terminated", "Worker terminated")
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait()
                log_event(LOGGER, "worker_killed_after_timeout", "Worker killed after shutdown timeout")
        log_event(LOGGER, "launcher_stopped", "Portfolio Tracker launcher stopped")


if __name__ == "__main__":
    raise SystemExit(main())
