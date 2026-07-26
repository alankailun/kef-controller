from __future__ import annotations

import threading
import time
import unittest
from pathlib import Path

from kef_app.ui.web_api_server import WebApiServer


class WebApiServerTests(unittest.TestCase):
    def test_bootstrap_uses_current_state_without_replaying_old_events(self) -> None:
        bridge = type("Bridge", (), {"invoke_api": lambda _self, method, args: {"method": method, "args": args}})()
        server = WebApiServer(Path("."), bridge)
        server.publish("state", '{"old": true}')
        server.publish("log", "old log line")

        result = server._bootstrap()

        self.assertEqual(result["cursor"], 2)
        self.assertEqual(result["state"], {"method": "initialState", "args": []})
        self.assertEqual(server._updates_since(int(result["cursor"]), timeout_s=0.0)["updates"], [])

    def test_updates_returns_immediately_when_an_event_is_already_available(self) -> None:
        server = WebApiServer(Path("."), object())
        server.publish("state", "{}")

        result = server._updates_since(0, timeout_s=0.0)

        self.assertEqual(result["cursor"], 1)
        self.assertEqual(result["updates"], [{"id": 1, "channel": "state", "payload": "{}"}])

    def test_updates_waits_until_a_new_event_arrives(self) -> None:
        server = WebApiServer(Path("."), object())
        result: dict[str, object] = {}
        finished = threading.Event()

        def wait_for_update() -> None:
            result.update(server._updates_since(0, timeout_s=1.0))
            finished.set()

        waiter = threading.Thread(target=wait_for_update)
        waiter.start()
        time.sleep(0.03)
        self.assertFalse(finished.is_set())

        server.publish("toast", '{"level":"info"}')
        waiter.join(timeout=1.0)

        self.assertTrue(finished.is_set())
        self.assertEqual(result["cursor"], 1)
        self.assertEqual(result["updates"][0]["channel"], "toast")


if __name__ == "__main__":
    unittest.main()
