from __future__ import annotations

import os
from typing import Optional

from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QHBoxLayout, QPlainTextEdit, QVBoxLayout, QWidget
from qfluentwidgets import FluentIcon as FIF, PushButton, SubtitleLabel

from ...config import AppConfig
from .log_handler import UILogHandler
from .log_history import merge_recent_lines, read_log_tail_lines, should_hide_from_ui_log

_LOG_STYLE = """
QPlainTextEdit {
    background: #1a1b26;
    color: #c0caf5;
    font-family: Consolas, "Courier New", monospace;
    font-size: 11px;
    border-radius: 6px;
    padding: 6px;
    selection-background-color: #3d59a1;
    border: none;
}
"""


class LogInterface(QWidget):
    def __init__(self, config: AppConfig, log_handler: UILogHandler, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("logInterface")
        self._log_dir = config.log_dir
        self._log_file = config.log_file
        self._log_handler = log_handler

        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 20, 36, 20)
        layout.setSpacing(12)

        header = QHBoxLayout()
        header.addWidget(SubtitleLabel("Application Log"))
        header.addStretch()
        reload_btn = PushButton("Reload", icon=FIF.SYNC)
        reload_btn.clicked.connect(self._reload_history)
        header.addWidget(reload_btn)
        open_btn = PushButton("Open Log Folder", icon=FIF.FOLDER)
        open_btn.clicked.connect(self._open_log_folder)
        header.addWidget(open_btn)
        layout.addLayout(header)

        self._log_view = QPlainTextEdit()
        self._log_view.setObjectName("log_view")
        self._log_view.setReadOnly(True)
        self._log_view.setMaximumBlockCount(500)
        self._log_view.setStyleSheet(_LOG_STYLE)
        layout.addWidget(self._log_view)

        self._reload_history()
        self._log_handler.emitter.new_line.connect(self._append)

    def _open_log_folder(self) -> None:
        os.makedirs(self._log_dir, exist_ok=True)
        try:
            os.startfile(self._log_dir)
        except OSError:
            pass

    def _reload_history(self) -> None:
        lines = merge_recent_lines(
            read_log_tail_lines(self._log_file, max_lines=400),
            self._log_handler.snapshot_lines(),
            max_lines=500,
        )
        lines = [line for line in lines if not should_hide_from_ui_log(line)]
        self._log_view.setPlainText("\n".join(lines))
        self._log_view.moveCursor(QTextCursor.MoveOperation.End)

    def _append(self, line: str) -> None:
        if should_hide_from_ui_log(line):
            return
        self._log_view.appendPlainText(line)
        self._log_view.moveCursor(QTextCursor.MoveOperation.End)
