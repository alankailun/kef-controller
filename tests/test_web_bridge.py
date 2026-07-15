from __future__ import annotations

import unittest

from kef_app.ui.web_bridge import _EVENTS, _wake_is_confirmed


class WebBridgeTests(unittest.TestCase):
    def test_wake_requires_a_live_non_standby_input_before_controls_enable(self) -> None:
        self.assertFalse(_wake_is_confirmed(False, "wifi"))
        self.assertFalse(_wake_is_confirmed(True, None))
        self.assertFalse(_wake_is_confirmed(True, "standby"))
        self.assertTrue(_wake_is_confirmed(True, "wifi"))

    def test_lid_close_simulation_uses_the_lid_close_rule(self) -> None:
        label, setting, _description, _runner = _EVENTS["lid-close"]

        self.assertEqual(label, "Lid Close")
        self.assertEqual(setting, "standby_on_lid_close")


if __name__ == "__main__":
    unittest.main()
