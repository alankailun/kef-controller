from __future__ import annotations

import contextlib
import io
import logging
import tempfile
import unittest
from pathlib import Path

from kef_app.config import AppConfig
from kef_app.runtime.logging_setup import build_logger, shutdown_logger


class CollectingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


class RuntimeLoggingSetupTests(unittest.TestCase):
    def test_build_logger_always_uses_info_level(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = AppConfig().with_updates(log_dir=temp_dir)
            with contextlib.redirect_stderr(io.StringIO()):
                logger = build_logger(config)
                try:
                    self.assertEqual(logger.level, logging.INFO)
                finally:
                    shutdown_logger(logger)

    def test_build_logger_queues_file_and_extra_handlers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = AppConfig().with_updates(log_dir=temp_dir)
            extra_handler = CollectingHandler()
            with contextlib.redirect_stderr(io.StringIO()):
                logger = build_logger(config, extra_handlers=[extra_handler])
                try:
                    self.assertEqual(len(logger.handlers), 1)
                    self.assertEqual(type(logger.handlers[0]).__name__, "QueueHandler")

                    logger.info("queued logging smoke test")
                    shutdown_logger(logger)

                    log_text = Path(config.log_file).read_text(encoding="utf-8")
                    self.assertIn("queued logging smoke test", log_text)
                    self.assertRegex(
                        log_text,
                        r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]\[MainThread\]\[INFO\] queued logging smoke test",
                    )
                    self.assertIn("queued logging smoke test", extra_handler.messages)
                finally:
                    shutdown_logger(logger)


if __name__ == "__main__":
    unittest.main()
