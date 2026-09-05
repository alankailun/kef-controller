from __future__ import annotations

import logging
import threading
import unittest
from unittest.mock import Mock, patch

from kef_app.config import AppConfig
from kef_app.controller import KefPowerController
from kef_app.controller.state_models import SpeakerUIPollResult
from kef_app.devices.speaker_models import SpeakerIdentity, normalize_input_source
from kef_app.ui.web_bridge import WebControllerBridge


class WebBridgeRegressionTests(unittest.TestCase):
    def controller(self):
        log = logging.getLogger(self.id())
        log.handlers = [logging.NullHandler()]
        log.propagate = False
        return KefPowerController(AppConfig().with_updates(kef_ip="192.168.1.10"), log)

    def bridge(self):
        bridge = WebControllerBridge.__new__(WebControllerBridge)
        bridge._controller = self.controller()
        bridge._config = bridge._controller.config
        bridge._config_store = Mock()
        bridge._notify = Mock()
        bridge.publish_state = Mock()
        bridge.publish_settings = Mock()
        bridge._target_sequence = 0
        bridge._target_completed = Mock()
        bridge._last_poll_success_mono = 42.0
        bridge._last_action_started = 0
        bridge._speaker_on = True
        bridge._input = "optical"
        bridge._volume = 10
        bridge._awaiting_wake_confirmation = False
        bridge._poll_lock = threading.Lock()
        bridge._polled_state = Mock()
        bridge._poll_failed = Mock()
        bridge._scan_id = ""
        bridge._scan_cancel = threading.Event()
        bridge.toast = Mock()
        bridge._encode = lambda value: value
        return bridge

    def test_invalid_mac_is_rejected_before_identity_probe_or_save(self):
        for mac in ("ZZ:ZZ:ZZ:ZZ:ZZ:ZZ", "GG:AABBCCDDEEFF"):
            with self.subTest(mac=mac):
                bridge = self.bridge()
                bridge._controller.inspect_kef_identity_at_ip = Mock()
                with patch("kef_app.ui.web_bridge.start_background_task") as task:
                    bridge.applyTarget("192.168.1.10", mac)
                result = task.call_args.args[1]()
                self.assertEqual(result.status, "invalid_mac")
                task.call_args.kwargs["on_success"](result)
                bridge._on_target_completed(bridge._target_completed.emit.call_args.args[0])
                bridge._controller.inspect_kef_identity_at_ip.assert_not_called()
                bridge._config_store.save.assert_not_called()
                self.assertEqual(bridge._notify.call_args.kwargs["code"], "target_invalid_mac")

    def test_valid_and_empty_mac_still_validate_and_save_normalized_identity(self):
        for mac in ("aa:bb:cc:dd:ee:ff", "AA-BB-CC-DD-EE-FF", ""):
            with self.subTest(mac=mac):
                bridge = self.bridge()
                bridge._controller.inspect_kef_identity_at_ip = Mock(return_value=SpeakerIdentity(
                    ip="192.168.1.10", mac="AABBCCDDEEFF", available=True,
                ))
                with patch("kef_app.ui.web_bridge.start_background_task") as task:
                    bridge.applyTarget("192.168.1.10", mac)
                result = task.call_args.args[1]()
                self.assertEqual(result.status, "verified")
                task.call_args.kwargs["on_success"](result)
                bridge._on_target_completed(bridge._target_completed.emit.call_args.args[0])
                self.assertEqual(bridge._config.kef_mac, "AABBCCDDEEFF")
                bridge._config_store.save.assert_called_once()

    def test_bridge_and_controller_agree_on_standby_confirmation(self):
        for outcome, confirmed in (
            ("sent_unconfirmed_prewarmed", False),
            ("sent_unconfirmed_fire_and_forget", False),
            ("sent_skipped_host_unreachable", False),
            ("success", True),
        ):
            with self.subTest(outcome=outcome):
                bridge = self.bridge()
                bridge._poll_speaker_state = Mock()
                bridge._controller._set_speaker_runtime_state = Mock()
                bridge._controller._emit_power_action_finished("EARLY_STANDBY", "test", outcome)
                bridge._on_power_action_finished("EARLY_STANDBY", "test", True, outcome)
                self.assertEqual(bridge._speaker_on, not confirmed)
                self.assertEqual(bridge._controller._set_speaker_runtime_state.called, confirmed)
                bridge._poll_speaker_state.assert_called_once_with(force=True)

    def test_poll_dispatch_distinguishes_read_failure_skip_and_unexpected_exception(self):
        for result in (
            SpeakerUIPollResult(status="failed"),
            SpeakerUIPollResult(),
            SpeakerUIPollResult(("wifi", 0, False), "success"),
        ):
            with self.subTest(status=result.status):
                bridge = self.bridge()
                bridge._controller.poll_external_ui_state_result = Mock(return_value=result)
                with patch("kef_app.ui.web_bridge.start_background_task") as task:
                    bridge._poll_speaker_state(force=True)
                task.call_args.kwargs["on_success"](task.call_args.args[1]())
                self.assertEqual(bridge._polled_state.emit.called, result.status == "success")
                self.assertEqual(bridge._poll_failed.emit.called, result.status == "failed")
                task.call_args.kwargs["on_error"](RuntimeError("unexpected"))
                bridge._poll_failed.emit.assert_called_with("unexpected")

    def test_failed_or_empty_poll_does_not_reset_age_or_power(self):
        bridge = self.bridge()
        bridge._on_poll_failed("offline")
        bridge._on_polled_state(None, None, None)
        self.assertEqual(bridge._last_poll_success_mono, 42.0)
        self.assertTrue(bridge._speaker_on)
        self.assertEqual(bridge._last_failure[0], "offline")
        with patch("kef_app.ui.web_bridge.time.monotonic", return_value=84.0):
            bridge._on_polled_state(None, 0, False)
        self.assertEqual(bridge._last_poll_success_mono, 84.0)

    def test_controller_reports_skipped_sleep_and_failed_target_resolution(self):
        controller = self.controller()
        controller._windows_events.system_sleep_pending = True
        controller._read_ui_value = Mock()
        self.assertEqual(controller.poll_external_ui_state_result("test", "test").status, "skipped")
        controller._read_ui_value.assert_not_called()
        controller._windows_events.system_sleep_pending = False
        controller._identity.current_ip = ""
        controller.resolve_target = Mock(return_value=False)
        self.assertEqual(controller.poll_external_ui_state_result("test", "test").status, "failed")

    def test_controller_reports_network_failure_and_partial_success(self):
        controller = self.controller()
        controller._read_ui_value = Mock(return_value=(None, False))
        controller.probe_external_identity = Mock(return_value=(False, False))
        self.assertEqual(controller.poll_external_ui_state_result("test", "test").status, "failed")
        controller._read_ui_value = Mock(side_effect=[(False, True), (None, False), (0, True)])
        result = controller.poll_external_ui_state_result("test", "test")
        self.assertEqual(result.status, "success")
        self.assertEqual(result.values, (None, 0, False))

    def test_new_scan_cancels_old_callbacks_and_old_close_does_not_cancel_new(self):
        bridge = self.bridge()
        bridge._controller.scan_kef_devices = Mock(return_value=[])
        with patch("kef_app.ui.web_bridge.start_background_task") as task:
            bridge.scanSpeakers("old")
            old = task.call_args
            old.args[1]()
            callbacks = bridge._controller.scan_kef_devices.call_args.kwargs
            self.assertTrue(callbacks["should_continue"]())
            bridge.scanSpeakers("new")
            new = task.call_args
            self.assertFalse(callbacks["should_continue"]())
            bridge.cancelScan("old")
            self.assertFalse(bridge._scan_cancel.is_set())
            callbacks["on_candidate"](SpeakerIdentity(ip="192.168.1.20"))
            callbacks["on_progress"](10)
            old.kwargs["on_success"]([])
            old.kwargs["on_error"](RuntimeError("old"))
            bridge.toast.emit.assert_not_called()
            new.kwargs["on_success"]([])
            self.assertEqual(bridge.toast.emit.call_args.args[0]["scan_id"], "new")
            bridge.cancelScan("new")
            self.assertTrue(bridge._scan_cancel.is_set())

    def test_reopened_scan_waits_for_lock_and_can_be_cancelled_while_waiting(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                controller = self.controller()
                controller._blind_discovery_lock.acquire()
                waiting = threading.Event()
                cancelled = threading.Event()
                def should_continue():
                    waiting.set()
                    return not cancelled.is_set()
                result = []
                with patch("kef_app.controller.discovery.recovery.discover_kef_devices", return_value=["found"]) as scan:
                    worker = threading.Thread(target=lambda: result.append(
                        controller.scan_kef_devices(should_continue=should_continue)
                    ), daemon=True)
                    worker.start()
                    try:
                        self.assertTrue(waiting.wait(1))
                        scan.assert_not_called()
                        if cancel:
                            cancelled.set()
                            worker.join(1)
                            self.assertFalse(worker.is_alive())
                    finally:
                        controller._blind_discovery_lock.release()
                        worker.join(1)
                    self.assertFalse(worker.is_alive())
                    self.assertEqual(result, [[]] if cancel else [["found"]])
                    self.assertEqual(scan.called, not cancel)

    def test_removed_alias_keys_keep_all_supported_spellings(self):
        for raw, expected in (("wi-fi", "wifi"), ("e-arc", "tv"), ("poweron_", "powerOn"), ("poweron.", "powerOn")):
            self.assertEqual(normalize_input_source(raw), expected)


if __name__ == "__main__":
    unittest.main()
