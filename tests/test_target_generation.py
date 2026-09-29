from __future__ import annotations

import logging
import unittest
from unittest.mock import Mock, patch

from kef_app.config import AppConfig
from kef_app.controller import KefPowerController
from kef_app.devices.speaker_models import SpeakerIdentity


class TargetGenerationTests(unittest.TestCase):
    def make_controller(self) -> KefPowerController:
        config = AppConfig().with_updates(kef_ip="10.0.0.10", kef_mac="AAAAAAAAAAAA")
        logger = logging.getLogger(f"tests.target_generation.{self._testMethodName}")
        logger.addHandler(logging.NullHandler())
        return KefPowerController(config, logger)

    @staticmethod
    def switch_to_b(controller: KefPowerController) -> None:
        controller.config.kef_ip = "10.0.0.20"
        controller.config.kef_mac = "BBBBBBBBBBBB"
        controller.apply_configured_device_target(
            trigger="test_switch",
            info=SpeakerIdentity(ip="10.0.0.20", mac="BBBBBBBBBBBB", speaker_name="Speaker B"),
        )

    def test_old_mac_recovery_cannot_replace_new_target(self):
        controller = self.make_controller()

        def finish_old_scan(*_args):
            self.switch_to_b(controller)
            return "10.0.0.11"

        with patch("kef_app.controller.discovery.recovery.discover_ip_by_mac", side_effect=finish_old_scan):
            self.assertFalse(controller.maybe_refresh_kef_ip_by_mac("test", "test", force=True))

        self.assertEqual(controller.get_current_kef_target(), ("10.0.0.20", "BBBBBBBBBBBB"))
        self.assertEqual(controller.get_current_identity().speaker_name, "Speaker B")
        self.assertEqual(controller._fast_standby_send_cache.read().target_ip, "10.0.0.20")

    def test_old_blind_recovery_cannot_replace_new_target(self):
        controller = self.make_controller()

        def finish_old_scan(*_args, **_kwargs):
            self.switch_to_b(controller)
            return SpeakerIdentity(ip="10.0.0.11", mac="AAAAAAAAAAAA", speaker_name="Speaker A")

        with patch("kef_app.controller.discovery.recovery.discover_kef_device_blind", side_effect=finish_old_scan):
            self.assertFalse(controller.maybe_refresh_kef_ip_by_blind("test", "test", force=True))

        self.assertEqual(controller.get_current_kef_target(), ("10.0.0.20", "BBBBBBBBBBBB"))
        self.assertEqual(controller.get_current_identity().speaker_name, "Speaker B")

    def test_target_switch_clears_old_identity_and_runtime_values(self):
        controller = self.make_controller()
        controller.update_identity_from_device_info(
            SpeakerIdentity(ip="10.0.0.10", mac="AAAAAAAAAAAA", speaker_name="Speaker A"), trigger="test",
        )
        controller._set_speaker_runtime_state(input_source="wifi", volume=65, speaker_on=True, trigger="test")
        controller.config.kef_ip = "10.0.0.20"
        controller.config.kef_mac = "BBBBBBBBBBBB"
        controller.apply_configured_device_target(trigger="test_switch")

        self.assertEqual(controller.get_current_identity().speaker_name, "")
        self.assertEqual(controller._runtime_speaker.input_source, "")
        self.assertIsNone(controller._runtime_speaker.volume)
        self.assertIsNone(controller._runtime_speaker.power_on)

    def test_late_poll_from_old_target_is_discarded(self):
        controller = self.make_controller()
        reads = iter([(True, True), ("wifi", True), (65, True)])

        def read(*_args, **_kwargs):
            value = next(reads)
            if value[0] == 65:
                self.switch_to_b(controller)
            return value

        controller._read_ui_value = Mock(side_effect=read)
        controller.probe_external_identity = Mock(return_value=(True, False))
        result = controller.poll_external_ui_state_result("test", "test")

        self.assertEqual(result.status, "skipped")
        self.assertIsNone(controller._runtime_speaker.volume)


    def mark_recently_verified(self, controller: KefPowerController) -> None:
        with controller._ip_lock:
            controller._identity.verified_ip = controller._identity.current_ip
            controller._identity.verified_generation = controller._identity.generation
            controller._identity.verified_mono = controller.mono()

    def test_recent_verification_does_not_make_failed_reads_reachable(self):
        controller = self.make_controller()
        self.mark_recently_verified(controller)
        controller._read_ui_value = Mock(return_value=(None, False))
        controller.capture_identity_from_current_ip = Mock(return_value=False)
        controller.maybe_refresh_kef_ip = Mock(return_value=False)

        result = controller.poll_external_ui_state_result("test", "test")

        self.assertEqual(result.status, "failed")
        controller.capture_identity_from_current_ip.assert_called_once()
        controller.maybe_refresh_kef_ip.assert_called_once()
        self.assertEqual(controller._runtime_speaker.last_ui_target_success_mono, 0.0)

    def test_recent_verification_still_skips_identity_after_successful_reads(self):
        controller = self.make_controller()
        self.mark_recently_verified(controller)
        controller._read_ui_value = Mock(side_effect=[(True, True), ("wifi", True), (40, True)])
        controller.capture_identity_from_current_ip = Mock(return_value=True)

        result = controller.poll_external_ui_state_result("test", "test")

        self.assertEqual(result.status, "success")
        controller.capture_identity_from_current_ip.assert_not_called()

    def test_stale_recovery_result_is_logged_and_rejected(self):
        controller = self.make_controller()
        generation = controller.get_target_generation()
        self.switch_to_b(controller)
        controller._log_structured = Mock()

        applied = controller.apply_recovered_target(
            "10.0.0.11", generation=generation, target_mac="AAAAAAAAAAAA", trigger="test",
        )

        self.assertFalse(applied)
        self.assertEqual(controller.get_current_kef_ip(), "10.0.0.20")
        controller._log_structured.assert_called_once()
        self.assertEqual(controller._log_structured.call_args.kwargs["cause"], "target_changed")


if __name__ == "__main__":
    unittest.main()
