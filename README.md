# Dividend Portfolio Tracker

Dividend Portfolio Tracker is a self-contained local NiceGUI application for managing, analyzing, and exporting a dividend-focused investment portfolio. NiceGUI is the production interface; the retained Streamlit application is a temporary rollback option.

## Core Features

- Spreadsheet-style holdings management backed by local SQLite storage.
- Yahoo Finance prices, dividend-yield estimates, and optional supplemental investment intelligence.
- Server-maintained market values and live portfolio history during market sessions, even with no browser connected.
- Cost basis, unrealized gain/loss, yield on cost, portfolio yield, allocation, concentration, and weighted calculations.
- Regular, special, and HD ESPP contribution tracking.
- Growth projections, income estimates, milestones, dividend timing, insights, and methodology-based health views.
- Optional, explicitly triggered 1min.AI analysis and Supplemental Research.
- Manual/read-only exports: **Holdings CSV**, **Portfolio report** (Markdown), and **Structured data** (JSON).
- Mobile-friendly layouts and a browser UI refresh that keeps connected pages visually current.

## Architecture

```text
NiceGUI server
├── Browser/UI sessions
│   └── 20-second UI refresh while connected
├── Market Refresh Worker
│   ├── NYSE calendar check every 60 seconds
│   ├── Yahoo price refresh every ~5 minutes while open
│   └── one final post-close refresh
└── Supplemental Research Worker
    └── due AI research checks approximately every 6 hours

Shared services/data
├── Yahoo/yfinance in-process cache
├── portfolio calculations
├── data/portfolio.db
└── data/stash_statement_history.sqlite
```

The in-process Market Refresh worker, connected browser refreshes, and explicit shared-data preparation use a process-wide market-refresh lock. This coordinates overlapping Yahoo, calculation, and live-snapshot work without introducing another database or distributed lock.

## Persistent Market Refresh

The server-level Market Refresh worker starts and stops with NiceGUI. It does not use a browser timer or require any connected clients: after the server is ready, the Portfolio Tracker webpage may be closed while market data and the current live-history point continue to advance. The PowerShell/server process must remain running; shutdown, sleep, or stopping NiceGUI stops background work.

The worker:

- checks market state every 60 seconds;
- refreshes Yahoo prices approximately every 5 minutes during regular NYSE sessions;
- uses the local `exchange-calendars` `XNYS` calendar for sessions, weekends, exchange holidays, early closes, Eastern/UTC conversion, and daylight-saving changes;
- performs one final Yahoo refresh approximately 2 minutes after the actual regular-session close;
- avoids repeated Yahoo polling overnight, on weekends, and on exchange holidays; and
- logs temporary Yahoo/database failures and retries on a later check without terminating NiceGUI.

No market-hours web API or new market-data provider is used. Yahoo/yfinance remains the market-data source. The worker reuses `current_market_data`, `ReadOnlyRefreshCoordinator`, the existing in-process Yahoo cache, portfolio calculations, and live-snapshot persistence. Refreshed prices feed current prices, market values, total value, unrealized gain/loss, allocation percentages, concentration and weighted calculations, and the current History/Performance live point.

The final closing refresh invalidates only cached price entries. Longer-lived dividend/fundamental metadata, investment intelligence, historical context, and fund-sector-weight caches retain their configured TTL behavior.

The two refresh mechanisms have separate responsibilities:

- **Server Market Refresh worker:** maintains Yahoo-backed portfolio calculations and today's live snapshot with zero browser clients.
- **NiceGUI browser refresh:** every 20 seconds while that client is connected, detects changed state, invalidates dependent views, and rerenders the active section. It stops for that client on disconnect.

The market worker does not render expensive tabs or automatically trigger AI Analysis, Supplemental Research, Yahoo Investment Intelligence enrichment, fund-sector look-through downloads, historical downloads, or ticker charts. Those remain controlled by explicit actions and their existing caches.

## Application Tabs

1. **Holdings** — add, edit, remove, and save holdings; configure dividends, contributions, ESPP, asset type, and sector; download **Holdings CSV**, **Portfolio report**, or **Structured data**.
2. **Portfolio Analysis** — review summaries, allocation, income, yield, growth projections, the contribution-based Snowball Tracker and milestones, and available dividend timing/seasonality analysis.
3. **AI Analysis** — explicitly run optional 1min.AI analysis, select its model and analysis type, view saved/generated output, and use its separate **Download Markdown** control.
4. **Portfolio Insights** — compare holdings, yields, contribution allocations, concentration, and prioritized observations with cached supplemental market context.
5. **Portfolio Health** — review tracker-methodology health scores, alerts, goals, and filtered views. Scores are heuristics, not objective investment ratings.
6. **History** — select **1M**, **3M**, **1Y**, or **ALL** and view imported statement history together with the current live portfolio point.
7. **Statement History** — explicitly scan local PDFs, inspect diagnostics and monthly continuity, resolve missing months through manual review, import ready statements, clear scan previews, or delete imported history. Original PDFs are never moved or deleted; deleting imported history leaves live snapshots intact.
8. **Supplemental Research** — manage its model, enrollment, per-holding research, archived results, and worker status.
9. **System** — view operational, cache, database, statement-folder, and AI configuration status without displaying credentials. **Prepare Shared Data** performs an explicit Yahoo/portfolio refresh, updates the live snapshot, verifies/checkpoints the shared databases, and prepares a safe interface handoff.

## Portfolio History

