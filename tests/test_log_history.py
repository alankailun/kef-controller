from __future__ import annotations

import os
import tempfile
import unittest

from kef_app.ui.logs.log_history import list_log_history_files, resolve_log_history_file, should_hide_from_ui_log


class LogHistoryTests(unittest.TestCase):
    def test_hides_noisy_ui_poll_lines_without_hiding_actions(self) -> None:
        self.assertTrue(should_hide_from_ui_log("trigger=web_ui_poll | step=input_source"))
        self.assertFalse(should_hide_from_ui_log("trigger=set_volume_before_action | changed=False"))
        self.assertFalse(should_hide_from_ui_log("trigger=change_input_before_action | changed=False"))

    def test_keeps_startup_and_user_action_results(self) -> None:
        self.assertFalse(should_hide_from_ui_log("PROCESS_START | pid=1234"))
        self.assertFalse(should_hide_from_ui_log("END action=SET_VOLUME | outcome=success"))

    def test_lists_rotated_logs_and_rejects_other_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            current = os.path.join(directory, "kef_controller.log")
            for name in ("kef_controller.log.2026-07-13", "kef_controller.log.2026-07-14", "other.log"):
                open(os.path.join(directory, name), "w", encoding="utf-8").close()

            self.assertEqual(
                list_log_history_files(current),
                ["kef_controller.log", "kef_controller.log.2026-07-14", "kef_controller.log.2026-07-13"],
            )
            self.assertEqual(
                resolve_log_history_file(current, "kef_controller.log.2026-07-14"),
                os.path.join(directory, "kef_controller.log.2026-07-14"),
            )
            with self.assertRaises(ValueError):
                resolve_log_history_file(current, "..\\state.json")


if __name__ == "__main__":
    unittest.main()
