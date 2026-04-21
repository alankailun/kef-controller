from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap


def _make_icon(bg: str) -> QIcon:
    size = 64
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(bg))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(4, 4, size - 8, size - 8)
    p.setPen(QColor("#ffffff"))
    font = QFont("Arial", 26, QFont.Weight.Bold)
    p.setFont(font)
    p.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, "K")
    p.end()
    return QIcon(pix)


def icon_connected() -> QIcon:
    return _make_icon("#27ae60")   # Green: connected


def icon_disconnected() -> QIcon:
    return _make_icon("#7f8c8d")   # Gray: disconnected
