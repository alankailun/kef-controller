from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap


def _make_large_pixmap(accent: str, body: str) -> QPixmap:
    size = 64
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)

    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    accent_color = QColor(accent)
    body_color = QColor(body)
    shadow_color = QColor(0, 0, 0, 72)
    cone_color = QColor("#f4efe4")
    line_color = QColor("#2b333b")

    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(shadow_color)
    p.drawRoundedRect(15, 8, 36, 50, 14, 14)

    p.setBrush(body_color)
    p.drawRoundedRect(13, 6, 36, 50, 14, 14)

    p.setPen(QPen(QColor("#29313a"), 2))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(14, 7, 34, 48, 13, 13)

    p.setPen(QPen(accent_color, 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawArc(19, 12, 24, 24, 34 * 16, 250 * 16)

    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(line_color)
    p.drawEllipse(17, 15, 28, 28)

    p.setBrush(cone_color)
    p.drawEllipse(21, 19, 20, 20)

    p.setBrush(body_color)
    p.drawEllipse(27, 25, 8, 8)

    p.setPen(QPen(accent_color, 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(28, 48, 34, 48)

    wave = QPainterPath()
    wave.moveTo(46, 23)
    wave.cubicTo(53, 29, 53, 35, 46, 41)
    p.setPen(QPen(accent_color, 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawPath(wave)

    p.end()
    return pix


def _make_small_pixmap(accent: str, body: str) -> QPixmap:
    pix = QPixmap(16, 16)
    pix.fill(Qt.GlobalColor.transparent)

    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    accent_color = QColor(accent)
    body_color = QColor(body)

    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(body_color)
    p.drawRoundedRect(2, 1, 11, 14, 4, 4)

    p.setPen(QPen(accent_color, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawArc(4, 3, 7, 7, 40 * 16, 235 * 16)

    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#f4efe4"))
    p.drawEllipse(5, 5, 5, 5)

    p.setBrush(body_color)
    p.drawEllipse(7, 7, 2, 2)

    p.setPen(QPen(accent_color, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(7, 13, 9, 13)

    p.drawArc(10, 5, 5, 6, -48 * 16, 96 * 16)

    p.end()
    return pix


def _make_icon(accent: str, body: str = "#151a1f") -> QIcon:
    icon = QIcon()
    icon.addPixmap(_make_large_pixmap(accent, body))
    icon.addPixmap(_make_small_pixmap(accent, body))
    return icon


def icon_connected() -> QIcon:
    return _make_icon("#22c7b8")  # Teal: connected


def icon_working() -> QIcon:
    return _make_icon("#f2a33a")  # Amber: busy


def icon_disconnected() -> QIcon:
    return _make_icon("#8c949e", "#1a1f25")  # Gray: disconnected
