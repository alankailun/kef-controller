from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QMetaObject, QTimer, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from ..appdata import AppConfig, UserConfigStore
from ..controller import KefPowerController
from ..headless_runtime import HeadlessRuntime
from .icons import icon_connected, icon_disconnected
from .logs import UILogHandler
from .main_window import KefMainWindow


class KefTrayApp:
    def __init__(
        self,
        config: AppConfig,
        controller: KefPowerController,
        config_store: UserConfigStore,
        log: logging.Logger,
        app: QApplication,
        runtime: HeadlessRuntime,
        log_handler: UILogHandler,
    ) -> None:
        self._config = config
        self._controller = controller
        self._config_store = config_store
        self._log = log
        self._log_handler = log_handler
        self._app = app
        self._runtime = runtime
        self._is_exiting = False

        self._window = KefMainWindow(
            self._config, self._controller, self._config_store, self._log_handler
        )

        self._tray = QSystemTrayIcon(icon_disconnected())
        self._tray.setToolTip("KEF Controller")
        self._menu = self._build_menu()
        self._tray.setContextMenu(self._menu)
        self._tray.activated.connect(self._on_activated)

        self._poll = QTimer()
        self._poll.timeout.connect(self._refresh_icon)
        self._poll.setInterval(5000)

    def _get_window(self) -> KefMainWindow:
        return self._window

    def _build_menu(self) -> QMenu:
        menu = QMenu()

        self._status_action = QAction("Initializing...", menu)
        self._status_action.setEnabled(False)
        menu.addAction(self._status_action)
        menu.addSeparator()

        self._open_action = QAction("Open Controller", menu)
        self._open_action.triggered.connect(lambda: self._get_window().toggle())
        menu.addAction(self._open_action)

        menu.addSeparator()

        self._wake_action = QAction("Wake Speaker", menu)
        self._wake_action.triggered.connect(self._do_wake)
        menu.addAction(self._wake_action)

        self._standby_action = QAction("Standby", menu)
        self._standby_action.triggered.connect(self._do_standby)
        menu.addAction(self._standby_action)

        menu.addSeparator()

        self._exit_action = QAction("Exit", menu)
        self._exit_action.triggered.connect(self._on_exit)
        menu.addAction(self._exit_action)

        return menu

    def _do_wake(self) -> None:
        def run():
            gen = self._controller._new_generation("wake", "ui_tray")
            self._controller.wake_kef(gen, "ui_tray")

        threading.Thread(target=run, daemon=True, name="TrayWake").start()

    def _do_standby(self) -> None:
        def run():
            gen = self._controller._new_generation("sleep", "ui_tray")
            self._controller.standby_kef(gen, "ui_tray")

        threading.Thread(target=run, daemon=True, name="TrayStandby").start()

    def start(self) -> None:
        thread = threading.Thread(target=self._runtime.run, daemon=True, name="HeadlessRuntime")
        thread.start()

        def _watch():
            thread.join()
            QMetaObject.invokeMethod(self._app, "quit", Qt.ConnectionType.QueuedConnection)

        threading.Thread(target=_watch, daemon=True, name="RuntimeWatcher").start()

        self._tray.show()
        self._poll.start()
        self._refresh_icon()

    def _refresh_icon(self) -> None:
        identity = self._controller.get_current_identity()
        ip = identity.ip
        name = identity.speaker_name
        model = identity.speaker_model

        if name and model:
            display = f"{name} - {model}"
        elif name or model:
            display = name or model
        elif ip:
            display = ip
        else:
            display = "No device found"

        self._status_action.setText(display)

        if ip:
            self._tray.setIcon(icon_connected())
            self._tray.setToolTip(f"KEF Controller - {display} ({ip})")
        else:
            self._tray.setIcon(icon_disconnected())
            self._tray.setToolTip("KEF Controller - Disconnected")

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._get_window().toggle()

    def _on_exit(self) -> None:
        if self._is_exiting:
            return

        self._is_exiting = True
        self._log.info("Exit was requested from the tray menu; stopping the runtime and closing the UI")
        self._poll.stop()
        self._tray.hide()
        self._window.hide()
        self._runtime.request_stop()
        QTimer.singleShot(0, self._app.quit)
