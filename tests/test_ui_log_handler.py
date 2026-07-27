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


if __name__ == "__main__":
    unittest.main()
