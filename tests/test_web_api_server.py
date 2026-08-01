from __future__ import annotations

import threading
import time
import tempfile
import unittest
from http.client import HTTPConnection
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urljoin, urlparse
from urllib.request import Request, urlopen

from kef_app.ui.web_api_server import WebApiServer, _STATIC_CONTENT_TYPES


class WebApiServerTests(unittest.TestCase):
    def test_static_web_asset_types_do_not_depend_on_windows_registry_associations(self) -> None:
        self.assertEqual(_STATIC_CONTENT_TYPES[".html"], "text/html; charset=utf-8")
        self.assertEqual(_STATIC_CONTENT_TYPES[".css"], "text/css; charset=utf-8")
        self.assertEqual(_STATIC_CONTENT_TYPES[".js"], "text/javascript; charset=utf-8")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "styles.css").write_text("body {}", encoding="utf-8")
            (root / "texts.js").write_text("const text = {};", encoding="utf-8")
            server = WebApiServer(root, object())
            server.start()
            try:
                self.assertTrue(server.origin.endswith("/"))
                self.assertNotIn("token=", server.origin)
                with urlopen(urljoin(server.url, "styles.css")) as response:
                    self.assertEqual(response.headers.get_content_type(), "text/css")
                with urlopen(urljoin(server.url, "texts.js")) as response:
                    self.assertEqual(response.headers.get_content_type(), "text/javascript")
            finally:
                server.stop()

    def test_api_requires_the_per_process_token_and_limits_request_size(self) -> None:
        bridge = type("Bridge", (), {"invoke_api": lambda _self, method, args: {"method": method, "args": args}})()
        server = WebApiServer(Path("."), bridge)
        server.start()
        try:
            base = server.url.split("?", 1)[0]
            token = parse_qs(urlparse(server.url).query)["token"][0]
            self.assertEqual(server.origin, base)
            self.assertNotIn(token, server.origin)
            request = Request(
                f"{base}api/refresh?token={token}",
                data=b'{"args":[]}',
                headers={"Content-Type": "application/json"},
            )
            with urlopen(request) as response:
                self.assertEqual(response.status, 200)

            with self.assertRaises(HTTPError) as denied:
                urlopen(Request(f"{base}api/refresh", data=b'{}'))
            self.assertEqual(denied.exception.code, 403)
            denied.exception.close()

            # Test the advertised request size without streaming an oversized
            # body.  On Windows, a client still uploading that body can race
            # the server's immediate 413 response and receive a connection
            # abort instead of the HTTP status we are verifying.
            parsed = urlparse(server.url)
            connection = HTTPConnection(parsed.hostname, parsed.port)
            try:
                connection.putrequest("POST", f"/api/refresh?token={token}")
                connection.putheader("Content-Type", "application/json")
                connection.putheader("Content-Length", str((1 << 20) + 1))
                connection.endheaders()
                response = connection.getresponse()
                self.assertEqual(response.status, 413)
                response.read()
            finally:
                connection.close()
        finally:
            server.stop()

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
