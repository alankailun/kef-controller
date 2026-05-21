from __future__ import annotations

import threading
import unittest

from kef_app.controller.fast_standby_cache import FastStandbySendCache


class FastStandbySendCacheTests(unittest.TestCase):
    def test_update_swaps_complete_immutable_snapshot(self):
        cache = FastStandbySendCache()

        first = cache.update(target_ip="10.0.0.222", target_mac="84171517AC77", updated_mono=10.0)
        second = cache.update(target_ip="10.0.0.223", target_mac="84171517AC78", updated_mono=11.0)

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(first.target_ip, "10.0.0.222")
        self.assertIn(b"Host: 10.0.0.222\r\n", first.standby_request_bytes)
        self.assertEqual(second.target_ip, "10.0.0.223")
        self.assertIn(b"Host: 10.0.0.223\r\n", second.standby_request_bytes)
        self.assertEqual(second.version, first.version + 1)
        self.assertIs(cache.read(), second)

    def test_clear_when_target_ip_is_empty(self):
        cache = FastStandbySendCache()
        cache.update(target_ip="10.0.0.222", target_mac="84171517AC77", updated_mono=10.0)

        snapshot = cache.update(target_ip="", target_mac="", updated_mono=11.0)

        self.assertIsNone(snapshot)
        self.assertIsNone(cache.read())

    def test_concurrent_reads_observe_complete_snapshots(self):
        cache = FastStandbySendCache()
        seen: list[tuple[str, bytes, int]] = []
        stop = threading.Event()

        def reader() -> None:
            while not stop.is_set():
                snapshot = cache.read()
                if snapshot is not None:
                    seen.append((snapshot.target_ip, snapshot.standby_request_bytes, snapshot.version))

        readers = [threading.Thread(target=reader) for _ in range(8)]
        for thread in readers:
            thread.start()
        try:
            for index in range(100):
                ip = f"10.0.0.{index % 250 + 1}"
                cache.update(target_ip=ip, target_mac=f"{index:012X}", updated_mono=float(index))
        finally:
            stop.set()
            for thread in readers:
                thread.join(timeout=1.0)

        self.assertTrue(seen)
        for ip, request, version in seen:
            self.assertGreaterEqual(version, 1)
            self.assertIn(f"Host: {ip}\r\n".encode("ascii"), request)


if __name__ == "__main__":
    unittest.main()
