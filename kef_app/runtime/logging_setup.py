from __future__ import annotations

import atexit
import logging
import os
import queue
import sys
from typing import Iterable
from logging.handlers import QueueHandler, QueueListener, TimedRotatingFileHandler

from ..config import AppConfig


_LOGGER_NAME = "kef_controller"
_LISTENER_ATTR = "_kef_queue_listener"
_LISTENER_STOPPED_ATTR = "_kef_queue_listener_stopped"


def _stop_listener(listener: QueueListener) -> None:
    if getattr(listener, _LISTENER_STOPPED_ATTR, False):
        return
    listener.stop()
    setattr(listener, _LISTENER_STOPPED_ATTR, True)


def shutdown_logger(logger: logging.Logger | None = None) -> None:
    logger = logger or logging.getLogger(_LOGGER_NAME)
    listener = getattr(logger, _LISTENER_ATTR, None)
    if listener is not None:
        _stop_listener(listener)
        for handler in listener.handlers:
            try:
                handler.flush()
            finally:
                handler.close()
        setattr(logger, _LISTENER_ATTR, None)


def build_logger(config: AppConfig, *, extra_handlers: Iterable[logging.Handler] = ()) -> logging.Logger:
    os.makedirs(config.log_dir, exist_ok=True)

    file_formatter = logging.Formatter(
        fmt="[%(asctime)s][%(threadName)s][%(levelname)s] %(message)s",
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

    logger = logging.getLogger(_LOGGER_NAME)
    shutdown_logger(logger)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    listener_handlers: list[logging.Handler] = [file_handler]

    # pythonw.exe has no console, so sys.stderr is None. Skip StreamHandler to
    # avoid AttributeError on every log call under the GUI entry point.
    if sys.stderr is not None:
        console_formatter = logging.Formatter(
            fmt="[%(asctime)s][%(threadName)s][%(levelname)s] %(message)s",
            datefmt="%H:%M:%S",
        )
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(console_formatter)
        listener_handlers.append(console_handler)

    listener_handlers.extend(extra_handlers)

    log_queue = queue.SimpleQueue()
    queue_handler = QueueHandler(log_queue)
    listener = QueueListener(log_queue, *listener_handlers, respect_handler_level=True)
    listener.start()
    setattr(listener, _LISTENER_STOPPED_ATTR, False)
    setattr(logger, _LISTENER_ATTR, listener)
    logger.addHandler(queue_handler)
    atexit.register(shutdown_logger, logger)

    return logger
