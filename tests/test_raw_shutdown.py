from __future__ import annotations

import json
import unittest

from kef_app.controller.actions.raw_shutdown import build_standby_request_bytes


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


if __name__ == "__main__":
    unittest.main()
