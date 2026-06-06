from __future__ import annotations

import logging
import unittest
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication

from kef_app.config import AppConfig
from kef_app.controller import KefPowerController
from kef_app.storage import UserConfigStore
from kef_app.ui.event_test_view import EventTestPanel
from kef_app.ui.settings.settings_service import StartupStatusView
from kef_app.ui.settings.settings_view import SettingsInterface


class SettingsEventTestsUiTests(unittest.TestCase):
    _app: QApplication | None = None

    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def make_controller(self, config: AppConfig) -> KefPowerController:
        logger = logging.getLogger(f"tests.settings_event_tests_ui.{self._testMethodName}")
        logger.handlers = [logging.NullHandler()]
        logger.propagate = False
        return KefPowerController(config, logger)

    def test_settings_event_tests_button_expands_and_collapses_panel(self):
        config = AppConfig()
        controller = self.make_controller(config)
        config_store = Mock(spec=UserConfigStore)
        status = StartupStatusView(
            current_label="Disabled",
            current_detail="No Windows startup entry is currently registered.",
            cleanup_needed=False,
            current_is_healthy=True,
            preferred_label="Registry Run",
            cleanup_text="Clean",
        )

        with (
            patch("kef_app.ui.settings.settings_view.is_startup_registered", return_value=False),
            patch("kef_app.ui.settings.settings_view.get_startup_status_view", return_value=status),
        ):
            view = SettingsInterface(config, controller, config_store)

        try:
            self.assertFalse(view._event_tests_expanded)
            self.assertTrue(view._event_tests.isHidden())

            view._toggle_event_tests()

            self.assertTrue(view._event_tests_expanded)
            self.assertFalse(view._event_tests.isHidden())
            self.assertEqual(view._event_tests_toggle.button.text(), "Hide Tests")

            view._toggle_event_tests()

            self.assertFalse(view._event_tests_expanded)
            self.assertTrue(view._event_tests.isHidden())
            self.assertEqual(view._event_tests_toggle.button.text(), "Show Tests")
        finally:
            view.deleteLater()
            self._app.processEvents()

    def test_event_test_panel_skips_disabled_startup_event(self):
        config = AppConfig().with_updates(wake_on_startup=False)
        controller = self.make_controller(config)
        controller.on_startup = Mock()
        panel = EventTestPanel(config, controller)

        try:
            with patch("kef_app.ui.event_test_view.InfoBar.warning"):
                panel._test_startup()

            controller.on_startup.assert_not_called()
            self.assertEqual(panel._event_status.text(), "Skipped: Startup.")
            self.assertEqual(panel._active_tests, 0)
        finally:
            panel.deleteLater()
            self._app.processEvents()

    def test_event_test_panel_includes_display_off_event(self):
        config = AppConfig()
        controller = self.make_controller(config)
        panel = EventTestPanel(config, controller)

        try:
            self.assertIn("Display Off", panel._buttons_by_label)
        finally:
            panel.deleteLater()
            self._app.processEvents()

    def test_event_test_panel_skips_disabled_display_off_event(self):
        config = AppConfig().with_updates(standby_on_sleep=False)
        controller = self.make_controller(config)
        controller.on_display_off = Mock()
        panel = EventTestPanel(config, controller)

        try:
            with patch("kef_app.ui.event_test_view.InfoBar.warning"):
                panel._test_display_off()

            controller.on_display_off.assert_not_called()
            self.assertEqual(panel._event_status.text(), "Skipped: Display Off.")
            self.assertEqual(panel._active_tests, 0)
        finally:
            panel.deleteLater()
            self._app.processEvents()


if __name__ == "__main__":
    unittest.main()
