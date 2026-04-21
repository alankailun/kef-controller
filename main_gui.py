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

from kef_app.appdata import AppConfig, UserConfigStore, SpeakerStateStore
from kef_app.controller import KefPowerController
from kef_app.headless_runtime import HeadlessRuntime
from kef_app.logging_setup import build_logger
from kef_app.ui import KefTrayApp
from kef_app.ui.logs import UILogHandler
from kef_app.platform_windows import (
    ensure_single_instance,
    register_application_restart,
    ensure_startup_registration,
    maybe_handle_startup_task_repair,
)


def main() -> None:
    repair_exit_code = maybe_handle_startup_task_repair()
    if repair_exit_code is not None:
        raise SystemExit(repair_exit_code)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("KEF Controller")

    setTheme(Theme.AUTO)

    base_config = AppConfig()
    user_config_store = UserConfigStore(base_config)
    config = user_config_store.load_or_create()

    log_handler = UILogHandler()
    log = build_logger(config)
    log.addHandler(log_handler)

    for msg in user_config_store.drain_startup_messages():
        log.info(msg)

    ensure_startup_registration(config.app_name, log, mode=config.startup_registration_mode)

    _mutex = ensure_single_instance(log, config.single_instance_mutex_name)

    state_store = SpeakerStateStore(config, log)
    controller = KefPowerController(config, log, state_store=state_store)
    register_application_restart(log, config.enable_application_restart, config.application_restart_flags)

    runtime = HeadlessRuntime(config, controller, log)
    tray = KefTrayApp(config, controller, user_config_store, log, app, runtime, log_handler)
    tray.start()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
