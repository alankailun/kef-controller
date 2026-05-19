from __future__ import annotations

import logging
import socket
import threading
import time
import unittest

from kef_app.config import AppConfig
from kef_app.controller import KefPowerController


class _LoopbackHttpSpeaker:
    def __init__(self):
        self._stop = threading.Event()
        self._requests: list[bytes] = []
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
            except socket.timeout:
                continue
            except OSError:
                return

            with conn:
                conn.settimeout(0.10)
                data = self._read_request(conn)
                with self._lock:
                    self._requests.append(data)
                if data.startswith(b"GET "):
                    try:
                        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n[]")
                    except OSError:
                        pass

    @staticmethod
    def _read_request(conn: socket.socket) -> bytes:
        chunks: list[bytes] = []
        try:
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
                if b"\r\n\r\n" in b"".join(chunks):
                    break
        except OSError:
            pass
        return b"".join(chunks)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = round((len(ordered) - 1) * percentile)
    return ordered[index]


class PrewarmedStandbySocketTests(unittest.TestCase):
    def make_controller(self, port: int, **config_updates) -> KefPowerController:
        config = AppConfig().with_updates(
            kef_ip="127.0.0.1",
            mac_discovery_tcp_port=port,
            prewarmed_keepalive_interval_s=20.0,
            prewarmed_socket_timeout_s=0.10,
            prewarmed_send_deadline_s=0.10,
            **config_updates,
        )
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
                result = controller.standby_kef_fast_suspend(generation, "PBT_APMSUSPEND")
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
