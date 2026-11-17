from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

from kef_app.controller.network_timeout import temporary_socket_timeout
from kef_app.devices.speaker_backend import W2Backend
from kef_app.devices.speaker_http import current_socket_timeout


class NetworkTimeoutTests(unittest.TestCase):
    def test_timeout_is_nested_thread_local_and_restored_on_error(self):
        self.assertEqual(current_socket_timeout(5), 5)
        with temporary_socket_timeout(1.25):
            with self.assertRaises(RuntimeError):
                with temporary_socket_timeout(0.2):
                    self.assertEqual(current_socket_timeout(5), 0.2)
                    raise RuntimeError("test")
            self.assertEqual(current_socket_timeout(5), 1.25)
            observed = []
            thread = threading.Thread(target=lambda: observed.append(current_socket_timeout(5)))
            thread.start()
            thread.join()
            self.assertEqual(observed, [5])
            with patch("kef_app.devices.speaker_backend.KefClient") as client:
                connector = W2Backend(None).create_connector("192.168.1.10")
                client.assert_called_once_with("192.168.1.10", timeout=1.25)
                self.assertEqual(connector.request_timeout, 1.25)
        self.assertEqual(current_socket_timeout(5), 5)


if __name__ == "__main__":
    unittest.main()
