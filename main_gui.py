"""
KEF Controller - GUI entry point (system tray + main window)

Run (no console window):
    pythonw main_gui.py

Package as .exe:
    pyinstaller --noconsole --onefile --name "KEF Controller" main_gui.py
"""
from __future__ import annotations

import os
import sys


def _run_webview_host(url: str) -> None:
    import webview

    webview.create_window(
        "KEF Controller",
        url,
        width=1280,
        height=860,
        min_size=(840, 620),
        background_color="#f4f5f6",
        text_select=True,
    )
    webview.start(gui="edgechromium", debug=False)


if "--webview-host" in sys.argv:
    host_index = sys.argv.index("--webview-host")
    if host_index + 1 >= len(sys.argv):
        raise SystemExit("Missing WebView host URL")
    _run_webview_host(sys.argv[host_index + 1])
    raise SystemExit(0)


if "--webview2-import-check" in sys.argv:
    import webview

    assert webview
    raise SystemExit(0)

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from kef_app.runtime.bootstrap import build_runtime_context, exit_if_startup_task_repair_requested
from kef_app.runtime.headless_service import HeadlessRuntime
from kef_app.ui import KefTrayApp
from kef_app.ui.app_icon import apply_application_icon, configure_windows_app_user_model_id
from kef_app.ui.logs import UILogHandler


def main() -> None:
    exit_if_startup_task_repair_requested()
    configure_windows_app_user_model_id()

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("KEF Controller")
    app.setFont(QFont("Segoe UI Variable Text", 10))
    apply_application_icon(app)

    log_handler = UILogHandler()
    runtime_context = build_runtime_context(extra_log_handlers=[log_handler])

    runtime = HeadlessRuntime(runtime_context.config, runtime_context.controller, runtime_context.log)
    tray = KefTrayApp(
        runtime_context.config,
        runtime_context.controller,
        runtime_context.user_config_store,
        runtime_context.log,
        app,
        runtime,
        log_handler,
    )
    tray.start()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
