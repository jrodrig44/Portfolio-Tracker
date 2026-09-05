import asyncio
import json
import logging
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

from logging_setup import StructuredJsonFormatter
from network_diagnostics import ConnectionDiagnosticsMiddleware, WindowsResetObserver, diagnostic, install_client_diagnostics, network_operation


class NetworkDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    def test_cleanup_observer_records_endpoints_but_keeps_original_error(self):
        error = ConnectionResetError("private")
        error.winerror = 10054
        transport = Mock()
        transport.get_extra_info.side_effect = lambda key: {"peername": ("127.0.0.1", 123), "sockname": ("127.0.0.1", 8501)}[key]
        frame = SimpleNamespace(f_globals={"__name__": "asyncio.proactor_events"},
                                f_code=SimpleNamespace(co_name="_call_connection_lost"),
                                f_locals={"self": transport})
        traceback = SimpleNamespace(tb_frame=frame, tb_next=None)
        record = logging.LogRecord("asyncio", logging.ERROR, "", 1, "original", (), (type(error), error, traceback))
        with patch("network_diagnostics.diagnostic") as event:
            self.assertTrue(WindowsResetObserver().filter(record))
            self.assertEqual(event.call_args.kwargs["local"], ("127.0.0.1", 8501))
            self.assertNotIn("private", str(event.call_args_list))
        self.assertIs(record.exc_info[1], error)
        self.assertEqual(record.getMessage(), "original")
        frame.f_globals["__name__"] = "another_client"
        with patch("network_diagnostics.diagnostic") as event:
            self.assertTrue(WindowsResetObserver().filter(record))
            event.assert_not_called()

    def test_observer_inspection_failure_keeps_original_error(self):
        error = ConnectionResetError()
        error.winerror = 10054
        record = logging.LogRecord("asyncio", logging.ERROR, "", 1, "original", (), (type(error), error, object()))
        self.assertTrue(WindowsResetObserver().filter(record))

    def test_disabled_diagnostics_do_not_configure_logging_or_hooks(self):
        with patch.dict("os.environ", {"PORTFOLIO_NETWORK_DIAGNOSTICS": "0"}), patch("network_diagnostics.configure_structured_logging") as configure:
            app = Mock()
            diagnostic("test")
            install_client_diagnostics(app)
            with network_operation("test"):
                pass
            configure.assert_not_called()
            self.assertEqual(app.mock_calls, [])

    def test_network_failure_is_reraised_without_private_exception_text(self):
        error = ConnectionResetError("private request payload")
        with patch.dict("os.environ", {"PORTFOLIO_NETWORK_DIAGNOSTICS": "1"}), patch("network_diagnostics.diagnostic") as event:
            with self.assertRaises(ConnectionResetError) as caught:
                with network_operation("yahoo_prices"):
                    raise error
            self.assertIs(caught.exception, error)
            self.assertEqual(event.call_args.args[0], "network_failed")
            self.assertEqual(event.call_args.kwargs["error_type"], "ConnectionResetError")
            self.assertNotIn("private", str(event.call_args_list))

    async def test_websocket_disconnect_passes_messages_and_omits_payloads(self):
        message = {"type": "websocket.disconnect", "code": 1001}
        async def application(scope, receive, send):
            self.assertIs(await receive(), message)
        async def receive():
            return message
        scope = dict(type="websocket", client=("127.0.0.1", 12345), server=("127.0.0.1", 8501), query_string=b"secret", headers=[(b"cookie", b"private")])
        with patch("network_diagnostics.diagnostic") as event:
            await ConnectionDiagnosticsMiddleware(application)(scope, receive, Mock())
            self.assertEqual([call.args[0] for call in event.call_args_list], ["connection_started", "connection_disconnected", "connection_scope_completed"])
            self.assertNotIn("secret", str(event.call_args_list))
            self.assertNotIn("private", str(event.call_args_list))

    async def test_server_reset_is_not_suppressed_and_loop_handler_is_unchanged(self):
        loop = asyncio.get_running_loop()
        handler = loop.get_exception_handler()
        async def application(*args):
            raise ConnectionResetError("unrelated")
        with patch("network_diagnostics.diagnostic") as event:
            with self.assertRaises(ConnectionResetError):
                await ConnectionDiagnosticsMiddleware(application)({"type": "http"}, Mock(), Mock())
            self.assertEqual(event.call_args_list[-2].args[0], "connection_failed")
        self.assertIs(loop.get_exception_handler(), handler)

    def test_formatter_uses_record_time_pid_and_thread(self):
        record = logging.LogRecord("test", logging.INFO, "", 1, "test", (), None)
        record.created = 1700000000.123
        payload = json.loads(StructuredJsonFormatter().format(record))
        self.assertIn(".123", payload["timestamp"])
        self.assertEqual(payload["pid"], record.process)
        self.assertEqual(payload["thread"], record.threadName)

    def test_yahoo_failure_is_distinct_and_cached_result_still_reused(self):
        from nicegui_app.services import yahoo
        yahoo.clear_market_data_cache()
        self.addCleanup(yahoo.clear_market_data_cache)
        with patch.object(yahoo.yf, "download", side_effect=TimeoutError("private")) as download, patch.object(yahoo.yf, "Ticker") as ticker, patch.object(yahoo, "diagnostic") as event:
            ticker.return_value.info = {}
            first = yahoo.current_market_data(["TEST"])
            self.assertEqual(first, yahoo.current_market_data(["TEST"]))
            download.assert_called_once()
            self.assertIn("yahoo_prices_failed", [call.args[0] for call in event.call_args_list])
            self.assertNotIn("private", str(event.call_args_list))
