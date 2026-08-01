from __future__ import annotations

import logging
import unittest

from kef_app.ui.logs.log_handler import UILogHandler


class UILogHandlerTests(unittest.TestCase):
    def test_formats_live_lines_with_the_authoritative_log_level(self) -> None:
        handler = UILogHandler()
        record = logging.LogRecord(
            name="kef_controller",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="failed checks=0 is still the message",
            args=(),
            exc_info=None,
        )

        handler.emit(record)

        self.assertRegex(
            handler.snapshot_lines()[0],
            r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]\[MainThread\]\[ERROR\] failed checks=0 is still the message$",
        )

    def test_keeps_debug_poll_records_when_debug_verbosity_emits_them(self) -> None:
        handler = UILogHandler()
        record = logging.LogRecord(
            name="kef_controller",
            level=logging.DEBUG,
            pathname=__file__,
            lineno=1,
            msg="STEP action=POLL_EXTERNAL_STATE | trigger=web_ui_poll | mono=1.000",
            args=(),
            exc_info=None,
        )

        handler.emit(record)

        self.assertEqual(len(handler.snapshot_lines()), 1)
        self.assertIn("[DEBUG] STEP action=POLL_EXTERNAL_STATE", handler.snapshot_lines()[0])


if __name__ == "__main__":
    unittest.main()
