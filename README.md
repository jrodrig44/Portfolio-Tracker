# Dividend Portfolio Tracker

Dividend Portfolio Tracker is a self-contained local NiceGUI application for managing, analyzing, and exporting a dividend-focused investment portfolio. NiceGUI is the production interface; the retained Streamlit application is a temporary rollback option.

## Core Features

- Spreadsheet-style holdings management with add, edit, and remove actions.
- Local SQLite persistence for holdings data.
- Live Yahoo Finance price updates, dividend-yield estimates, and supplemental investment intelligence where data is available.
- Manual dividend overrides, cost basis, current market value, unrealized gain/loss, yield on cost, and portfolio-yield tracking.
- Regular, special, and HD ESPP contribution tracking.
- Asset-type, direct-sector, look-through sector, holding-concentration, and income-concentration analysis.
- Portfolio growth projections, income estimates, milestones, and dividend-timing or income-seasonality views when historical timing data is available.
- Methodology-based portfolio health scores, alerts, goal tracking, and filtered health views. These scores are tracker heuristics, not objective investment ratings.
- HD ESPP-aware contribution and employer-stock concentration context.
- Manual Markdown and JSON exports.
- Optional built-in 1min.AI portfolio analysis.
- Mobile-friendly layouts and periodic market-data refreshes.

## Application Tabs

1. Holdings
2. Portfolio Analysis
3. AI Analysis
4. Portfolio Insights
5. Portfolio Health
6. History
7. Statement History
8. Supplemental Research
9. System

### Holdings

- Add, edit, and remove holdings.
- Enter shares and invested amounts.
- Configure regular, special, and ESPP contributions.
- Maintain dividend, ESPP, asset-type, and sector information.
- Save portfolio data locally.

### Portfolio Analysis

- Review the portfolio summary, allocation, income, yield, and contribution views.
- Explore growth projections, income milestones, and available dividend timing or seasonality analysis.
- Use the manual Markdown and JSON export controls.

### AI Analysis

- Run optional targeted analysis through 1min.AI.
- Uses portfolio information already loaded in memory; no export file is created or uploaded.
- Select a model and analysis type, then view or download the returned Markdown analysis.

### Portfolio Insights

- Compare holdings, yields, contribution allocations, and concentration views.
- Review charts, supplemental market or fundamental context, and prioritized portfolio observations.

### Portfolio Health

- Review methodology-based health scores and alerts.
- Track income goals and filter health views.
- Health scores are tracker-generated heuristics, not objective investment ratings.

## AI Analysis and Manual Exports

The application provides two separate, independent workflows.

### Built-in 1min.AI Analysis

Built-in AI Analysis is optional and runs only when you click **Analyze Portfolio**. It sends a compacted portfolio-analysis context already loaded in memory to 1min.AI; it does not upload an exported portfolio file. Use it for quick or targeted portfolio questions, with the model and analysis type selected in the app.

A local 1min.AI API key is required. The model dropdown focuses on lightweight models for quick, economical portfolio analysis. The model configured through `ONEMIN_AI_MODEL` remains the initial selection, including custom or legacy model identifiers, and you can choose another model directly from the dropdown. The selected model's description appears underneath the dropdown.

- GPT-4.1 Mini (`gpt-4.1-mini`): Recommended for routine portfolio analysis, balancing instruction-following, analytical quality, speed, and credit usage.
- Gemini 2.5 Flash (`gemini-2.5-flash`): Best for fast analysis of larger portfolio contexts and a second analytical perspective at relatively low credit usage.
- Claude Haiku 4.5 (`claude-haiku-4-5-20251001`): Best for careful explanations, nuanced wording, and detailed portfolio and ESPP instructions.
- GPT-4o Mini (`gpt-4o-mini`): Economical fallback for basic summaries and simple questions, though it may produce weaker or overstated portfolio conclusions.

Credit consumption varies by provider model, prompt size, and response length. Comprehensive reports can still be created through the independent manual export workflow, which does not require a 1min.AI API key.

Create or update the local project-root `.env` file:

```text
ONEMIN_AI_API_KEY=your_key_here
ONEMIN_AI_MODEL=gpt-5.6-sol
ONEMIN_AI_BASE_URL=https://api.1min.ai
ONEMIN_AI_TIMEOUT_SECONDS=180
```

Restart the application after changing `.env`. The repository ignores `.env`; never commit it or put a real key in `.env.example`.

### Supplemental Investment Research

Supplemental Investment Research is separate from Portfolio Analysis. Yahoo Finance remains the primary source for prices, dividends, fundamentals, classifications, market values, cost basis, income, projections, and portfolio calculations. 1min.AI web research fills only missing descriptive context such as fund objectives, expense ratios, property focus, geographic exposure, distribution policy, risk characteristics, and recent structural developments. It never overwrites Yahoo or user-entered financial data.

The research model selector focuses on lightweight models and does not use the temporary Portfolio Analysis selection. GPT-4.1 Mini (`gpt-4.1-mini`) is the default research model. Gemini 2.5 Flash (`gemini-2.5-flash`) is useful for fast analysis of larger contexts, Claude Haiku 4.5 (`claude-haiku-4-5-20251001`) for careful explanations and detailed instructions, and GPT-4o Mini (`gpt-4o-mini`) is the economical fallback. The selected raw model identifier is stored locally and survives browser refreshes and application restarts.

