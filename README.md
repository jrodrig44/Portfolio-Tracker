# Dividend Portfolio Tracker

Dividend Portfolio Tracker is a self-contained local Streamlit application for managing, analyzing, and exporting a dividend-focused investment portfolio. It combines holdings management, market-data enrichment, portfolio analysis, methodology-based health views, and optional AI-assisted analysis in one local workflow.

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

### Manual Markdown and JSON Exports

Manual exports are independent of built-in AI Analysis. Exporting does not require a 1min.AI API key, and the built-in AI feature does not replace, disable, or modify the export workflow. Export contents reflect the current portfolio snapshot and tracker methodology.

You can save an export and upload it manually to another AI service for a more comprehensive review. External AI analysis is not automatic.

## Running the Application

Startup chain:

```text
Start Portfolio Tracker.bat -> run.ps1 -> start_streamlit.py -> app.py
```

### Recommended: Double-click launcher

On Windows, double-click [Start Portfolio Tracker.bat](Start%20Portfolio%20Tracker.bat).

The launcher:

1. Determines its own project directory.
2. Opens PowerShell and keeps it open with `-NoExit`.
3. Runs `run.ps1` with an execution-policy bypass scoped only to that PowerShell process.
4. Changes to the project directory.
5. Creates `.venv` when needed.
6. Installs or updates dependencies from `requirements.txt`.
7. Starts the application through `start_streamlit.py`.
8. Launches `app.py` through Streamlit.

Keep the PowerShell window open while using the application. Closing it stops the local app. The execution-policy bypass does not permanently change Windows policy.

### PowerShell Alternative

From the project folder, run:

```powershell
.\run.ps1
```

### Manual Start

1. Create and activate a Python environment.
2. Install dependencies:

```powershell
pip install -r requirements.txt
```

3. Start the application:

```powershell
streamlit run app.py
```

## Notes

- Price lookups and supplemental Yahoo Finance data require internet access and may be incomplete for some holdings.
- Local holdings are stored at `data/portfolio.db`.
- A legacy `data/holdings.csv` file is migrated once when detected.

