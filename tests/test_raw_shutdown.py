from __future__ import annotations

import json
import socket
import threading
import time
import unittest

from kef_app.controller.actions.raw_shutdown import build_standby_request_bytes, fire_and_forget_standby


class _LoopbackTcpSink:
    def __init__(self):
        self._stop = threading.Event()
        self._accepted = 0
        self._lock = threading.Lock()
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(64)
        self._server.settimeout(0.05)
        self.port = self._server.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True, name="RawShutdownLoopbackSink")

    @property
    def accepted(self) -> int:
        with self._lock:
            return self._accepted

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
                conn.settimeout(0.05)
                try:
                    while conn.recv(4096):
                        pass
                except OSError:
                    pass
            with self._lock:
                self._accepted += 1


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = round((len(ordered) - 1) * percentile)
    return ordered[index]


class RawShutdownTests(unittest.TestCase):
    def test_standby_request_bytes_are_prebuilt_post_to_set_data(self):
        request = build_standby_request_bytes("10.0.0.222")
        header_blob, body = request.split(b"\r\n\r\n", 1)
        headers = header_blob.decode("ascii")
        payload = json.loads(body.decode("ascii"))

        self.assertTrue(headers.startswith("POST /api/setData HTTP/1.1\r\n"))
        self.assertIn("Host: 10.0.0.222", headers)
        self.assertIn(f"Content-Length: {len(body)}", headers)
        self.assertIn("Connection: close", headers)
        self.assertEqual(payload["path"], "settings:/kef/play/physicalSource")
        self.assertEqual(payload["roles"], "value")
        self.assertEqual(payload["value"]["kefPhysicalSource"], "standby")

    def test_fire_and_forget_loopback_benchmark(self):
        samples_ms: list[float] = []
        runs = 30
        with _LoopbackTcpSink() as sink:
            for _ in range(runs):
                started = time.perf_counter()
                result = fire_and_forget_standby(
                    "127.0.0.1",
                    port=sink.port,
                    attempts=3,
                    socket_timeout=0.05,
                    join_timeout=0.05,
                )
                samples_ms.append((time.perf_counter() - started) * 1000.0)
                self.assertTrue(result.success, result)

            deadline = time.monotonic() + 1.0
            while sink.accepted < runs and time.monotonic() < deadline:
                time.sleep(0.01)

        p50 = _percentile(samples_ms, 0.50)
        p95 = _percentile(samples_ms, 0.95)
        max_ms = max(samples_ms)
        print(
            "RAW_SHUTDOWN_LOOPBACK_BENCHMARK "
            f"runs={runs} p50_ms={p50:.3f} p95_ms={p95:.3f} max_ms={max_ms:.3f}"
        )
        self.assertLess(p95, 100.0)


if __name__ == "__main__":
    unittest.main()