Research is never triggered by opening, refreshing, rerunning, or switching tabs, refreshing Yahoo data, or saving a holding. New holdings start as **Missing** and require an explicit **Research Selected Holding** action. The AI Analysis tab also provides **Refresh Due Holdings** for successful research older than approximately 30 days and **Force Refresh All Holdings**, which requires confirmation and may use more credits. Each result displays its status, model, retrieval dates, verified supplemental facts, sanitized HTTPS source links, AI interpretation, limitations, and unresolved fields. Failed refreshes preserve the previous successful result.

The monthly worker runs independently of browser sessions as a child of the production NiceGUI server (and of the temporary Streamlit rollback supervisor when rollback is active). Keep the active application process running; the website does not need to remain open. The worker checks approximately every six hours, uses a persisted SQLite lease to prevent overlapping jobs, and can be exercised once without sleeping:

```text
python.exe supplemental_research_worker.py --once
```

The active interface starts exactly one worker child and stops it with the application process. Credit consumption varies by provider model, prompt size, and response length. Comprehensive reports remain available through the independent manual Markdown and JSON export workflow.

### Local Logs

The application writes local JSONL logs under `logs/`. `logs/streamlit.jsonl` records Streamlit lifecycle, Yahoo cache-miss, price fallback, and explicit cache-clear events. `logs/supplemental_research_worker.jsonl` records independent worker checks, leases, research outcomes, and sleep/shutdown events. Logs rotate at local midnight and retain up to 14 daily backups. They remain local and are ignored by Git.

Selecting an AI model does not create an API request. Only explicit research actions or eligible worker jobs make research requests. To watch a log in PowerShell:

```powershell
Get-Content ".\logs\supplemental_research_worker.jsonl" -Wait
Get-Content ".\logs\streamlit.jsonl" -Wait
```

### Manual Markdown and JSON Exports

Manual exports are independent of built-in AI Analysis. Exporting does not require a 1min.AI API key, and the built-in AI feature does not replace, disable, or modify the export workflow. Export contents reflect the current portfolio snapshot and tracker methodology.

You can save an export and upload it manually to another AI service for a more comprehensive review. External AI analysis is not automatic.

## Running the Application

Production startup chain:

```text
Start Portfolio Tracker.bat -> run_nicegui.ps1 -> nicegui_app/main.py -> http://localhost:8501
```

### Recommended: Double-click launcher

On Windows, double-click [Start Portfolio Tracker.bat](Start%20Portfolio%20Tracker.bat).

The production launcher:

1. Determines its own project directory.
2. Opens PowerShell without detaching the server.
3. Runs `run_nicegui.ps1` with an execution-policy bypass scoped only to that PowerShell process.
4. Changes to the project directory.
5. Creates `.venv` when needed.
6. Installs dependencies when `.venv` is new or `requirements.txt` has changed; otherwise skips installation using a local requirements fingerprint.
7. Refuses to start if production port 8501 belongs to another listener; it never kills or silently replaces that process.
8. Starts NiceGUI and verifies readiness at `http://localhost:8501`.
9. Starts one Supplemental Research worker as a NiceGUI child process.

Keep the PowerShell window open while using the application. Press Ctrl+C for the two-stage graceful shutdown: new protected writes are rejected, admitted writes drain, and an exact-process-tree forced stop is used only if the 15-second grace period expires. The launcher intentionally does not open a browser tab automatically.

### PowerShell Alternative

From the project folder, run:

```powershell
.\run_nicegui.ps1
```

### Manual Start

1. Create and activate a Python environment.
2. Install dependencies:

```powershell
pip install -r requirements.txt
```

3. Start the production application:

```powershell
python nicegui_app\main.py
```

## Shared data and temporary Streamlit rollback

NiceGUI and Streamlit read the same authoritative local databases:

- `data/portfolio.db`
- `data/stash_statement_history.sqlite`

There is no database copying or import/export synchronization. These databases, `.env`, NiceGUI user storage, logs, statements, and other runtime files remain local and ignored by Git. Both interfaces initialize the project-root `.env` with existing environment values taking precedence (`override=False`).

Before switching from production NiceGUI to rollback:

1. Open **System** in NiceGUI.
2. Select **Prepare Shared Data** and wait for **Shared data is ready**.
3. Stop NiceGUI gracefully with Ctrl+C.
4. Run [Start Portfolio Tracker - Streamlit Rollback.bat](Start%20Portfolio%20Tracker%20-%20Streamlit%20Rollback.bat).
5. Open `http://localhost:8502` if Streamlit does not open it automatically.

To return to production:

1. Select **Prepare Shared Data** in the Streamlit sidebar and wait for success.
2. Stop Streamlit.
3. Run [Start Portfolio Tracker.bat](Start%20Portfolio%20Tracker.bat).
4. Open `http://localhost:8501`.

Never run the handoff as a database copy. The Last AI Analysis Snapshot, holdings, statement/live history, Supplemental Research records, and settings remain available through the shared databases.

## Notes

- Price lookups and supplemental Yahoo Finance data require internet access and may be incomplete for some holdings.
- Local holdings are stored at `data/portfolio.db`.
- A legacy `data/holdings.csv` file is migrated once when detected.

