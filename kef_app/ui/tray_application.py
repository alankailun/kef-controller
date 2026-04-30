from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QMetaObject, QTimer, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from ..config import AppConfig
from ..storage import UserConfigStore
from ..controller import KefPowerController
from ..runtime.headless_service import HeadlessRuntime
from .background_tasks import start_background_task
from .controller_events import ControllerEventBridge
from .icons import icon_connected, icon_disconnected, icon_working
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
        self._log = log
        self._app = app
        self._runtime = runtime
        self._is_exiting = False
        self._active_power_actions = 0
        self._active_action = ""
        self._identity_poll_lock = threading.Lock()
        self._controller_bridge = ControllerEventBridge(controller)
        self._controller_bridge.identity_changed.connect(self._on_identity_changed)
        self._controller_bridge.power_action_started.connect(self._on_power_action_started)
        self._controller_bridge.power_action_finished.connect(self._on_power_action_finished)

        self._window = KefMainWindow(config, controller, config_store, self._controller_bridge, log_handler)
        app_icon = self._app.windowIcon()
        if not app_icon.isNull():
            self._window.setWindowIcon(app_icon)
        self._window.visibility_changed.connect(self._on_window_visibility_changed)

        self._tray = QSystemTrayIcon(icon_disconnected())
        self._tray.setToolTip("KEF Controller")
        self._menu = self._build_menu()
        self._tray.setContextMenu(self._menu)
        self._tray.activated.connect(self._on_activated)

        self._identity_poll = QTimer()
        self._identity_poll.timeout.connect(self._poll_external_identity)
        self._apply_polling_config()

    def _build_menu(self) -> QMenu:
        menu = QMenu()

        self._status_action = QAction("Initializing...", menu)
        self._status_action.setEnabled(False)
        menu.addAction(self._status_action)
        menu.addSeparator()

        self._open_action = QAction("Open Controller", menu)
        self._open_action.triggered.connect(self._window.toggle)
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

    def _start_controller_action(self, desired_state: str, runner, reason: str, thread_name: str) -> None:
        def run():
            generation = self._controller._new_generation(desired_state, reason)
            runner(generation, reason)

        start_background_task(thread_name, run, log=self._log)

    def _do_wake(self) -> None:
        self._start_controller_action("wake", self._controller.wake_kef, "ui_tray", "TrayWake")

    def _do_standby(self) -> None:
        self._start_controller_action("sleep", self._controller.standby_kef, "ui_tray", "TrayStandby")

    def start(self) -> None:
        thread = start_background_task("HeadlessRuntime", self._runtime.run, log=self._log)

        def _watch():
            if thread is None:
                return
            thread.join()
            QMetaObject.invokeMethod(self._app, "quit", Qt.ConnectionType.QueuedConnection)

        start_background_task("RuntimeWatcher", _watch, log=self._log)

        self._tray.show()
        self._refresh_icon()

    def _refresh_icon(self) -> None:
        if self._active_power_actions > 0:
            action_label = self._format_action_label(self._active_action)
            self._status_action.setText(f"{action_label}...")
            self._tray.setIcon(icon_working())
            self._tray.setToolTip(f"KEF Controller - Working ({action_label})")
            return

        identity = self._controller.get_current_identity()
        ip = identity.ip
        available = identity.available
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

        status_text = display if available or not ip else f"{display} (Offline)"
        self._status_action.setText(status_text)

        if ip and available:
            self._tray.setIcon(icon_connected())
            self._tray.setToolTip(f"KEF Controller - {display} ({ip})")
        else:
            self._tray.setIcon(icon_disconnected())
            if ip:
                self._tray.setToolTip(f"KEF Controller - Offline ({ip})")
            else:
                self._tray.setToolTip("KEF Controller - Disconnected")

    @staticmethod
    def _format_action_label(action: str) -> str:
        return {
            "WAKE": "Waking Speaker",
            "STANDBY": "Putting Speaker in Standby",
            "LOCK_PRE_STANDBY": "Preparing Standby",
            "ENDSESSION_STANDBY": "Processing Shutdown Standby",
        }.get(action, action or "Working")

    def _on_identity_changed(self, _identity: object) -> None:
        self._refresh_icon()

    def _on_power_action_started(self, action: str, _reason: str) -> None:
        self._active_power_actions += 1
        self._active_action = action
        self._refresh_icon()

    def _on_power_action_finished(self, _action: str, _reason: str, _success: bool, _outcome: str) -> None:
        self._active_power_actions = max(0, self._active_power_actions - 1)
        if self._active_power_actions == 0:
            self._active_action = ""
        self._refresh_icon()

    def _poll_external_identity(self) -> None:
        if not self._window.isVisible():
            return
        if self._active_power_actions > 0:
            return
        if not self._controller.get_current_kef_ip():
            return

        def run() -> None:
            self._controller.probe_external_identity("ui_tray_poll", "ui_tray_poll")

        start_background_task(
            "TrayIdentityPoll",
            run,
            lock=self._identity_poll_lock,
            log=self._log,
        )

    def _apply_polling_config(self) -> None:
        self._identity_poll.setInterval(max(1000, int(self._config.tray_identity_poll_interval * 1000)))

    def _on_window_visibility_changed(self, visible: bool) -> None:
        if visible:
            self._identity_poll.start()
            self._refresh_icon()
            return
        self._identity_poll.stop()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._window.toggle()

    def _on_exit(self) -> None:
        if self._is_exiting:
            return

        self._is_exiting = True
        self._log.info("Exit was requested from the tray menu; stopping the runtime and closing the UI")
        self._identity_poll.stop()
        self._tray.hide()
        self._window.hide()
        self._controller_bridge.dispose()
        self._runtime.request_stop()
        QTimer.singleShot(0, self._app.quit)
