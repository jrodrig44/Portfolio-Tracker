# Windows connection-reset investigation — 2026-09-05

The duplicate announcement is explained. The original WinError 10054 socket
remains unidentified: controlled disconnects did not reproduce the traceback.
Do not classify it as a benign browser disconnect, Yahoo failure, or lost research
job on the evidence currently available. No exception is suppressed or downgraded.

## Startup findings

`Start Portfolio Tracker.bat` invokes `run_nicegui.ps1` once. The script checks
the shared environment, rejects an existing listener on 8501, and uses one
`Start-Process -FilePath $venvPython` to execute `nicegui_app/main.py`.
Its HTTP readiness probe does not launch another server.

The two announcement sources are:

* NiceGUI `welcome.collect_urls()` (`nicegui/welcome.py`, installed source):
  enumerates IPv4 interfaces for host `0.0.0.0`, constructs localhost and LAN
  URLs, and prints `NiceGUI ready to go on ...`. This runs during startup,
  even before a subsequent bind failure; the announcement alone is not proof
  of a successfully listening server.
* `run_nicegui.ps1:314`: `Write-Host "NiceGUI ready to go on $productionUrl"`
  after its HTTP readiness check succeeds. This is Portfolio Tracker output.

Both messages are retained. `main.py` has one `ui.run` call, guarded by
`__name__ in {"__main__", "__mp_main__"}`; importing it normally registers
pages/hooks but does not start the server. `reload=False` and `show=False` are
explicit. No worker count or Uvicorn reload setting is passed. NiceGUI's
`ui_run.py` returns outside `MainProcess`, preventing its multiprocessing import
path from starting another server. The installed framework runs its server
singleton without a reload supervisor in this configuration.

The research start/stop and market start/stop hooks each appear once. Research
startup has a lock plus live-child check; market startup has a lock plus live-thread
check. Existing tests exercise idempotent starts. Normal HTTP page access does not
start either worker.

Database initialization is **not once per process**: research helpers deliberately
call the idempotent `initialize_database` schema routine repeatedly (including
`run_once`, service construction, and synchronization). NiceGUI portfolio/history
loaders read existing sources. Repeated schema checks are not a second server
startup. No database, worker cadence, lease, or scheduling behavior was changed.

## What the traceback establishes

In the inspected CPython 3.12 `asyncio/proactor_events.py`,
`_ProactorBasePipeTransport._call_connection_lost` calls the protocol's
`connection_lost` and then performs socket shutdown/close. A reset raised there
is a transport-cleanup callback error. The abbreviated supplied traceback omits
the failing source line, protocol, PID, and endpoint, so it cannot distinguish
an accepted HTTP/WebSocket connection from another asyncio transport.

The NiceGUI Yahoo boundary uses synchronous yfinance, and the common AI provider
uses synchronous `httpx.post`, including supplemental research calls. That is
useful architecture evidence, not retrospective proof of which socket failed.
The existing Yahoo prices loader also catches exceptions and can return incomplete
prices; lack of Yahoo errors in the supplied console is not proof of Yahoo success.
Its fallback/cache behavior has been preserved and made observable when enabled.

## Diagnostic changes

Enable before launching:

```powershell
$env:PORTFOLIO_NETWORK_DIAGNOSTICS = '1'
& .\run_nicegui.ps1
```

Unset the variable to return to ordinary logging. Diagnostics are off by default.
Daily, PID-specific files are written under
`logs/network_diagnostics.YYYY-MM-DD.PID.jsonl` using the existing retention policy.

* `network_diagnostics.py`: ASGI connection start/disconnect/completion and failure,
  cached peer/local endpoints, WebSocket disconnect codes, NiceGUI client IDs,
  startup/shutdown, and timed operation IDs. HTTP scope completion is not claimed
  to mean that a TCP keep-alive connection closed.
* Its `WindowsResetObserver` is a logging observer, not an asyncio exception handler.
  It always returns `True`, retaining the original error record and traceback.
  It adds endpoint/protocol evidence only for `ConnectionResetError` with
  `winerror == 10054` and a traceback frame whose module is
  `asyncio.proactor_events` and function is `_call_connection_lost`.
  Inspection failure still retains the original record. No event-loop handler,
  transport method, root logger, or error level is replaced.
* `logging_setup.py`: structured timestamps use the LogRecord creation time with
  millisecond precision; PID and thread name are included.
* `nicegui_app/main.py`: installs diagnostics only when enabled.
* `nicegui_app/market_refresh_worker.py`: refresh operation boundaries and worker
  start/stop events, without scheduling changes.
* `nicegui_app/services/yahoo.py`: cache hits/misses, operation failures, caught price
  exceptions, requested/valid price counts. `network_completed` means the boundary
  returned; consult `yahoo_prices_result` and `yahoo_prices_failed` for completeness.
