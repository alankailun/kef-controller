from __future__ import annotations

import logging
import unittest
from unittest.mock import Mock

from kef_app.config import AppConfig
from kef_app.controller import KefPowerController


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

        result = controller.standby_kef_preemptive(generation, "WTS_SESSION_LOCK")

        self.assertFalse(result)
        self.assertEqual(len(perform_calls), 1)
        self.assertEqual(perform_calls[0]["generation"], generation)
        self.assertFalse(controller._recently_lock_standby_ok())

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
