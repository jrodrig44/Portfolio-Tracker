from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path


def main() -> int:
    project_root = Path(__file__).resolve().parent
    app_path = project_root / "app.py"

    # Avoid noisy Proactor disconnect callbacks on Windows when clients drop.
    if sys.platform.startswith("win"):
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        except Exception:
            pass

    from streamlit.web import cli as stcli

    worker_path = project_root / "supplemental_research_worker.py"
    worker = subprocess.Popen([sys.executable, str(worker_path)], cwd=project_root)
    try:
        sys.argv = ["streamlit", "run", str(app_path)]
        return int(stcli.main())
    finally:
        if worker.poll() is None:
            worker.terminate()
            try:
                worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait()


if __name__ == "__main__":
    raise SystemExit(main())
