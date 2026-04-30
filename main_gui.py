"""
KEF Controller - GUI entry point (system tray + main window)

Run (no console window):
    pythonw main_gui.py

Package as .exe:
    pyinstaller --noconsole --onefile --name "KEF Controller" main_gui.py
"""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication
from qfluentwidgets import Theme, setTheme

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
    apply_application_icon(app)

    setTheme(Theme.AUTO)

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
