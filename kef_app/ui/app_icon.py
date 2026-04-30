from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication


APP_USER_MODEL_ID = "ZhongwenLaw.KEFController"
APP_ICON_RELATIVE_PATH = Path("installer") / "assets" / "setup-icon.ico"


def configure_windows_app_user_model_id() -> None:
    if sys.platform != "win32":
        return

    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        # The icon still works without this; AppUserModelID just makes taskbar grouping steadier.
        return


def app_icon_path() -> Path:
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return bundle_root / APP_ICON_RELATIVE_PATH


def load_app_icon() -> QIcon:
    return QIcon(str(app_icon_path()))


def apply_application_icon(app: QApplication) -> QIcon:
    icon = load_app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)
    return icon
