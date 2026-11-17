from __future__ import annotations

import gc
import json
import logging
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from pykefcontrol import Event, EventType, FirmwareVersion, SpeakerModel, paths
from urllib3.exceptions import NewConnectionError

from kef_app.config import AppConfig
from kef_app.controller import KefPowerController
from kef_app.controller.network_timeout import temporary_socket_timeout
from kef_app.devices.speaker_backend import SpeakerConnector, W2Backend


class SpeakerBackendTests(unittest.TestCase):
    def test_real_client_selects_transport_and_signs_secure_writes(self):
        for secure in (False, True):
            with self.subTest(secure=secure):
                sent = []

                def request(_pool, method, url, **kwargs):
                    sent.append((method, url, kwargs))
                    if url.startswith("https:") and not secure:
                        raise NewConnectionError(None, "old firmware")
                    path = parse_qs(urlsplit(url).query).get("path", [""])[0]
                    if method == "GET":
                        values = {
                            paths.VOLUME: {"type": "i32_", "i32_": 23},
                            paths.PHYSICAL_SOURCE: {"type": "kefPhysicalSource", "kefPhysicalSource": "optical"},
                            paths.SPEAKER_STATUS: {"type": "kefSpeakerStatus", "kefSpeakerStatus": "powerOn"},
                        }
                        data = [values.get(path, {"type": "string_", "string_": "speaker"})]
                    else:
                        data = None
                    return SimpleNamespace(status=200, data=json.dumps(data).encode())

                with patch("urllib3.PoolManager.request", new=request):
                    speaker = SpeakerConnector("192.168.1.10", 0.4)
                    self.assertEqual(speaker.volume, 23)
                    self.assertEqual(speaker.source, "optical")
                    self.assertEqual(speaker.status, "powerOn")
                    speaker.source = "wifi"
                    speaker.volume = 24
                    speaker.shutdown()
                    writes = [(url, kw) for method, url, kw in sent if method == "POST"
                              and json.loads(kw["body"]).get("path") != "webserver:addUser"]
                    self.assertEqual(len(writes), 3)
                    self.assertTrue(all(url.startswith("https:" if secure else "http:") for url, _ in writes))
                    self.assertTrue(all(("Authorization" in kw["headers"]) == secure for _, kw in writes))
                    self.assertTrue(all(kw["timeout"].total == (15.0 if secure else 0.4) for _, kw in writes))
                    self.assertEqual(json.loads(writes[-1][1]["body"])["value"]["kefPhysicalSource"], "standby")
                    del speaker

    def test_identity_uses_readable_firmware_and_normalizes_all_supported_models(self):
        backend = W2Backend(logging.getLogger("tests.backend"))
        for model, label in [(SpeakerModel.LS50_WIRELESS_II, "LS50 Wireless II"),
                             (SpeakerModel.LSX_II, "LSX II"),
                             (SpeakerModel.LS60_WIRELESS, "LS60 Wireless")]:
            connector = Mock()
            connector.client.get_device_info.return_value = SimpleNamespace(
                name=None, mac_address="aa:bb:cc:dd:ee:ff", model=model,
                firmware=FirmwareVersion.parse("3.1.141"), firmware_version=None, release_text="fallback")
            identity = backend.capture_identity(connector, "192.168.1.10")
            self.assertEqual(identity.speaker_model, label)
            self.assertEqual(identity.mac, "AABBCCDDEEFF")
            self.assertEqual(identity.firmware_version, "3.1.141.0")
            self.assertEqual(identity.speaker_name, "")

    def test_event_subscription_initial_read_changes_and_reset(self):
        with patch("kef_app.devices.speaker_backend.KefClient") as client:
            client.return_value.get_source.return_value = "wifi"
            client.return_value.get_volume.return_value = 20
            client.return_value.get_status.return_value = "powerOn"
            queue = client.return_value.event_queue.return_value
            queue.queue_id = None
            speaker = SpeakerConnector("192.168.1.10", 2)
            self.assertEqual(speaker.poll_speaker(7), {"source": "wifi", "volume": 20, "speaker_status": "powerOn"})
            queue.subscribe.assert_called_once()
            queue.queue_id = "queue"
            queue.poll.return_value = [Event(paths.VOLUME, EventType.UPDATE, 0, True),
                                       Event(paths.SPEAKER_STATUS, EventType.UPDATE, "standby", True),
                                       Event(paths.PHYSICAL_SOURCE, EventType.UPDATE)]
            self.assertEqual(speaker.poll_speaker(7), {"volume": 0, "speaker_status": "standby"})
            queue.poll.assert_called_once_with(7)
            speaker.reset_event_subscription()
            queue.reset.assert_called_once_with()
            del speaker
            gc.collect()
            client.return_value.close.assert_called_once_with()

    def test_cached_client_keeps_the_current_scope_timeout(self):
        controller = KefPowerController(AppConfig(), logging.getLogger("tests.backend"))
        with patch("kef_app.devices.speaker_backend.KefClient"):
            first = controller.get_speaker()
            self.assertIs(first, controller.get_speaker())
            with temporary_socket_timeout(0.2):
                short = controller.get_speaker()
                self.assertEqual(short.request_timeout, 0.2)
                self.assertIs(short, controller.get_speaker())
            self.assertIsNot(short, controller.get_speaker())
            controller.reset_speaker()


if __name__ == "__main__":
    unittest.main()
