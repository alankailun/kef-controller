from __future__ import annotations

import unittest
from unittest.mock import patch

import requests

from kef_app.controller.network_timeout import temporary_socket_timeout


class NetworkTimeoutTests(unittest.TestCase):
    def test_module_get_post_use_pooled_session_and_scoped_timeout(self):
        calls: list[tuple[str, str, dict[str, object]]] = []

        class FakeSession:
            def get(self, url, params=None, **kwargs):
                calls.append(("get", url, dict(kwargs)))
                return "get-ok"

            def post(self, url, data=None, json=None, **kwargs):
                calls.append(("post", url, dict(kwargs)))
                return "post-ok"

        with patch(
            "kef_app.controller.network_timeout._get_pooled_requests_session",
            return_value=FakeSession(),
        ):
            with temporary_socket_timeout(1.25):
                self.assertEqual(requests.get("http://speaker/api/getData"), "get-ok")
                self.assertEqual(requests.post("http://speaker/api/setData", json={"x": 1}), "post-ok")

        self.assertEqual(
            calls,
            [
                ("get", "http://speaker/api/getData", {"timeout": 1.25}),
                ("post", "http://speaker/api/setData", {"timeout": 1.25}),
            ],
        )


if __name__ == "__main__":
    unittest.main()
