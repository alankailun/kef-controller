from __future__ import annotations

import logging
import socket
import threading
import time
import unittest
from unittest.mock import Mock, patch

from kef_app.config import AppConfig
from kef_app.controller import KefPowerController
from kef_app.controller.standby.prewarmed_socket import PrewarmedSocketHolder


class _LoopbackHttpSpeaker:
    def __init__(self, *, close_after_get: bool = False):
        self._stop = threading.Event()
        self._close_after_get = close_after_get
        self._requests: list[bytes] = []
        self._connection_count = 0
        self._lock = threading.Lock()
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(64)
        self._server.settimeout(0.05)
        self.port = self._server.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True, name="PrewarmedStandbyLoopback")

    @property
    def requests(self) -> list[bytes]:
        with self._lock:
            return list(self._requests)

    @property
    def connection_count(self) -> int:
        with self._lock:
            return self._connection_count

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        self._stop.set()
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=0.1):
                pass
        except OSError:
            pass
        self._thread.join(timeout=1.0)
        self._server.close()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _addr = self._server.accept()
            except TimeoutError:
                continue
            except OSError:
                return

            threading.Thread(
                target=self._handle_connection,
                args=(conn,),
                daemon=True,
                name="PrewarmedStandbyLoopbackClient",
            ).start()

    def _handle_connection(self, conn: socket.socket) -> None:
        with conn:
            conn.settimeout(0.10)
            counted_connection = False
            while not self._stop.is_set():
                data = self._read_request(conn)
                if not data:
                    break
                with self._lock:
                    if not counted_connection:
                        self._connection_count += 1
                        counted_connection = True
                    self._requests.append(data)
                try:
                    if data.startswith(b"GET "):
                        connection_header = b"Connection: close\r\n" if self._close_after_get else b""
                        conn.sendall(
                            b"HTTP/1.1 200 OK\r\n"
                            b"Transfer-Encoding: chunked\r\n"
                            b"Content-Type: application/json\r\n"
                            + connection_header
                            + b"\r\n2\r\n[]\r\n0\r\n\r\n"
                        )
                    else:
                        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                except OSError:
                    break
                if not data.startswith(b"GET ") or self._close_after_get:
                    break

    @staticmethod
    def _read_request(conn: socket.socket) -> bytes:
        data = bytearray()
        try:
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data.extend(chunk)
            if b"\r\n\r\n" not in data:
                return bytes(data)
            header, body = bytes(data).split(b"\r\n\r\n", 1)
            content_length = 0
            for line in header.split(b"\r\n")[1:]:
                if line.lower().startswith(b"content-length:"):
                    content_length = int(line.split(b":", 1)[1].strip())
                    break
            while len(body) < content_length:
                chunk = conn.recv(content_length - len(body))
                if not chunk:
                    break
                body += chunk
            return header + b"\r\n\r\n" + body[:content_length]
        except OSError:
            pass
        return bytes(data)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = round((len(ordered) - 1) * percentile)
    return ordered[index]


