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
        with patch.dict("os.environ", {"PORTFOLIO_NETWORK_DIAGNOSTICS": "0"}), patch("network_diagnostics.configure_structured_logging") as configure, patch("network_diagnostics.logging.getLogger") as get_logger:
            app = Mock()
            original_attributes = vars(app).copy()
            diagnostic("test")
            install_client_diagnostics(app)
            install_client_diagnostics(app)
            with network_operation("test"):
                pass
            configure.assert_not_called()
            get_logger.assert_not_called()
            self.assertEqual(app.mock_calls, [])
            self.assertEqual(vars(app), original_attributes)

    def test_enabled_installation_is_idempotent_and_preserves_other_filters(self):
        logger = logging.getLogger("asyncio")
        unrelated_filter = logging.Filter()
        app = Mock()
        with patch.dict("os.environ", {"PORTFOLIO_NETWORK_DIAGNOSTICS": "1"}), patch.object(logger, "filters", [unrelated_filter]), patch("network_diagnostics.diagnostic") as event:
            install_client_diagnostics(app)
            install_client_diagnostics(app)
            app.add_middleware.assert_called_once_with(ConnectionDiagnosticsMiddleware)
            for hook in (app.on_connect, app.on_disconnect, app.on_startup, app.on_shutdown):
                hook.assert_called_once()
            self.assertEqual(sum(isinstance(item, WindowsResetObserver) for item in logger.filters), 1)
            self.assertIn(unrelated_filter, logger.filters)
            client = SimpleNamespace(id="client-id")
            app.on_connect.call_args.args[0](client)
            app.on_disconnect.call_args.args[0](client)
            app.on_startup.call_args.args[0]()
            app.on_shutdown.call_args.args[0]()
            self.assertEqual([call.args[0] for call in event.call_args_list], [
                "nicegui_client_connected", "nicegui_client_disconnected", "nicegui_startup", "nicegui_shutdown",
            ])

    def test_distinct_apps_each_receive_diagnostics(self):
        logger = logging.getLogger("asyncio")
        apps = [Mock(), Mock()]
        with patch.dict("os.environ", {"PORTFOLIO_NETWORK_DIAGNOSTICS": "1"}), patch.object(logger, "filters", []):
            for app in apps:
                install_client_diagnostics(app)
                install_client_diagnostics(app)
                app.add_middleware.assert_called_once_with(ConnectionDiagnosticsMiddleware)
                for hook in (app.on_connect, app.on_disconnect, app.on_startup, app.on_shutdown):
                    hook.assert_called_once()
            self.assertEqual(sum(isinstance(item, WindowsResetObserver) for item in logger.filters), 1)

    def test_setup_failure_propagates_without_completion_marker(self):
        logger = logging.getLogger("asyncio")
        for failing_step in ("add_middleware", "on_connect", "on_disconnect", "on_startup", "on_shutdown"):
            with self.subTest(failing_step=failing_step), patch.dict("os.environ", {"PORTFOLIO_NETWORK_DIAGNOSTICS": "1"}), patch.object(logger, "filters", []):
                app = Mock()
                error = RuntimeError("registration failed")
                getattr(app, failing_step).side_effect = error
                with self.assertRaises(RuntimeError) as caught:
                    install_client_diagnostics(app)
                self.assertIs(caught.exception, error)
                self.assertIsNot(vars(app).get("_portfolio_tracker_network_diagnostics_installed"), True)

    def test_partial_setup_retry_registers_each_component_successfully_once(self):
        logger = logging.getLogger("asyncio")
        steps = ("add_middleware", "on_connect", "on_disconnect", "on_startup", "on_shutdown")
        for failing_step in steps:
            with self.subTest(failing_step=failing_step), patch.dict("os.environ", {"PORTFOLIO_NETWORK_DIAGNOSTICS": "1"}), patch.object(logger, "filters", [logging.Filter()]):
                unrelated_filter = logger.filters[0]
                successful_registrations = {step: [] for step in steps}
                app = Mock()
                error = RuntimeError("registration failed")
                failure_enabled = True

                def register(step, argument):
                    if step == failing_step and failure_enabled:
                        raise error
                    successful_registrations[step].append(argument)

                for step in steps:
                    getattr(app, step).side_effect = lambda argument, step=step: register(step, argument)
                with self.assertRaises(RuntimeError) as caught:
                    install_client_diagnostics(app)
                self.assertIs(caught.exception, error)
                self.assertIsNot(vars(app).get("_portfolio_tracker_network_diagnostics_installed"), True)
                for index, step in enumerate(steps):
                    self.assertEqual(len(successful_registrations[step]), int(index < steps.index(failing_step)))

                failure_enabled = False
                install_client_diagnostics(app)
                install_client_diagnostics(app)
                for step in steps:
                    self.assertEqual(len(successful_registrations[step]), 1, step)
                    self.assertEqual(getattr(app, step).call_count, 2 if step == failing_step else 1)
                self.assertIs(successful_registrations["add_middleware"][0], ConnectionDiagnosticsMiddleware)
                self.assertIs(vars(app).get("_portfolio_tracker_network_diagnostics_installed"), True)
                self.assertIn(unrelated_filter, logger.filters)
                self.assertEqual(sum(isinstance(item, WindowsResetObserver) for item in logger.filters), 1)

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
