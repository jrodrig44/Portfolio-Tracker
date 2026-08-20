# NiceGUI read-only proof of concept

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

Architecture observation: the two database reads run concurrently once per
client page load. Overview and Holdings reuse those in-memory results. Changing
`1M`, `3M`, `1Y`, or `ALL` replaces only the chart component from already-loaded
history, rather than rerunning the page script. Navigation switches client-side
tab panels and does not recompute portfolio data. Initial load can still be
affected by SQLite/OneDrive latency, but this POC adds no network pricing delay.
