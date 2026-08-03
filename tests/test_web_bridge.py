from __future__ import annotations

import re
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from kef_app.config import AppConfig
from kef_app.config.user_settings import USER_SETTINGS_FIELD_PATHS
from kef_app.storage import UserConfigStore
from kef_app.ui.web_bridge import WebControllerBridge, _EVENTS, _wake_is_confirmed
from kef_app.ui.settings.settings_service import SPEAKER_POWER_OPTIONS


class WebBridgeTests(unittest.TestCase):
    def test_startup_switch_passes_explicit_values_and_defaults_back_to_registry(self) -> None:
        html = (Path(__file__).parents[1] / "kef_app" / "ui" / "web" / "index.html").read_text(encoding="utf-8")

        self.assertNotIn('addEventListener("change", pushStartup)', html)
        self.assertIn('pushStartup("registry", e.currentTarget.checked)', html)
        registry_button = html.index('data-startup-mode="registry"')
        task_button = html.index('data-startup-mode="task"')
        self.assertLess(registry_button, task_button)

    def test_web_ui_bootstraps_from_current_state_and_coalesces_updates(self) -> None:
        html = (Path(__file__).parents[1] / "kef_app" / "ui" / "web" / "index.html").read_text(encoding="utf-8")

        self.assertIn('bootstrap: () => api("bootstrap")', html)
        self.assertIn("bridge.bootstrap().then(boot =>", html)
        self.assertIn("let latestState = null;", html)
        self.assertIn('else if (page === "settings") syncSettings();', html)

    def test_web_ui_keeps_the_renderer_heartbeat_running_while_occluded(self) -> None:
        html = (Path(__file__).parents[1] / "kef_app" / "ui" / "web" / "index.html").read_text(encoding="utf-8")

        self.assertIn("it is the renderer heartbeat", html)
        self.assertIn("function ensureUpdatePolling()", html)
        self.assertNotIn("document.hidden", html)
        self.assertNotIn("visibilitychange", html)

    def test_web_ui_loads_static_styles_and_localization_data_before_app_logic(self) -> None:
        web_root = Path(__file__).parents[1] / "kef_app" / "ui" / "web"
        html = (web_root / "index.html").read_text(encoding="utf-8")

        self.assertIn('<link rel="stylesheet" href="styles.css">', html)
        self.assertIn('<script src="texts.js"></script>', html)
        self.assertNotIn("<style>", html)
        self.assertIn("const TEXT = {", (web_root / "texts.js").read_text(encoding="utf-8"))

    def test_web_ui_uses_authoritative_severity_and_lifecycle_token_categories(self) -> None:
        html = (Path(__file__).parents[1] / "kef_app" / "ui" / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn('const structured = raw.match(/^\\[([^\\]]+)\\]\\[([^\\]]+)\\]\\[([A-Z]+)\\]\\s*(.*)$/);', html)
        self.assertIn('const legacy = structured ? null : raw.match(/^\\[([^\\]]+)\\]\\[([^\\]]+)\\]\\s*(.*)$/);', html)
        self.assertIn('const prefixLevel = original.match(/^(ERROR|WARN(?:ING)?|INFO)\\s*[:|-]?\\s*/i);', html)
        self.assertIn('const rawLevel = structured ? structured[3] : (prefixLevel ? prefixLevel[1] : "INFO");', html)
        self.assertIn('const category = categorized.match(/^(BEGIN|STEP|END|EVENT|STATE|SKIP)\\s/);', html)
        self.assertIn('if ((lvl === "INFO" || lvl === "DEBUG") && category) {', html)
        self.assertIn('const message = category ? categorized.slice(category[0].length) : categorized;', html)
        self.assertNotIn('const isProcessLifecycle =', html)
        self.assertNotIn('const isPrewarmRetry =', html)

    def test_web_ui_keeps_input_sources_left_aligned(self) -> None:
        css = (Path(__file__).parents[1] / "kef_app" / "ui" / "web" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('.input-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr));', css)
        self.assertNotIn('.input-btn:last-child', css)

    def test_web_ui_uses_the_classic_dark_active_severity_chips(self) -> None:
        css = (Path(__file__).parents[1] / "kef_app" / "ui" / "web" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('.severity-btn.active.INFO  { color: #cbd5e1; border-color: #334155; background: #1e293b; }', css)
        self.assertIn('.severity-btn.active.WARN  { color: #fcd34d; border-color: #78350f; background: #451a03; }', css)

    def test_web_ui_keeps_empty_severity_filters_selectable(self) -> None:
        html = (Path(__file__).parents[1] / "kef_app" / "ui" / "web" / "index.html").read_text(encoding="utf-8")

        self.assertIn('button.disabled = false;', html)

    def test_power_pending_uses_structured_toast_fields_not_english_copy(self) -> None:
        html = (Path(__file__).parents[1] / "kef_app" / "ui" / "web" / "index.html").read_text(encoding="utf-8")

        self.assertIn('const powerAction = String(msg.action || "").toUpperCase();', html)
        self.assertIn('const powerPhase = String(msg.phase || "");', html)
        self.assertNotIn('POWER_ACTION_TITLES', html)
        self.assertNotIn('Wake is running', html)

    def test_structured_power_toast_includes_machine_readable_fields(self) -> None:
        bridge = WebControllerBridge.__new__(WebControllerBridge)
        bridge._encode = lambda payload: payload
        bridge.toast = Mock()

        bridge._notify("success", "Wake", "Completed", action="WAKE", phase="finished", success=True)

        bridge.toast.emit.assert_called_once_with(
            {
                "kind": "toast",
                "level": "success",
                "title": "Wake",
                "detail": "Completed",
                "action": "WAKE",
                "phase": "finished",
                "success": True,
            }
        )

    def test_power_action_background_error_emits_structured_finished_toast(self) -> None:
        bridge = WebControllerBridge.__new__(WebControllerBridge)
        bridge._controller = Mock()
        bridge._controller.log = Mock()
        bridge._notify = Mock()
        bridge._poll_speaker_state = Mock()

        with patch("kef_app.ui.web_bridge.start_background_task") as start_task:
            bridge._start_action("WebWake", lambda: True, "Wake requested", action="wake")

        start_task.call_args.kwargs["on_error"](RuntimeError("unexpected failure"))
        failure_call = bridge._notify.call_args_list[-1]
        self.assertEqual(failure_call.args, ("error", "Speaker action failed", "unexpected failure"))
        self.assertEqual(
            failure_call.kwargs,
            {"action": "WAKE", "phase": "finished", "success": False},
        )

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
        label, setting, _runner = _EVENTS["lid-close"]

        self.assertEqual(label, "Lid Close")
        self.assertEqual(setting, "standby_on_lid_close")

    def test_power_event_metadata_stays_consistent_across_config_and_both_uis(self) -> None:
        event_keys = {setting_key for _label, setting_key, _runner in _EVENTS.values()}
        settings_keys = {option.key for option in SPEAKER_POWER_OPTIONS}
        html = (Path(__file__).parents[1] / "kef_app" / "ui" / "web" / "index.html").read_text(encoding="utf-8")
        event_rows = html.split("const EVENT_ROWS = [", 1)[1].split("];", 1)[0]
        web_keys = set(re.findall(r'\b(?:wake|standby):\s*"([^"]+)"', event_rows))

        self.assertSetEqual(event_keys, settings_keys)
        self.assertSetEqual(event_keys, web_keys)
        self.assertTrue(event_keys.issubset(USER_SETTINGS_FIELD_PATHS))

    def test_disabled_lid_close_simulation_reports_no_action_without_an_exception(self) -> None:
        bridge = WebControllerBridge.__new__(WebControllerBridge)
        bridge._config = AppConfig().with_updates(standby_on_lid_close=False)
        bridge._notify = Mock()
        bridge._emit_event_result = Mock()

        bridge.runEvent("lid-close")

        detail = "Put Speaker in Standby When the Laptop Lid Closes is currently off."
        bridge._notify.assert_called_once_with("warning", "Lid Close: no action", detail)
        bridge._emit_event_result.assert_called_once_with("lid-close", "warning", "No action", detail)

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
