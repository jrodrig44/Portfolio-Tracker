# Portfolio Tracker NiceGUI Production UI

NiceGUI is the production Portfolio Tracker interface. Normal Windows startup is:

```text
Start Portfolio Tracker.bat -> run_nicegui.ps1 -> nicegui_app/main.py
```

The server binds to `0.0.0.0:8501`, so it is available at `http://localhost:8501` and over the LAN when local firewall/network policy allows it. Browser auto-open is disabled. The launcher rejects an occupied production port, performs an HTTP readiness check, keeps the server attached, and provides graceful shutdown with a 15-second drain before exact-child-process-tree fallback.

NiceGUI owns two browser-independent background lifecycles:

- An in-process Market Refresh worker checks the local `exchange-calendars` XNYS schedule every 60 seconds, refreshes Yahoo prices approximately every 5 minutes during regular sessions, and performs one final price refresh about 2 minutes after the actual close. It handles holidays, weekends, early closes, and timezone/DST changes without a market-hours web API.
- One Supplemental Research child process checks persisted, enrolled research jobs approximately every 6 hours. SQLite leases prevent overlapping research work, and AI credits are used only when eligible work runs.

Both start with NiceGUI and stop with it. They continue with zero connected browser clients, but the PowerShell/NiceGUI server process must remain running. The connected browser's separate 20-second UI timer only keeps that page visually current.

NiceGUI and the temporary Streamlit rollback interface share the same project-root `.env` and authoritative databases:

- `data/portfolio.db`
- `data/stash_statement_history.sqlite`

No database synchronization or copy is required. Use **System → Prepare Shared Data** before stopping NiceGUI and launching `Start Portfolio Tracker - Streamlit Rollback.bat`. The rollback interface uses `http://localhost:8502`; run its **Prepare Shared Data** action before returning to production.

The normal launcher creates/repairs `.venv` and installs changed `requirements.txt` dependencies automatically. A usable Python 3 installation is still required on a new machine.

Runtime databases, `.env`, `.nicegui/`, logs, statements, process files, and user-specific state remain local and Git-ignored. Never commit real credentials or private portfolio data.
