from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QObject

from kef_app.ui.main_window import KefMainWindow


class KefMainWindowHostTests(unittest.TestCase):
    @staticmethod
    def _window() -> KefMainWindow:
        # These tests exercise the native-host lifecycle only, without needing
        # a QApplication or a live WebView2 process.
        window = KefMainWindow.__new__(KefMainWindow)
        QObject.__init__(window)
        window._host_hwnd = 0
        window._host_process = None
        window._monitor = Mock()
        window._launch_host = Mock()
        window._server = Mock()
        window._host_ready_mono = 0.0
        window._last_host_restart_mono = 0.0
        window._was_effectively_visible = False
        window._log = Mock()
        return window

    def test_show_does_not_launch_another_host_while_one_is_starting(self) -> None:
        window = self._window()
        process = Mock()
        process.poll.return_value = None
        window._host_process = process

        window.show()

        window._launch_host.assert_not_called()
        window._monitor.start.assert_called_once_with()

    def test_launch_is_idempotent_while_host_process_is_alive(self) -> None:
        window = self._window()
        process = Mock()
        process.poll.return_value = None
        window._host_process = process

        with patch("kef_app.ui.main_window.subprocess.Popen") as popen:
            KefMainWindow._launch_host(window)

        popen.assert_not_called()

    def test_finds_only_the_window_owned_by_the_host_process(self) -> None:
        def enumerate_windows(callback, _extra) -> None:
            callback(100, None)
            callback(200, None)

        with (
            patch("kef_app.ui.main_window.win32gui.EnumWindows", side_effect=enumerate_windows),
            patch("kef_app.ui.main_window.win32gui.GetWindowText", return_value="KEF Controller"),
            patch("kef_app.ui.main_window.win32gui.GetClassName", return_value="WindowsForms10.Window"),
            patch(
                "kef_app.ui.main_window.win32process.GetWindowThreadProcessId",
                side_effect=[(1, 10), (2, 20)],
            ),
        ):
            hwnd = KefMainWindow._find_host_window(20)

        self.assertEqual(hwnd, 200)

    def test_restarts_a_visible_host_when_the_ui_heartbeat_has_stalled(self) -> None:
        window = self._window()
        process = Mock()
        process.poll.return_value = None
        window._host_process = process
        window._host_hwnd = 100
        window._host_ready_mono = 100.0
        window._server.client_activity_age_s = 61.0
        window._terminate_host_tree = Mock()

        with patch("kef_app.ui.main_window.time.monotonic", return_value=161.0):
            self.assertTrue(window._host_needs_restart(True))
            window._restart_unresponsive_host()

        window._terminate_host_tree.assert_called_once_with()
        window._launch_host.assert_called_once_with()

    def test_show_refreshes_the_ui_heartbeat_before_restoring_a_hidden_host(self) -> None:
        window = self._window()
        window._host_hwnd = 100

        with (
            patch("kef_app.ui.main_window.win32gui.IsWindow", return_value=True),
            patch("kef_app.ui.main_window.win32gui.ShowWindow"),
            patch("kef_app.ui.main_window.win32gui.SetForegroundWindow"),
        ):
            window.show()

        window._server._touch_client_activity.assert_called_once_with()

    def test_effective_visibility_treats_a_minimized_window_as_hidden(self) -> None:
        window = self._window()
        window._host_hwnd = 100

        with (
            patch("kef_app.ui.main_window.win32gui.IsWindow", return_value=True),
            patch("kef_app.ui.main_window.win32gui.IsWindowVisible", return_value=True),
            patch("kef_app.ui.main_window.win32gui.IsIconic", return_value=True),
        ):
            self.assertFalse(window.isVisible())

    def test_restore_resets_the_heartbeat_before_watchdog_evaluation(self) -> None:
        window = self._window()
        process = Mock()
        process.poll.return_value = None
        window._host_process = process
        window._host_hwnd = 100
        window._host_ready_mono = 100.0
        window._server.client_activity_age_s = 600.0
        window._host_needs_restart = Mock(return_value=False)

        with (
            patch("kef_app.ui.main_window.win32gui.IsWindow", return_value=True),
            patch.object(window, "_effectively_visible", return_value=True),
        ):
            window._monitor_host()

        window._server._touch_client_activity.assert_called_once_with()
        window._host_needs_restart.assert_called_once_with(True)
        self.assertTrue(window._was_effectively_visible)

    def test_minimized_host_skips_watchdog_and_pauses_ui_visibility(self) -> None:
        window = self._window()
        process = Mock()
        process.poll.return_value = None
        window._host_process = process
        window._host_hwnd = 100
        window._host_ready_mono = 100.0
        window._was_effectively_visible = True
        window._host_needs_restart = Mock()
        visibility: list[bool] = []
        window.visibility_changed.connect(visibility.append)

        with (
            patch("kef_app.ui.main_window.win32gui.IsWindow", return_value=True),
            patch.object(window, "_effectively_visible", return_value=False),
        ):
            window._monitor_host()

        window._host_needs_restart.assert_called_once_with(False)
        self.assertEqual(visibility, [False])
        self.assertFalse(window._was_effectively_visible)

    def test_terminate_host_tree_uses_taskkill_tree_mode(self) -> None:
        window = self._window()
        process = Mock(pid=4321)
        process.poll.return_value = None
        window._host_process = process

        with patch("kef_app.ui.main_window.subprocess.run") as run:
            run.return_value.returncode = 0
            KefMainWindow._terminate_host_tree(window)

        run.assert_called_once_with(
            ["taskkill.exe", "/PID", "4321", "/T", "/F"],
            check=False,
            capture_output=True,
            creationflags=getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0),
            timeout=5,
        )
        process.terminate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
