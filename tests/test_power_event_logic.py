from __future__ import annotations

import logging
import unittest
from unittest.mock import Mock, patch

from kef_app.config import AppConfig
from kef_app.controller import KefPowerController
from kef_app.devices.speaker_models import SpeakerIdentity


class PowerEventLogicTests(unittest.TestCase):
    def make_controller(self, **config_updates) -> KefPowerController:
        config = AppConfig().with_updates(**config_updates)
        logger = logging.getLogger(f"tests.power_event_logic.{self._testMethodName}")
        logger.handlers = [logging.NullHandler()]
        logger.propagate = False
        return KefPowerController(config, logger)

    def test_on_startup_delay_can_abort_before_wake(self):
        controller = self.make_controller(wake_on_startup=True, startup_delay=0.5)
        controller._interruptible_sleep = Mock(return_value=False)
        controller.wake_kef = Mock(return_value=True)

        controller.on_startup()

        controller._interruptible_sleep.assert_called_once_with(0.5, 1, "startup_delay")
        controller.wake_kef.assert_not_called()
        self.assertEqual(controller._current_generation(), 1)

    def test_on_suspend_refreshes_generation_even_when_sleep_standby_disabled(self):
        controller = self.make_controller(standby_on_sleep=False)
        controller.standby_kef = Mock(return_value=True)

        controller.on_suspend("PBT_APMSUSPEND")

        controller.standby_kef.assert_not_called()
        self.assertEqual(controller._current_generation(), 1)

    def test_on_lock_creates_sleep_generation_before_preemptive_standby(self):
        controller = self.make_controller(standby_on_lock=True)
        seen: dict[str, object] = {}
        controller._start_controller_thread = lambda target, _thread_name: target()

        def fake_preemptive(generation: int, reason: str) -> bool:
            seen["generation"] = generation
            seen["reason"] = reason
            return True

        controller.standby_kef_preemptive = fake_preemptive

        controller.on_lock("WTS_SESSION_LOCK")

        self.assertEqual(seen, {"generation": 1, "reason": "WTS_SESSION_LOCK"})
        self.assertEqual(controller._current_generation(), 1)

    def test_preemptive_standby_does_not_mark_success_after_generation_changes(self):
        controller = self.make_controller()
        generation = controller._new_generation("sleep", "WTS_SESSION_LOCK")
        perform_calls: list[dict[str, object]] = []

        def fake_perform(**kwargs) -> None:
            perform_calls.append(kwargs)
            controller._new_generation("wake", "WTS_SESSION_UNLOCK")

        controller._perform_standby_request = fake_perform
        controller.resolve_target = Mock(return_value=True)

        result = controller.standby_kef_preemptive(generation, "WTS_SESSION_LOCK")

        self.assertFalse(result)
        self.assertEqual(len(perform_calls), 1)
        self.assertEqual(perform_calls[0]["generation"], generation)
        self.assertFalse(controller._recently_lock_standby_ok())

    def test_end_session_standby_skips_when_target_identity_is_not_verified(self):
        controller = self.make_controller(kef_ip="192.168.1.10")
        controller.resolve_target = Mock(return_value=False)
        controller._request_shutdown = Mock()

        result = controller.standby_kef_end_session("unit_test", "flags")

        self.assertFalse(result)
        controller._request_shutdown.assert_not_called()

    def test_apply_configured_device_target_updates_runtime_ip_and_mac(self):
        controller = self.make_controller(kef_ip="192.168.1.10", kef_mac="AA:BB:CC:DD:EE:01")

        controller.config.kef_ip = "192.168.1.20"
        controller.config.kef_mac = "AA:BB:CC:DD:EE:02"

        changed = controller.apply_configured_device_target(source="unit_test")

        self.assertTrue(changed)
        self.assertEqual(controller.get_current_kef_ip(), "192.168.1.20")
        self.assertEqual(controller.get_target_kef_mac(), "AABBCCDDEE02")

    def test_apply_configured_device_target_ignores_invalid_ip(self):
        controller = self.make_controller(kef_ip="192.168.1.10", kef_mac="AA:BB:CC:DD:EE:01")

        controller.config.kef_ip = "not-an-ip"
        controller.config.kef_mac = "AA:BB:CC:DD:EE:02"

        changed = controller.apply_configured_device_target(source="unit_test")

        self.assertTrue(changed)
        self.assertEqual(controller.get_current_kef_ip(), "192.168.1.10")
        self.assertEqual(controller.get_target_kef_mac(), "AABBCCDDEE02")

    def test_apply_configured_device_target_clears_target_mac(self):
        controller = self.make_controller(kef_ip="192.168.1.10", kef_mac="AA:BB:CC:DD:EE:01")

        controller.config.kef_mac = ""

        changed = controller.apply_configured_device_target(source="unit_test")

        self.assertTrue(changed)
        self.assertEqual(controller.get_target_kef_mac(), "")

    def test_apply_configured_device_target_clears_current_ip(self):
        controller = self.make_controller(kef_ip="192.168.1.10", kef_mac="AA:BB:CC:DD:EE:01")

        controller.config.kef_ip = ""

        changed = controller.apply_configured_device_target(source="unit_test")

        self.assertTrue(changed)
        self.assertEqual(controller.get_current_kef_ip(), "")

    def test_resolve_target_recovers_ip_when_only_target_mac_is_known(self):
        controller = self.make_controller(kef_ip="", kef_mac="AA:BB:CC:DD:EE:01")
        identity = SpeakerIdentity(
            ip="192.168.1.20",
            mac="AABBCCDDEE01",
            speaker_name="Office Speaker",
            speaker_model="LS50 Wireless II",
        )
        controller._backend.capture_identity = Mock(return_value=identity)

        with (
            patch("kef_app.controller.discovery.recovery.discover_ip_by_mac", return_value="192.168.1.20"),
            patch("kef_app.controller.discovery.identity_probe.identify_kef_device", return_value=identity),
        ):
            resolved = controller.resolve_target("unit_test", "unit_test", force_recovery=True)

        self.assertTrue(resolved)
        self.assertEqual(controller.get_current_kef_ip(), "192.168.1.20")

    def test_resolve_target_supplements_partial_identity_with_http_mac(self):
        controller = self.make_controller(kef_ip="192.168.1.10", kef_mac="AA:BB:CC:DD:EE:01")
        partial_identity = SpeakerIdentity(
            ip="192.168.1.10",
            speaker_model="LS50 Wireless II",
        )
        full_identity = SpeakerIdentity(
            ip="192.168.1.10",
            mac="AABBCCDDEE01",
            mac_display="AA:BB:CC:DD:EE:01",
            speaker_name="Office Speaker",
            speaker_model="LS50 Wireless II",
        )
        controller.get_speaker = Mock(return_value=object())
        controller._backend.capture_identity = Mock(return_value=partial_identity)

        with patch("kef_app.controller.discovery.identity_probe.identify_kef_device", return_value=full_identity) as identify:
            resolved = controller.resolve_target("unit_test", "unit_test", force_recovery=True)

        self.assertTrue(resolved)
        identify.assert_called_once_with("192.168.1.10", controller.config)

    def test_urgent_standby_target_can_use_cached_identity_when_live_mac_is_missing(self):
        controller = self.make_controller(kef_ip="192.168.1.10", kef_mac="AA:BB:CC:DD:EE:01")
        with controller._ip_lock:
            controller._speaker_model = "LS50 Wireless II"
        partial_identity = SpeakerIdentity(
            ip="192.168.1.10",
            speaker_model="LS50 Wireless II",
        )
        controller.get_speaker = Mock(return_value=object())
        controller._backend.capture_identity = Mock(return_value=partial_identity)

        with patch("kef_app.controller.discovery.identity_probe.identify_kef_device") as identify:
            resolved = controller.resolve_target(
                "WTS_SESSION_LOCK",
                "lock_pre_standby_before_attempts",
                force_recovery=True,
            )

        self.assertTrue(resolved)
        identify.assert_not_called()

    def test_startup_http_identity_snapshot_does_not_mutate_target(self):
        controller = self.make_controller(kef_ip="192.168.1.10", kef_mac="AA:BB:CC:DD:EE:01")
        other_identity = SpeakerIdentity(
            ip="192.168.1.10",
            mac="AABBCCDDEE02",
            mac_display="AA:BB:CC:DD:EE:02",
            speaker_name="Other Speaker",
            speaker_model="LS50 Wireless II",
        )

        with patch("kef_app.controller.discovery.identity_probe.identify_kef_device", return_value=other_identity):
            matched = controller.log_current_http_identity_snapshot("startup_prebuild", "unit_test")

        self.assertFalse(matched)
        self.assertEqual(controller.get_current_kef_ip(), "192.168.1.10")
        self.assertEqual(controller.get_target_kef_mac(), "AABBCCDDEE01")

    def test_blind_recovery_without_target_mac_does_not_replace_existing_target(self):
        controller = self.make_controller(kef_ip="192.168.1.10", kef_mac="")

        with patch("kef_app.controller.discovery.recovery.discover_kef_device_blind") as discover:
            refreshed = controller.maybe_refresh_kef_ip_by_blind("unit_test", "unit_test", force=True)

        self.assertFalse(refreshed)
        discover.assert_not_called()
        self.assertEqual(controller.get_current_kef_ip(), "192.168.1.10")

    def test_blind_recovery_without_target_mac_requires_manual_selection(self):
        controller = self.make_controller(kef_ip="", kef_mac="")
        identity = SpeakerIdentity(
            ip="192.168.1.20",
            mac="AABBCCDDEE01",
            speaker_name="Office Speaker",
            speaker_model="LS50 Wireless II",
        )

        with patch("kef_app.controller.discovery.recovery.discover_kef_device_blind", return_value=identity) as discover:
            refreshed = controller.maybe_refresh_kef_ip_by_blind("unit_test", "unit_test", force=True)

        self.assertFalse(refreshed)
        discover.assert_not_called()
        self.assertEqual(controller.get_current_kef_ip(), "")

    def test_scan_kef_devices_does_not_change_current_target(self):
        controller = self.make_controller(kef_ip="192.168.1.10", kef_mac="AA:BB:CC:DD:EE:01")
        found = [
            SpeakerIdentity(
                ip="192.168.1.20",
                mac="AABBCCDDEE02",
                speaker_name="Other Speaker",
                speaker_model="LSX II",
            )
        ]

        with patch("kef_app.controller.discovery.recovery.discover_kef_devices", return_value=found):
            result = controller.scan_kef_devices()

        self.assertEqual(result, found)
        self.assertEqual(controller.get_current_kef_ip(), "192.168.1.10")
        self.assertEqual(controller.get_target_kef_mac(), "AABBCCDDEE01")

    def test_select_kef_device_updates_current_target(self):
        controller = self.make_controller(kef_ip="192.168.1.10", kef_mac="AA:BB:CC:DD:EE:01")
        identity = SpeakerIdentity(
            ip="192.168.1.20",
            mac="AABBCCDDEE02",
            speaker_name="Office Speaker",
            speaker_model="LS50 Wireless II",
        )

        changed = controller.select_kef_device(identity, source="unit_test")

        self.assertTrue(changed)
        self.assertEqual(controller.get_current_kef_ip(), "192.168.1.20")
        self.assertEqual(controller.get_target_kef_mac(), "AABBCCDDEE02")

    def test_select_kef_device_without_mac_clears_previous_target_mac(self):
        controller = self.make_controller(kef_ip="192.168.1.10", kef_mac="AA:BB:CC:DD:EE:01")
        identity = SpeakerIdentity(
            ip="192.168.1.20",
            speaker_name="Office Speaker",
            speaker_model="LS50 Wireless II",
        )

        changed = controller.select_kef_device(identity, source="unit_test")

        self.assertTrue(changed)
        self.assertEqual(controller.get_current_kef_ip(), "192.168.1.20")
        self.assertEqual(controller.get_target_kef_mac(), "")

    def test_validate_manual_target_rejects_mac_mismatch(self):
        controller = self.make_controller()
        identity = SpeakerIdentity(
            ip="192.168.1.20",
            mac="AABBCCDDEE02",
            speaker_name="Office Speaker",
            speaker_model="LS50 Wireless II",
        )
        controller.inspect_kef_identity_at_ip = Mock(return_value=identity)

        result = controller.validate_manual_target(
            "192.168.1.20",
            "AA:BB:CC:DD:EE:01",
            reason="unit_test",
            trigger="unit_test",
        )

        self.assertEqual(result.status, "mac_mismatch")
        self.assertEqual(controller.get_current_kef_ip(), "")

    def test_validate_manual_target_allows_unreachable_ip_only(self):
        controller = self.make_controller()
        controller.inspect_kef_identity_at_ip = Mock(return_value=None)

        with patch("kef_app.controller.discovery.manual_target.probe_ip_port", return_value=False):
            result = controller.validate_manual_target(
                "192.168.1.20",
                "",
                reason="unit_test",
                trigger="unit_test",
            )

        self.assertEqual(result.status, "unreachable")
        self.assertEqual(result.requested_ip, "192.168.1.20")

    def test_validate_manual_target_recovers_mac_only(self):
        controller = self.make_controller()
        identity = SpeakerIdentity(
            ip="192.168.1.20",
            mac="AABBCCDDEE01",
            speaker_name="Office Speaker",
            speaker_model="LS50 Wireless II",
        )
        controller.inspect_kef_identity_at_ip = Mock(return_value=identity)

        with patch("kef_app.controller.discovery.manual_target.discover_ip_by_mac", return_value="192.168.1.20"):
            result = controller.validate_manual_target(
                "",
                "AA:BB:CC:DD:EE:01",
                reason="unit_test",
                trigger="unit_test",
            )

        self.assertEqual(result.status, "recovered")
        self.assertEqual(result.identity.ip, "192.168.1.20")
        self.assertEqual(controller.get_current_kef_ip(), "")

    def test_live_input_rejects_empty_or_unsupported_source(self):
        controller = self.make_controller(kef_ip="192.168.1.10")

        self.assertFalse(controller.change_input_live(""))
        self.assertFalse(controller.change_input_live("standby"))

    def test_set_volume_rejects_non_numeric_level_without_connector_call(self):
        controller = self.make_controller(kef_ip="192.168.1.10")

        self.assertFalse(controller.set_volume("loud"))  # type: ignore[arg-type]

    def test_wake_rejects_unsupported_configured_input(self):
        controller = self.make_controller(kef_ip="192.168.1.10", kef_input="standby")
        controller.wait_until_reachable = Mock(return_value=True)

        self.assertFalse(controller.wake_kef(0, "unit_test"))
        controller.wait_until_reachable.assert_not_called()


if __name__ == "__main__":
    unittest.main()
