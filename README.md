# Dividend Portfolio Tracker

This is a self-contained local Streamlit app for managing and analyzing a dividend portfolio.

## Features

- Add, edit, and remove holdings in a spreadsheet-style table.
- Auto-save holdings to local storage (`data/portfolio.db`).
- Enter `Shares` and `Amount Invested` directly.
- Auto-calculate `Avg Cost` and live `Current Value`.
- Auto-fetch dividend yield and estimate annual dividend/share.
- Optional manual override for annual dividend/share per holding.
- Optional `Asset Type` field per holding for allocation reporting.
- Mobile-optimized mode with compact layouts for phones.
- Three dedicated tabs:
  - Holdings
  - Portfolio Analysis
  - Portfolio Insights
  - Portfolio Health
  - Dividend Calendar
- Portfolio Analysis now includes:
  - Portfolio summary and projection assumptions
  - Asset allocation by type
  - Yield and income concentration analysis
  - Growth projections
  - Snowball tracker, milestones, progress, and years-to-reach
- Portfolio Insights now includes:
  - Key portfolio metrics and trend callouts
  - Holdings value and yield comparison tables
  - Asset mix and contribution-efficiency views
  - Dynamic charts for value, yield, allocation, and contribution distribution
  - Current snapshot table with priority takeaways
- Portfolio Health now includes:
  - Single health score metric
  - One-line alerts
  - Goal tracker card
  - Quick filters for focused views
- Dividend Calendar now includes:
  - Monthly projected dividend income (timing view)
  - Month-over-month and YTD cash-flow tracking
  - Best vs weakest payout month metrics
  - Top payer schedule by month
- Track key metrics:
  - Cost basis
  - Market value
  - Unrealized gain/loss
  - Annual dividend income
  - Yield on cost
  - Portfolio yield
- Refresh live prices from Yahoo Finance with cache control.

## Run

### One-command start (PowerShell)

From the project folder, run:

```powershell
.\run.ps1
```

This script will:

- Create `.venv` automatically (first run only)
- Install dependencies from `requirements.txt`
- Launch Streamlit

### Double-click start (Windows)

You can also start the app by double-clicking:

- Start Portfolio Tracker.bat

This opens PowerShell and runs run.ps1 for you.

### Manual start (optional)

1. Create and activate a Python environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Start the app:

```bash
streamlit run app.py
```

4. In the app sidebar:
  - Force refresh prices
  - Force refresh dividend yield
  - Reload holdings from disk

## 1min.AI Portfolio Analysis

AI Analysis is optional. It analyzes the current portfolio data directly in memory; it does not require exporting or uploading a portfolio file.

Create a local `.env` file in the project root and add:

```text
ONEMIN_AI_API_KEY=your_key_here
```

Optional configuration:

```text
ONEMIN_AI_MODEL=gpt-5.6-sol
ONEMIN_AI_BASE_URL=https://api.1min.ai
ONEMIN_AI_TIMEOUT_SECONDS=180
```

The default model is `gpt-5.6-sol`; `gpt-5.6-terra` and `gpt-5.6-luna` are available alternatives for balancing quality and credit consumption. Restart the Streamlit app after changing `.env`. Never commit `.env`; it is ignored by this repository. 1min.AI credit accounting is provider-specific and can vary by feature and model.

## Host On ASUSTOR NAS (AS1002T v2)

This app is ready to run as a Docker container on your NAS.

### Files added for NAS hosting

- `Dockerfile`
- `docker-compose.yml`
- `.streamlit/config.toml`

### Steps

1. Copy this project folder to your NAS shared folder.
2. Install/open **Docker Engine** or **Container Manager** on ASUSTOR.
3. Open a terminal on the NAS and go to the project folder.
4. Build and start:

```bash
docker compose up -d --build
```

5. Open the app:

```text
http://<NAS-IP>:8501
```

### Data persistence

- The SQLite DB is persisted via volume mapping `./data:/app/data`.
- Your holdings remain after container restarts/upgrades.

### Update after code changes

```bash
docker compose up -d --build
```

### Stop

```bash
docker compose down
```

## Notes

- Price lookups require internet access.
- Saved holdings are stored at `data/portfolio.db` (SQLite).
- Legacy `data/holdings.csv` is auto-migrated one time into SQLite if detected.

