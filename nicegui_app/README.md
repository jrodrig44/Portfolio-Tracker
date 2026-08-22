# Portfolio Tracker NiceGUI production UI

NiceGUI is the production Portfolio Tracker interface. Normal Windows startup is:

```text
Start Portfolio Tracker.bat -> run_nicegui.ps1 -> nicegui_app/main.py
```

The server listens on `0.0.0.0:8501` and is available locally at `http://localhost:8501`. The launcher refuses an occupied production port, performs an HTTP readiness check, keeps the server attached, and preserves the graceful 15-second shutdown followed by exact-child-process-tree fallback. Browser auto-open remains disabled.

The production process starts one Supplemental Research worker child. NiceGUI shutdown stops the worker normally; the existing SQLite lease continues to prevent duplicate research work. Opening pages or clicking **Prepare Shared Data** does not directly enqueue research.

NiceGUI and the temporary Streamlit rollback interface use the same project-root `.env` and authoritative databases:

- `data/portfolio.db`
- `data/stash_statement_history.sqlite`

No database synchronization or copy is required. Use **System → Prepare Shared Data** before stopping NiceGUI and launching `Start Portfolio Tracker - Streamlit Rollback.bat`. The rollback interface uses `http://localhost:8502`. Before returning, run **Prepare Shared Data** in Streamlit, stop it, and launch `Start Portfolio Tracker.bat` again.

Runtime databases, `.env`, `.nicegui/`, logs, statements, process files, and user-specific state remain local and ignored by Git.