class PrewarmedStandbySocketTests(unittest.TestCase):
    def make_controller(self, port: int, **config_updates) -> KefPowerController:
        updates = {
            "kef_ip": "127.0.0.1",
            "mac_discovery_tcp_port": port,
            "prewarmed_keepalive_interval_s": 20.0,
            "prewarmed_socket_timeout_s": 0.10,
            "prewarmed_send_deadline_s": 0.10,
        }
        updates.update(config_updates)
        config = AppConfig().with_updates(**updates)
        logger = logging.getLogger(f"tests.prewarmed_standby.{self._testMethodName}")
        logger.handlers = [logging.NullHandler()]
        logger.propagate = False
        return KefPowerController(config, logger)

    def test_keepalive_then_short_connection_send_loopback_benchmark(self):
        samples_ms: list[float] = []
        runs = 20
        with _LoopbackHttpSpeaker() as speaker:
            controller = self.make_controller(speaker.port, prewarmed_persist_socket=False)
            controller._probe_prewarmed_keepalive("127.0.0.1")
            controller._record_prewarmed_keepalive_success("unit_test", "127.0.0.1", 1)

            for _ in range(runs):
                started = time.perf_counter()
                result = controller.try_send_prewarmed_standby("127.0.0.1")
                samples_ms.append((time.perf_counter() - started) * 1000.0)
                self.assertTrue(result.success, result)
                self.assertEqual(result.status, "sent")
                self.assertEqual(result.so_error, 0)

            deadline = time.monotonic() + 1.0
            expected_requests = runs + 1
            while len(speaker.requests) < expected_requests and time.monotonic() < deadline:
                time.sleep(0.01)

        p50 = _percentile(samples_ms, 0.50)
        p95 = _percentile(samples_ms, 0.95)
        max_ms = max(samples_ms)
        print(
            "PREWARMED_STANDBY_LOOPBACK_BENCHMARK "
            f"runs={runs} p50_ms={p50:.3f} p95_ms={p95:.3f} max_ms={max_ms:.3f}"
        )
        self.assertLess(p95, 100.0)
        self.assertTrue(any(request.startswith(b"GET /api/getData") for request in speaker.requests))
        self.assertGreaterEqual(
            sum(1 for request in speaker.requests if request.startswith(b"POST /api/setData")),
            runs,
        )

    def test_persistent_socket_pool_allows_lock_and_suspend_sends(self):
        with _LoopbackHttpSpeaker() as speaker:
            controller = self.make_controller(speaker.port, prewarmed_persist_socket=True)
            controller._prewarmed_standby_tick("unit_test")

            first = controller.try_send_prewarmed_standby("127.0.0.1")
            second = controller.try_send_prewarmed_standby("127.0.0.1")

            deadline = time.monotonic() + 1.0
            while len(speaker.requests) < 3 and time.monotonic() < deadline:
                time.sleep(0.01)

        self.assertTrue(first.success, first)
        self.assertTrue(second.success, second)
        self.assertEqual(first.status, "sent")
        self.assertEqual(second.status, "sent")
        self.assertEqual(first.mode, "persistent_socket")
        self.assertEqual(second.mode, "persistent_socket")
        self.assertTrue(any(request.startswith(b"GET /api/getData") for request in speaker.requests))
        self.assertGreaterEqual(
            sum(1 for request in speaker.requests if request.startswith(b"POST /api/setData")),
            2,
        )

    def test_persistent_keepalive_reuses_both_pool_connections_without_reconnecting(self):
        with _LoopbackHttpSpeaker() as speaker:
            controller = self.make_controller(speaker.port, prewarmed_persist_socket=True)

            controller._prewarmed_standby_tick("unit_test")
            controller._prewarmed_standby_tick("unit_test")
            controller._prewarmed_standby_tick("unit_test")

            deadline = time.monotonic() + 1.0
            while len(speaker.requests) < 3 and time.monotonic() < deadline:
                time.sleep(0.01)
            with controller._prewarmed.lock:
                holders = list(controller._prewarmed.holders)

            self.assertEqual(len(holders), 2)
            self.assertTrue(all(holder.sock is not None for holder in holders))
            self.assertEqual(speaker.connection_count, 2)
            self.assertEqual(
                sum(1 for request in speaker.requests if request.startswith(b"GET /api/getData")),
                3,
            )
            self.assertTrue(
                all(b"Connection: keep-alive\r\n" in request for request in speaker.requests[:3])
            )
            controller._close_prewarmed_socket_holders()

    def test_persistent_keepalive_replaces_only_connection_closed_by_server(self):
        with _LoopbackHttpSpeaker(close_after_get=True) as speaker:
            controller = self.make_controller(speaker.port, prewarmed_persist_socket=True)
            controller._log_structured = Mock()

            delay = controller._prewarmed_standby_tick("unit_test")

            with controller._prewarmed.lock:
                holders = list(controller._prewarmed.holders)
            self.assertEqual(delay, 20.0)
            self.assertEqual(len(holders), 2)
            self.assertTrue(all(holder.sock is not None for holder in holders))
            self.assertTrue(
                any(
                    call.kwargs.get("status") == "replaced_failed_connection"
                    and "Connection: close" in call.kwargs.get("error", "")
                    for call in controller._log_structured.mock_calls
                )
            )
            controller._close_prewarmed_socket_holders()

    def test_monitor_stop_keeps_holder_for_pending_suspend_worker(self):
        controller = self.make_controller(80, prewarmed_persist_socket=True)
        controller._log_structured = Mock()
        sock = Mock()
        holder = PrewarmedSocketHolder(sock, "127.0.0.1")
        with controller._prewarmed.lock:
            controller._prewarmed.holders = [holder]
            controller._prewarmed.running = True
        controller._prewarmed.stop.set()

        controller._run_prewarmed_standby_socket_monitor("unit_test")

        with controller._prewarmed.lock:
            holders = list(controller._prewarmed.holders)
        self.assertEqual(holders, [holder])
        sock.close.assert_not_called()

        taken = controller._take_prewarmed_socket_holder("127.0.0.1")

        self.assertIs(taken, sock)
        with controller._prewarmed.lock:
            self.assertEqual(controller._prewarmed.holders, [])

    def test_monitor_start_clears_stale_holders_before_rebuilding_pool(self):
        controller = self.make_controller(80, prewarmed_persist_socket=True)
        stale_sock = Mock()
        stale_holder = PrewarmedSocketHolder(stale_sock, "127.0.0.1")
        with controller._prewarmed.lock:
            controller._prewarmed.holders = [stale_holder]
        thread = Mock()

        with patch("kef_app.controller.standby.prewarmed_socket.threading.Thread", return_value=thread):
            started = controller.start_prewarmed_standby_socket_monitor("resume")

        self.assertTrue(started)
        stale_sock.close.assert_called_once()
        thread.start.assert_called_once()
        with controller._prewarmed.lock:
            self.assertEqual(controller._prewarmed.holders, [])
            self.assertIs(controller._prewarmed.thread, thread)

    def test_monitor_start_resets_keepalive_failure_backoff(self):
        controller = self.make_controller(80, prewarmed_persist_socket=True)
        with controller._prewarmed.lock:
            controller._prewarmed.failures = 100
        thread = Mock()

        with patch("kef_app.controller.standby.prewarmed_socket.threading.Thread", return_value=thread):
            started = controller.start_prewarmed_standby_socket_monitor("resume")

        self.assertTrue(started)
        with controller._prewarmed.lock:
            self.assertEqual(controller._prewarmed.failures, 0)

    def test_keepalive_failures_back_off_until_success_resets_counter(self):
        controller = self.make_controller(80, prewarmed_persist_socket=False, prewarmed_keepalive_interval_s=5.0)
        controller._log_structured = Mock()
        controller._probe_prewarmed_keepalive = Mock(side_effect=OSError(10065, "host unreachable"))

        delays = [controller._prewarmed_standby_tick("unit_test") for _ in range(7)]

        self.assertEqual(delays[:2], [5.0, 5.0])
        self.assertEqual(delays[2:5], [30.0, 30.0, 30.0])
        self.assertEqual(delays[5:], [60.0, 60.0])
        with controller._prewarmed.lock:
            self.assertEqual(controller._prewarmed.failures, 7)

        controller._probe_prewarmed_keepalive = Mock()
        delay = controller._prewarmed_standby_tick("unit_test")

        self.assertEqual(delay, 5.0)
        with controller._prewarmed.lock:
            self.assertEqual(controller._prewarmed.failures, 0)

    def test_health_snapshot_reports_latest_heartbeat_and_retry_error(self):
        controller = self.make_controller(80, prewarmed_persist_socket=True)
        with controller._prewarmed.lock:
            controller._prewarmed.last_ok_mono = controller.mono() - 2.0
            controller._prewarmed.failures = 1
            controller._prewarmed.last_error = "TimeoutError('timed out')"

        health = controller.get_prewarmed_standby_health()

        self.assertGreaterEqual(health["last_heartbeat_age_s"], 2.0)
        self.assertEqual(health["failures"], 1)
        self.assertEqual(health["last_error"], "TimeoutError('timed out')")

    def test_keepalive_failure_does_not_discard_other_hot_connection(self):
        controller = self.make_controller(80, prewarmed_persist_socket=True)
        healthy_socket = Mock()
        healthy_holder = PrewarmedSocketHolder(healthy_socket, "127.0.0.1")
        with controller._prewarmed.lock:
            controller._prewarmed.holders = [healthy_holder]

        delay = controller._record_prewarmed_keepalive_failure(
            "unit_test",
            "127.0.0.1",
            TimeoutError("timed out"),
        )

        self.assertEqual(delay, 20.0)
        with controller._prewarmed.lock:
            self.assertEqual(controller._prewarmed.holders, [healthy_holder])
            self.assertEqual(controller._prewarmed.failures, 1)
        healthy_socket.close.assert_not_called()

    def test_keepalive_failure_logs_only_the_first_and_every_third_failure_at_info(self):
        controller = self.make_controller(80, prewarmed_persist_socket=True)
        controller.log.setLevel(logging.DEBUG)

        class CaptureHandler(logging.Handler):
            def __init__(self) -> None:
                super().__init__()
                self.records: list[logging.LogRecord] = []

            def emit(self, record: logging.LogRecord) -> None:
                self.records.append(record)

        capture = CaptureHandler()
        controller.log.addHandler(capture)
        self.addCleanup(controller.log.removeHandler, capture)

        for _ in range(6):
            controller._record_prewarmed_keepalive_failure("unit_test", "127.0.0.1", TimeoutError("timed out"))

        records = [
            record
            for record in capture.records
            if record.getMessage().startswith("STEP action=PREWARMED_STANDBY_SOCKET")
        ]
        self.assertEqual(
            [record.levelno for record in records],
            [logging.INFO, logging.DEBUG, logging.INFO, logging.DEBUG, logging.DEBUG, logging.INFO],
        )

    def test_cached_prewarmed_send_uses_snapshot_bytes_and_matching_socket(self):
        with _LoopbackHttpSpeaker() as speaker:
            controller = self.make_controller(speaker.port, prewarmed_persist_socket=True)
            controller._prewarmed_standby_tick("unit_test")

            result = controller.try_send_cached_prewarmed_standby()
            controller._close_prewarmed_socket_holders()

            deadline = time.monotonic() + 1.0
            while len(speaker.requests) < 2 and time.monotonic() < deadline:
                time.sleep(0.01)

        self.assertTrue(result.success, result)
        self.assertEqual(result.status, "sent")
        self.assertEqual(result.target_ip, "127.0.0.1")
        self.assertEqual(result.mode, "persistent_socket")
        self.assertIsNotNone(result.cache_version)
        self.assertTrue(any(request.startswith(b"GET /api/getData") for request in speaker.requests))
        self.assertTrue(any(b"Host: 127.0.0.1\r\n" in request for request in speaker.requests))
        self.assertGreaterEqual(
            sum(1 for request in speaker.requests if request.startswith(b"POST /api/setData")),
            1,
        )

    def test_cached_prewarmed_send_tries_second_pool_socket_after_first_send_failure(self):
        controller = self.make_controller(80, prewarmed_persist_socket=True)
        first_socket = Mock()
        first_socket.sendall.side_effect = OSError("stale pooled socket")
        second_socket = Mock()
        second_socket.getsockopt.return_value = 0
        with controller._prewarmed.lock:
            controller._prewarmed.holders = [
                PrewarmedSocketHolder(first_socket, "127.0.0.1"),
                PrewarmedSocketHolder(second_socket, "127.0.0.1"),
            ]

        with patch("kef_app.controller.standby.prewarmed_socket.select.select", return_value=([], [], [])):
            result = controller.try_send_cached_prewarmed_standby()

        self.assertTrue(result.success, result)
        first_socket.sendall.assert_called_once()
        second_socket.sendall.assert_called_once()
        first_socket.close.assert_called_once()
        second_socket.close.assert_called_once()

    def test_cached_prewarmed_send_rejects_a_readable_stale_socket_before_send(self):
        controller = self.make_controller(80, prewarmed_persist_socket=True)
        first_socket = Mock()
        second_socket = Mock()
        second_socket.getsockopt.return_value = 0
        with controller._prewarmed.lock:
            controller._prewarmed.holders = [
                PrewarmedSocketHolder(first_socket, "127.0.0.1"),
                PrewarmedSocketHolder(second_socket, "127.0.0.1"),
            ]

        with patch(
            "kef_app.controller.standby.prewarmed_socket.select.select",
            side_effect=[([first_socket], [], []), ([], [], [])],
        ):
            result = controller.try_send_cached_prewarmed_standby()

        self.assertTrue(result.success, result)
        first_socket.sendall.assert_not_called()
        first_socket.close.assert_called_once()
        second_socket.sendall.assert_called_once()

    def test_cached_prewarmed_send_skips_when_socket_ip_does_not_match_cache(self):
        with _LoopbackHttpSpeaker() as speaker:
            controller = self.make_controller(speaker.port, prewarmed_persist_socket=True)
            controller._prewarmed_standby_tick("unit_test")
            controller._fast_standby_send_cache.update(
                target_ip="127.0.0.2",
                target_mac="",
                updated_mono=controller.mono(),
            )

            result = controller.try_send_cached_prewarmed_standby()

        self.assertFalse(result.success)
        self.assertEqual(result.fast_path_skip_reason, "no_socket_for_cached_ip")
        self.assertEqual(result.target_ip, "127.0.0.2")

    def test_fast_standby_controller_loopback_benchmark(self):
        samples_ms: list[float] = []
        runs = 20
        with _LoopbackHttpSpeaker() as speaker:
            controller = self.make_controller(
                speaker.port,
                prewarmed_standby_enabled=False,
                suspend_fast_standby_enabled=True,
            )

            for _ in range(runs):
                generation = controller._new_generation("sleep", "PBT_APMSUSPEND")
                started = time.perf_counter()
                result = controller.standby_kef_fast_suspend(
                    generation,
                    "PBT_APMSUSPEND",
                    deadline_mono=controller.mono() + 1.0,
                )
                samples_ms.append((time.perf_counter() - started) * 1000.0)
                self.assertTrue(result)

            deadline = time.monotonic() + 1.0
            while len(speaker.requests) < runs and time.monotonic() < deadline:
                time.sleep(0.01)

        p50 = _percentile(samples_ms, 0.50)
        p95 = _percentile(samples_ms, 0.95)
        max_ms = max(samples_ms)
        print(
            "FAST_STANDBY_LOOPBACK_BENCHMARK "
            f"runs={runs} p50_ms={p50:.3f} p95_ms={p95:.3f} max_ms={max_ms:.3f}"
        )
        self.assertLess(p95, 100.0)
        self.assertGreaterEqual(
            sum(1 for request in speaker.requests if request.startswith(b"POST /api/setData")),
            runs,
        )


if __name__ == "__main__":
    unittest.main()