* `onemin_ai_client.py`: times the existing provider call and records HTTP status
  and transport exception type. A completed HTTP operation is not a claim that
  provider response validation succeeded; existing validation remains intact.

No URLs, query strings, headers, cookies, request/response bodies, exception text,
holdings, or statement contents are emitted by the new diagnostics. Local endpoint
addresses, process IDs, and random connection/operation/client IDs are retained
for correlation. No additional production provider/Yahoo calls were introduced.

For a future burst, locate `windows_transport_cleanup_reset` in the affected PID's
file. Its protocol class and local/peer endpoints identify the transport much more
directly than timing alone. Match the endpoints to the ASGI connection events and
compare market/Yahoo/provider operation IDs and outcomes in all process logs.
The original console traceback remains necessary evidence. The new observer was
tested with representative traceback records; an actual production burst has not
yet exercised it.

## Runtime and reproduction evidence

The shared `.venv` could not start because its Microsoft Store Python target was
unavailable. It was left untouched. Validation used isolated managed CPython
3.12.13 and NiceGUI 3.16.0 with `requirements.txt`. The production launcher was
audited and covered by tests, but was not executed end to end with its broken
shared environment.

The first direct test launch encountered WinError 10048 (occupied port), not
10054, and shut down its hooks cleanly. Elevated inspection found PID 19040
listening at that point; it disappeared before its executable could be identified.
It was not killed or adopted. This was a real pre-existing port conflict, not
evidence that the two ready messages represent two successful server startups.

Once the port became free, one instrumented server (PID 13536) owned
`0.0.0.0:8501`. It logged one startup, one market-worker start, and one supplemental
worker startup with a normal no-due/completed/sleep cycle. Its research process
tree was 13536 → 10656 → 22740 (interpreter wrapper plus executing child).

| Requested action | Result |
| --- | --- |
| Browser open/close/reopen, normal refresh, hard refresh, multiple tabs | Not performed: browser runtime discovery returned no available browsers. |
| Host localhost HTTP access/repeated requests | HTTP 200 throughout. |
| Host `192.168.1.20` HTTP access/repeated requests | HTTP 200 throughout. |
| Direct WebSocket disconnect/reconnect approximation | Twelve successful upgrades: six per address, three normal closes and three deliberate TCP RST closes per address. All disconnected; no Proactor traceback. These are Engine.IO transport tests, not full NiceGUI browser sessions. |
| Initial WebSocket probe | Wrong unmounted endpoint returned 403; corrected to `/_nicegui_ws/socket.io/`, which returned 101. |
| Another LAN device | No connected test device available. |
| Normal market refresh without browser activity | Calendar-gated worker started, but September 5 was Saturday, so no regular NYSE refresh was due. Cadence was not altered to manufacture one. |
| Existing Yahoo adapter during page loading | 11 requested prices, 11 valid; metadata operations completed and subsequent requests hit cache. No price failure or provider call was observed. |
| Live values in a browser | Not visually verified because no browser was connected. |
| Deliberate Yahoo failure | Mocked local timeout produced `yahoo_prices_failed`; cached fallback behavior stayed unchanged. Existing resilience/live-history tests passed. |

No controlled action reproduced the reported repeated 10054 callback. Successful
transport closes and Yahoo results do not establish that no job was lost during
the original, unobserved incident. No benign-disconnect suppression is justified.

Three older supplemental worker trees (root PIDs 21400, 5180, 23316, created
August 31, September 1, and September 3) were also present; their recorded server
parent PIDs were absent. They predate this investigation. Their originating
shutdown paths and source versions could not be established, so they were not
silently terminated or attributed to this launch. Current graceful shutdown did
not reproduce that orphan condition. Existing persistent leases remain unchanged.

## Regression coverage and final state

`tests/test_network_diagnostics.py` adds eight focused tests for opt-in behavior,
payload omission, unchanged exception propagation, WebSocket disconnect forwarding,
record timing/process/thread fields, Yahoo failure/cache behavior, and precise
cleanup observation without dropping original or unrelated errors.

Final focused suite: **74 tests passed**. Final full suite:
**576 tests passed** (`python -m unittest discover -s tests`). Coverage includes
startup, both workers, Yahoo/cache, refresh, shutdown, and Streamlit compatibility.
Test transcripts are local at `logs/network-focused-tests.log` and
`.codex-network-tests.log`.

The test server exited with code 0 using the existing shutdown request watcher.
Shutdown logged once, market stop logged once, and PIDs 13536, 10656, and 22740
were absent afterward. Port 8501 had no listener. Pre-existing research processes
were left untouched. `git diff --check` passed; Git emitted only its configured
LF-to-CRLF conversion notices. No commits or pushes were made.

Remaining evidence needed: reproduce the original burst with diagnostics enabled
and a real browser, complete the tab/hard-refresh matrix, and observe a genuine
market-session refresh. The original socket's exact identity remains unresolved.
