from __future__ import annotations

import unittest

from kef_app.ui.logs.log_history import should_hide_from_ui_log


class LogHistoryTests(unittest.TestCase):
    def test_hides_noisy_ui_poll_lines_without_hiding_actions(self) -> None:
        self.assertTrue(should_hide_from_ui_log("reason=web_ui_poll | step=input_source"))
        self.assertFalse(should_hide_from_ui_log("trigger=set_volume_before_action | changed=False"))
        self.assertFalse(should_hide_from_ui_log("trigger=change_input_before_action | changed=False"))

    def test_keeps_startup_and_user_action_results(self) -> None:
        self.assertFalse(should_hide_from_ui_log("PROCESS_START | pid=1234"))
        self.assertFalse(should_hide_from_ui_log("END action=SET_VOLUME | outcome=success"))


if __name__ == "__main__":
    unittest.main()
