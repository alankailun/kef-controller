from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from kef_app.ui.main_window import KefMainWindow


class KefMainWindowHostTests(unittest.TestCase):
    @staticmethod
    def _window() -> KefMainWindow:
        # These tests exercise the native-host lifecycle only, without needing
        # a QApplication or a live WebView2 process.
        window = KefMainWindow.__new__(KefMainWindow)
        window._host_hwnd = 0
        window._host_process = None
        window._monitor = Mock()
        window._launch_host = Mock()
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


if __name__ == "__main__":
    unittest.main()
