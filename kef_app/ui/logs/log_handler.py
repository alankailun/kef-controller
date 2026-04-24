from __future__ import annotations

from collections import deque
import logging
import threading

from PySide6.QtCore import QObject, Signal

from .log_history import should_hide_from_ui_log


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
        self.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if should_hide_from_ui_log(record.getMessage()):
                return
            line = self.format(record)
            with self._history_lock:
                self._recent_lines.append(line)
            self.emitter.new_line.emit(line)
        except Exception:
            self.handleError(record)

    def snapshot_lines(self) -> list[str]:
        with self._history_lock:
            return list(self._recent_lines)
