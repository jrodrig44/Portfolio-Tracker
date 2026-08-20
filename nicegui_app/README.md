# NiceGUI read-only mirror

Start alongside Streamlit with:

```powershell
.\.venv\Scripts\python.exe nicegui_app\main.py
```

NiceGUI listens on `0.0.0.0:8502`, so it is available at
`http://localhost:8502` and `http://<current-machine-lan-ip>:8502`. Streamlit
continues to use port 8501.

The app opens both existing SQLite databases with URI `mode=ro`. It never calls
the production schema preparation, snapshot, statement import, save, migration,
research, or worker functions. The overview uses the same stored/enriched
holding fields and formulas as the Streamlit calculation path; it does not make
Yahoo requests during page interaction.

The mirror includes Holdings, Portfolio Analysis, AI Analysis, Portfolio
Insights, Portfolio Health, and History. Portfolio and history databases load
once per browser session. Holdings renders immediately; Analysis, AI controls,
Insights, Health, and History render when first visited. AI is never invoked
until Analyze is clicked. Export payloads are generated only when a download is
requested. Changing `1M`, `3M`, `1Y`, or `ALL` updates only the history chart.

Use `Start NiceGUI Portfolio Tracker.bat` for normal startup. The launcher keeps
Python attached, rejects duplicate port-8502 launches, and records its PID for a
server watchdog so closing the launcher also stops NiceGUI. No service,
scheduled task, startup entry, or automatic restart is installed.
