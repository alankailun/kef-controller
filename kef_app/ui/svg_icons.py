from __future__ import annotations

import sys
from enum import Enum
from pathlib import Path

from qfluentwidgets import FluentIconBase, Theme, getIconColor


def _icons_dir() -> Path:
    """Directory holding the bundled Fluent SVG icon files (dev and frozen)."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / "kef_app" / "ui" / "assets" / "icons"
    return Path(__file__).resolve().parent / "assets" / "icons"


class AppIcon(FluentIconBase, Enum):
    """Custom icons from Microsoft Fluent UI System Icons (MIT).

    Each value has a `<name>_black.svg` and `<name>_white.svg` variant so the
    icon follows the light/dark theme exactly like the built-in FluentIcon set.
    """

    LOCK_CLOSED = "lock_closed"
    LOCK_OPEN = "lock_open"
    BEAKER = "beaker"
    DESKTOP = "desktop"
    DESKTOP_OFF = "desktop_off"

    def path(self, theme: Theme = Theme.AUTO) -> str:
        return str(_icons_dir() / f"{self.value}_{getIconColor(theme)}.svg")