History combines the long-term imported brokerage statement baseline with `live_portfolio_snapshots`. The live table stores one row per calendar date. Repeated market refreshes update today's row rather than creating a five-minute or intraday tick series. After the final post-close refresh, the row effectively represents that session's closing portfolio value. Imported statement records and their existing union with live snapshots remain unchanged.

## AI Analysis, Supplemental Research, and Exports

### Built-in AI Analysis

AI Analysis is optional and runs only after **Analyze Portfolio** is selected. It sends compact portfolio-analysis context already loaded in memory to 1min.AI; it does not upload an exported file. Model selection alone does not make an API request. Its **Download Markdown** output is the saved/generated AI result and is separate from the comprehensive **Portfolio report** Markdown export.

Create or update the project-root `.env` with generic local values:

```text
ONEMIN_AI_API_KEY=your_key_here
ONEMIN_AI_MODEL=gpt-5.6-sol
ONEMIN_AI_BASE_URL=https://api.1min.ai
ONEMIN_AI_TIMEOUT_SECONDS=180
```

Restart after changing `.env`. The repository ignores `.env`; never commit real credentials or place them in `.env.example`.

### Supplemental Research

Supplemental Research has its own dedicated tab. Yahoo remains the primary source for prices, dividends, fundamentals, classifications, market values, cost basis, income, projections, and portfolio calculations. Research supplies missing descriptive/contextual information and never overwrites Yahoo or user-entered financial values.

The tab provides:

- **Supplemental Research Model** and **Save Research Model**;
- per-holding status, enrollment, last success, next refresh, model used, verified facts, and applicable errors;
- **Enroll & Research** for holdings not enrolled and **Refresh Research** for enrolled holdings;
- expandable **Existing Research**, **Archived Research**, and **Background Worker Status**.

The Supplemental Research worker is a child process, separate from the in-process Market Refresh worker. It checks for due research approximately every six hours, uses persisted leases/jobs to prevent overlaps, and operates without a browser. AI credits are used only when eligible research actually runs. It can be exercised once without sleeping:

```text
python.exe supplemental_research_worker.py --once
```

### Manual/Read-only Exports

The Holdings workflow provides **Holdings CSV**, **Portfolio report** (Markdown), and **Structured data** (JSON). These exports are manual, do not require a 1min.AI API key, and do not trigger AI Analysis. A saved export can be uploaded manually to another service; no external analysis occurs automatically.

## Running the Application

Production startup chain:

```text
Start Portfolio Tracker.bat -> run_nicegui.ps1 -> nicegui_app/main.py -> http://localhost:8501
```

### Recommended: Double-click launcher

On Windows, double-click [Start Portfolio Tracker.bat](Start%20Portfolio%20Tracker.bat).

The launcher:

1. Resolves the project directory and runs `run_nicegui.ps1` without detaching the server.
2. Finds a usable Python 3 installation and creates or repairs `.venv` when needed.
3. Installs dependencies whenever `.venv` is new or `requirements.txt` changes, using a local requirements fingerprint otherwise. This includes `exchange-calendars>=4.5`; normal launcher use does not require installing it separately.
4. Refuses to replace another listener on production port 8501.
5. Starts NiceGUI, checks `http://localhost:8501` readiness, and starts one Supplemental Research child plus one in-process Market Refresh worker through the NiceGUI lifecycle.

A usable Python 3 installation must exist on a new machine before the launcher can build `.venv`. Keep the PowerShell window/server process running. Browser auto-open is disabled, and the browser may remain closed after readiness while both background systems continue. Press Ctrl+C for graceful shutdown; protected writes drain before an exact-process-tree forced stop is used only if the 15-second grace period expires.

PowerShell alternative:

```powershell
.\run_nicegui.ps1
```

Manual startup remains available with `pip install -r requirements.txt` followed by `python nicegui_app\main.py`.

## Shared Data and Temporary Streamlit Rollback

NiceGUI and Streamlit use the same authoritative local databases:

- `data/portfolio.db`
- `data/stash_statement_history.sqlite`

There is no database copying or import/export synchronization. The databases, `.env`, NiceGUI user storage, logs, statements, and other runtime files remain local and Git-ignored.

Before switching interfaces, use **System → Prepare Shared Data**, wait for **Shared data is ready**, stop the active server gracefully, and then start the other launcher. Streamlit rollback uses `http://localhost:8502`; production NiceGUI uses `http://localhost:8501`. Never run the handoff as a database copy.

## Local Logs

Structured application logs use JSONL, switch to new date-and-process-stamped files at local midnight, retain 14 calendar days, and redact sensitive fields. The configured filename patterns are:

- `logs/streamlit.YYYY-MM-DD.PID.jsonl` for the temporary Streamlit rollback lifecycle and its Yahoo/cache events.
- `logs/supplemental_research_worker.YYYY-MM-DD.PID.jsonl` for independent research-worker checks, leases, outcomes, and sleep/shutdown events.

The in-process Market Refresh worker uses the shared `portfolio_tracker` logger and appears with NiceGUI/server logging; it does not create a separate `market_refresh_worker.jsonl` file. Logs remain local and Git-ignored.

```powershell
$workerLog = Get-ChildItem ".\logs\supplemental_research_worker.*.jsonl" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
Get-Content $workerLog.FullName -Wait
$streamlitLog = Get-ChildItem ".\logs\streamlit.*.jsonl" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
Get-Content $streamlitLog.FullName -Wait
```

## Notes

- Yahoo price and supplemental-data lookups require internet access and may be incomplete for some holdings.
- A legacy `data/holdings.csv` is migrated once when detected.
- Never commit credentials, account identifiers, statements, or other private portfolio runtime data.
