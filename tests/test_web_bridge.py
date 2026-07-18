from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from kef_app.config import AppConfig
from kef_app.storage import UserConfigStore
from kef_app.ui.web_bridge import WebControllerBridge, _EVENTS, _wake_is_confirmed


class WebBridgeTests(unittest.TestCase):
    def test_startup_switch_passes_explicit_values_and_defaults_back_to_registry(self) -> None:
        html = (Path(__file__).parents[1] / "kef_app" / "ui" / "web" / "index.html").read_text(encoding="utf-8")

        self.assertNotIn('addEventListener("change", pushStartup)', html)
        self.assertIn('pushStartup("registry", e.currentTarget.checked)', html)
        registry_button = html.index('data-startup-mode="registry"')
        task_button = html.index('data-startup-mode="task"')
        self.assertLess(registry_button, task_button)

    def test_startup_update_is_scheduled_without_blocking_the_ui_thread(self) -> None:
        bridge = WebControllerBridge.__new__(WebControllerBridge)
        bridge._config = AppConfig()
        bridge._config_store = Mock()
        bridge._config_store.FIELD_COERCERS = UserConfigStore.FIELD_COERCERS
        bridge._controller = Mock()
        bridge._controller.log = Mock()
        bridge._startup_busy = False
        bridge._startup_requested_mode = None
        bridge._startup_requested_enabled = None
        bridge.publish_state = Mock()
        bridge._notify = Mock()

        with patch("kef_app.ui.web_bridge.start_background_task", return_value=Mock()) as start_task:
            bridge.updateStartup("task", True)

        self.assertTrue(bridge._startup_busy)
        self.assertEqual(bridge._startup_requested_mode, "task")
        self.assertTrue(bridge._startup_requested_enabled)
        bridge.publish_state.assert_called_once_with()
        start_task.assert_called_once()
        self.assertEqual(start_task.call_args.args[0], "WebUpdateStartup")

    def test_wake_requires_a_live_non_standby_input_before_controls_enable(self) -> None:
        self.assertFalse(_wake_is_confirmed(False, "wifi"))
        self.assertFalse(_wake_is_confirmed(True, None))
        self.assertFalse(_wake_is_confirmed(True, "standby"))
        self.assertTrue(_wake_is_confirmed(True, "wifi"))

    def test_lid_close_simulation_uses_the_lid_close_rule(self) -> None:
        label, setting, _description, _runner = _EVENTS["lid-close"]

        self.assertEqual(label, "Lid Close")
        self.assertEqual(setting, "standby_on_lid_close")

    def test_ui_visibility_stops_background_speaker_polling(self) -> None:
        bridge = WebControllerBridge.__new__(WebControllerBridge)
        bridge._ui_visible = False
        bridge._poll_timer = Mock()
        bridge._poll_speaker_state = Mock()

        bridge.set_ui_visible(False)
        bridge._poll_timer.stop.assert_not_called()

        bridge.set_ui_visible(True)
        bridge._poll_timer.start.assert_called_once_with()
        bridge._poll_speaker_state.assert_called_once_with(force=True)

        bridge.set_ui_visible(False)
        bridge._poll_timer.stop.assert_called_once_with()

    def test_health_exposes_recent_failure_without_retaining_stale_errors(self) -> None:
        bridge = WebControllerBridge.__new__(WebControllerBridge)
        bridge._last_failure = ("timed out", 100.0)

        with patch("kef_app.ui.web_bridge.time.monotonic", return_value=160.0):
            self.assertEqual(
                bridge._recent_failure(),
                {"detail": "timed out", "age_s": 60.0},
            )
        with patch("kef_app.ui.web_bridge.time.monotonic", return_value=401.0):
            self.assertIsNone(bridge._recent_failure())


if __name__ == "__main__":
    unittest.main()
