from __future__ import annotations

import logging
import os
import sys
from logging.handlers import TimedRotatingFileHandler

from .appdata import AppConfig


def build_logger(config: AppConfig) -> logging.Logger:
    os.makedirs(config.log_dir, exist_ok=True)
    log_level = logging.DEBUG if config.diagnostic_logging else logging.INFO

    file_formatter = logging.Formatter(
        fmt="[%(asctime)s][%(threadName)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = TimedRotatingFileHandler(
        config.log_file,
        when="midnight",
        interval=1,
        backupCount=config.log_backup_days,
        encoding="utf-8",
        delay=True,
    )
    file_handler.setFormatter(file_formatter)

    logger = logging.getLogger("kef_controller")
    logger.setLevel(log_level)
    logger.handlers.clear()
    logger.propagate = False
    logger.addHandler(file_handler)

    # pythonw.exe has no console, so sys.stderr is None. Skip StreamHandler to
    # avoid AttributeError on every log call under the GUI entry point.
    if sys.stderr is not None:
        console_formatter = logging.Formatter(
            fmt="[%(asctime)s][%(threadName)s] %(message)s",
            datefmt="%H:%M:%S",
        )
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)

    return logger
