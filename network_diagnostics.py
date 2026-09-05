"""Opt-in, payload-free correlation of application networking; never filters errors."""
from __future__ import annotations

from contextlib import contextmanager
import logging
import os
from pathlib import Path
import time
import threading
import uuid

from logging_setup import configure_structured_logging, log_event

_LOGGER_LOCK = threading.Lock()
_INSTALLATION_LOCK = threading.Lock()
_INSTALLATION_MARKER = "_portfolio_tracker_network_diagnostics_installed"


class WindowsResetObserver(logging.Filter):
    """Observe a precise cleanup traceback, always retaining the original record.

    CPython retains the transport in the traceback frame when shutdown raises.
    Reading its cached endpoints can identify the socket even after a peer reset.
    This does not replace the event loop handler or change transport cleanup.
    """
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if not record.exc_info:
                return True
            _, error, traceback = record.exc_info
            if not isinstance(error, ConnectionResetError) or getattr(error, "winerror", None) != 10054:
                return True
            while traceback is not None:
                frame = traceback.tb_frame
                if (frame.f_globals.get("__name__") == "asyncio.proactor_events"
                        and frame.f_code.co_name == "_call_connection_lost"):
                    transport = frame.f_locals.get("self")
                    protocol = transport.get_protocol()
                    diagnostic("windows_transport_cleanup_reset",
                               peer=transport.get_extra_info("peername"),
                               local=transport.get_extra_info("sockname"),
                               protocol=f"{type(protocol).__module__}.{type(protocol).__name__}",
                               transport_type=type(transport).__name__,
                               winerror=10054)
                    break
                traceback = traceback.tb_next
        except Exception:
            # Diagnostic inspection must never interfere with the original error.
            pass
        return True


def diagnostic(event: str, **fields: object) -> None:
    if os.environ.get("PORTFOLIO_NETWORK_DIAGNOSTICS") != "1":
        return
    with _LOGGER_LOCK:
        logger = configure_structured_logging(
            "portfolio_tracker.network_diagnostics",
            Path(__file__).resolve().parent / "logs" / "network_diagnostics.jsonl",
            "network_diagnostics",
        )
    log_event(logger, event, event, **fields)


@contextmanager
def network_operation(operation: str):
    if os.environ.get("PORTFOLIO_NETWORK_DIAGNOSTICS") != "1":
        yield
        return
    operation_id = uuid.uuid4().hex
    started = time.monotonic()
    diagnostic("network_started", operation=operation, operation_id=operation_id)
    try:
        yield
    except BaseException as exc:
        diagnostic("network_failed", operation=operation, operation_id=operation_id,
                   error_type=type(exc).__name__, duration_ms=round((time.monotonic()-started)*1000, 3))
        raise
    else:
        diagnostic("network_completed", operation=operation, operation_id=operation_id,
                   duration_ms=round((time.monotonic()-started)*1000, 3))


class ConnectionDiagnosticsMiddleware:
    """Record ASGI peer ports and WebSocket lifetime without URLs or payloads."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        connection_id = uuid.uuid4().hex
        fields = dict(connection_id=connection_id, connection_type=scope["type"],
                      peer=scope.get("client"), local=scope.get("server"))
        diagnostic("connection_started", **fields)

        async def observed_receive():
            message = await receive()
            if message["type"] in {"websocket.disconnect", "http.disconnect"}:
                diagnostic("connection_disconnected", **fields, code=message.get("code"))
            return message

        try:
            await self.app(scope, observed_receive, send)
        except BaseException as exc:
            diagnostic("connection_failed", **fields, error_type=type(exc).__name__)
            raise
        finally:
            diagnostic("connection_scope_completed", **fields)


def install_client_diagnostics(app) -> None:
    """Install opt-in diagnostics per app under a lock, resuming partial setup.

    Setup errors propagate; neither installation nor retries suppress errors.
    """
    if os.environ.get("PORTFOLIO_NETWORK_DIAGNOSTICS") != "1":
        return
    with _INSTALLATION_LOCK:
        if getattr(app, _INSTALLATION_MARKER, False) is True:
            return
        # The observer belongs to the process logger; NiceGUI registrations
        # belong to each app and must not share a process-wide completion flag.
        asyncio_logger = logging.getLogger("asyncio")
        if not any(isinstance(item, WindowsResetObserver) for item in asyncio_logger.filters):
            asyncio_logger.addFilter(WindowsResetObserver())
        registrations = (
            ("middleware", app.add_middleware, ConnectionDiagnosticsMiddleware),
            ("connect_hook", app.on_connect, lambda client: diagnostic("nicegui_client_connected", client_id=client.id)),
            ("disconnect_hook", app.on_disconnect, lambda client: diagnostic("nicegui_client_disconnected", client_id=client.id)),
            ("startup_hook", app.on_startup, lambda: diagnostic("nicegui_startup", parent_pid=os.getppid())),
            ("shutdown_hook", app.on_shutdown, lambda: diagnostic("nicegui_shutdown")),
        )
        # Successful registrations survive a later failure. Record each one so
        # retries resume without duplicating hooks or relying on framework rollback.
        for component, register, argument in registrations:
            marker = f"_portfolio_tracker_network_diagnostics_{component}_installed"
            if getattr(app, marker, False) is not True:
                register(argument)
                setattr(app, marker, True)
        # Only a fully registered app may take the completion fast path.
        setattr(app, _INSTALLATION_MARKER, True)
