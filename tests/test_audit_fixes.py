from __future__ import annotations

import json
import logging
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from kef_app.config import AppConfig
from kef_app.controller import KefPowerController
from kef_app.devices.transport import secure
from kef_app.devices.transport.standby_request import build_standby_request_bytes, fire_and_forget_standby
from kef_app.storage import UserConfigStore
from kef_app.ui.tray_application import KefTrayApp
from kef_app.ui.web_state import WebStateMixin


class AuditFixTests(unittest.TestCase):
    def controller(self):
        return KefPowerController(AppConfig().with_updates(kef_ip="192.168.1.10"), logging.getLogger("audit"))

    def switch(self, c):
        c.config.kef_ip = "192.168.1.20"
        c.apply_configured_device_target("test")

    def test_offline_is_not_confirmed_standby(self):
        c = self.controller()
        c._read_standby_snapshot = Mock(return_value=(None, None, "offline"))
        self.assertFalse(c._wait_for_standby_confirmation(timeout=0.2)[0])
        c._read_standby_snapshot = Mock(return_value=(False, "standby", None))
        self.assertTrue(c._wait_for_standby_confirmation(timeout=0.2)[0])

    def test_old_volume_completion_is_discarded(self):
        c = self.controller()
        c._ensure_target_identity = Mock(return_value=True)
        owner = self
        class Speaker:
            @property
            def volume(self): return 0
            @volume.setter
            def volume(self, value): owner.switch(c)
        c.get_speaker = Mock(return_value=Speaker())
        self.assertFalse(c.set_volume(65))
        self.assertIsNone(c._runtime_speaker.volume)

    def test_source_retry_does_not_use_new_target(self):
        c = self.controller()
        c._ensure_target_identity = Mock(return_value=True)
        def change(*args, **kwargs):
            self.switch(c)
            raise OSError("old target failed")
        c._set_speaker_source = Mock(side_effect=change)
        self.assertFalse(c.change_input_live("wifi"))
        self.assertEqual(c._set_speaker_source.call_count, 1)

    def test_tray_discards_old_target_and_event(self):
        c = self.controller()
        tray = SimpleNamespace(_controller=c, _target_generation=c.get_target_generation(),
                               _speaker_on_hint=True, _refresh_icon=Mock())
        old = c.get_target_generation()
        self.switch(c)
        KefTrayApp._on_identity_changed(tray, None)
        self.assertIsNone(tray._speaker_on_hint)
        KefTrayApp._on_speaker_state_changed(tray, None, None, True, old)
        self.assertIsNone(tray._speaker_on_hint)

    def test_poll_completion_only_records_health(self):
        c = self.controller()
        bridge = SimpleNamespace(_controller=c, _last_failure="old", publish_state=Mock(), _apply_speaker_state=Mock())
        WebStateMixin._on_polled_state(bridge, "wifi", 25, True, c.get_target_generation())
        bridge._apply_speaker_state.assert_not_called()
        bridge.publish_state.assert_not_called()
        self.assertGreater(bridge._last_poll_success_mono, 0)

    def test_old_backend_selection_is_ignored_and_not_saved(self):
        store = UserConfigStore(AppConfig())
        for data in ({"backend_name": "fake"}, {"device": {"backend_name": "fake"}}):
            c = store._apply_to_config(AppConfig(), data)
            self.assertEqual(c.backend_name, "w2")
            self.assertNotIn("backend_name", json.dumps(store._to_user_dict(c)))

    def test_https_preparation_signing_and_bounded_fallback(self):
        ip = "192.0.2.10"
        context = Mock()
        try:
            with patch.object(secure, "create_ssl_context", return_value=context), patch.object(secure.urllib3, "PoolManager") as pool:
                pool.return_value.request.return_value = SimpleNamespace(status=200, data=b"null")
                secure.prepare_secure_transport(ip, SimpleNamespace(is_secure=True))
                self.assertIs(secure.secure_context(ip), context)
                pool.return_value.clear.assert_called_once()
            first = build_standby_request_bytes(ip)
            second = build_standby_request_bytes(ip)
            self.assertIn(b"Authorization: HMAC_SHA256 ", first)
            self.assertNotEqual(first, second)
            self.assertIn(b'"role":"value"', first)
            with patch("kef_app.devices.transport.standby_request.fire_and_forget_http_post") as send:
                fire_and_forget_standby(ip, deadline_mono=123, should_send=lambda: False)
                self.assertEqual(send.call_args.kwargs["port"], 4430)
                self.assertIs(send.call_args.kwargs["ssl_context"], context)
                self.assertEqual(send.call_args.kwargs["deadline_mono"], 123)
        finally:
            secure._contexts.pop(ip, None)
