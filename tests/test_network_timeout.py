from __future__ import annotations

import unittest
from unittest.mock import patch

import requests

from kef_app.controller.network_timeout import temporary_socket_timeout
from kef_app.devices.speaker_http import speaker_requests


class NetworkTimeoutTests(unittest.TestCase):
    def test_speaker_requests_use_scoped_timeout_without_global_patch(self):
        calls: list[tuple[str, str, dict[str, object]]] = []

        class FakeSession:
            def get(self, url, params=None, **kwargs):
                calls.append(("get", url, dict(kwargs)))
                return "get-ok"

            def post(self, url, data=None, json=None, **kwargs):
                calls.append(("post", url, dict(kwargs)))
                return "post-ok"

        with patch(
            "kef_app.devices.speaker_http._speaker_session",
            return_value=FakeSession(),
        ):
            with temporary_socket_timeout(1.25):
                self.assertEqual(speaker_requests.get("http://speaker/api/getData"), "get-ok")
                self.assertEqual(speaker_requests.post("http://speaker/api/setData", json={"x": 1}), "post-ok")
        self.assertIsNot(requests.get, speaker_requests.get)

        self.assertEqual(
            calls,
            [
                ("get", "http://speaker/api/getData", {"timeout": 1.25}),
                ("post", "http://speaker/api/setData", {"timeout": 1.25}),
            ],
        )


if __name__ == "__main__":
    unittest.main()
