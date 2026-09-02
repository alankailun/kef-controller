from __future__ import annotations

import logging
import threading
from collections import deque

from PySide6.QtCore import QObject, Signal


class _Emitter(QObject):
    new_line = Signal(str)


class UILogHandler(logging.Handler):
    """Logging handler that emits each formatted line as a Qt signal.

    Safe to emit from any thread; connected slots run in the receiver's thread.
    """

    def __init__(self) -> None:
        super().__init__()
        self.emitter = _Emitter()
        self._history_lock = threading.Lock()
        self._recent_lines: deque[str] = deque(maxlen=400)
        # Keep the live UI stream byte-for-byte compatible with the file log.
        # This lets the history merger recognize its overlap and gives the web
        # renderer an authoritative severity token instead of guessing it from
        # message text.
        self.setFormatter(logging.Formatter(
            "[%(asctime)s][%(threadName)s][%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
            with self._history_lock:
                self._recent_lines.append(line)
            self.emitter.new_line.emit(line)
        except Exception:
            self.handleError(record)

    def snapshot_lines(self) -> list[str]:
        with self._history_lock:
            return list(self._recent_lines)
