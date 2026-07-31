from __future__ import annotations

import logging
import threading
from typing import Optional

from PySide6.QtCore import QObject, QTimer, Qt, Signal
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


class RuntimeExitBridge(QObject):
    runtime_stopped = Signal()


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
        self._runtime_exit_bridge = RuntimeExitBridge()
        self._runtime_exit_bridge.runtime_stopped.connect(
            self._on_runtime_stopped,
            Qt.ConnectionType.QueuedConnection,
        )
        self._active_action = ""
        self._speaker_on_hint: Optional[bool] = None
        self._identity_poll_lock = threading.Lock()
        self._controller_bridge = ControllerEventBridge(controller)
        self._controller_bridge.identity_changed.connect(self._on_identity_changed)
        self._controller_bridge.speaker_state_changed.connect(self._on_speaker_state_changed)
        self._controller_bridge.power_action_started.connect(self._on_power_action_started)
        self._controller_bridge.power_action_finished.connect(self._on_power_action_finished)

        self._window = KefMainWindow(config, controller, config_store, self._controller_bridge, log_handler)
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

    def _start_controller_action(self, action: str, reason: str, thread_name: str) -> None:
        start_background_task(
            thread_name,
            lambda: self._controller.run_user_power_action(action, reason),
            log=self._log,
        )

    def _do_wake(self) -> None:
        self._start_controller_action("wake", "ui_tray", "TrayWake")

    def _do_standby(self) -> None:
        self._start_controller_action("standby", "ui_tray", "TrayStandby")

    def start(self) -> None:
        thread = start_background_task("HeadlessRuntime", self._runtime.run, log=self._log)

        def _watch():
            if thread is None:
                return
            thread.join()
            self._log.info("Headless runtime stopped; requesting UI shutdown")
            self._runtime_exit_bridge.runtime_stopped.emit()

        start_background_task("RuntimeWatcher", _watch, log=self._log)

        self._tray.show()
        self._refresh_icon()

    def _refresh_icon(self) -> None:
        if self._controller.is_power_action_active():
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

        speaker_on = self._speaker_on_hint if self._speaker_on_hint is not None else available
        if not ip or speaker_on:
            status_text = display
        elif self._speaker_on_hint is False:
            status_text = f"{display} (Standby)"
        else:
            status_text = f"{display} (Offline)"
        self._status_action.setText(status_text)

        if ip and speaker_on:
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
            "EARLY_STANDBY": "Preparing Standby",
            "ENDSESSION_STANDBY": "Processing Shutdown Standby",
        }.get(action, action or "Working")

    def _on_identity_changed(self, _identity: object) -> None:
        self._refresh_icon()

    def _on_speaker_state_changed(
        self,
        _input_source: object,
        _volume: object,
        speaker_on: object,
    ) -> None:
        if speaker_on is not None:
            self._speaker_on_hint = bool(speaker_on)
        self._refresh_icon()

    def _on_power_action_started(self, action: str, _reason: str) -> None:
        self._active_action = action
        self._refresh_icon()

    def _on_power_action_finished(self, _action: str, _reason: str, _success: bool, _outcome: str) -> None:
        if not self._controller.is_power_action_active():
            self._active_action = ""
        self._refresh_icon()

    def _poll_external_identity(self) -> None:
        if not self._window.isVisible():
            return
        if self._controller.is_power_action_active():
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
        self._shutdown_ui(
            "Exit was requested from the tray menu; stopping the runtime and closing the UI",
            stop_runtime=True,
        )

    def _on_runtime_stopped(self) -> None:
        self._shutdown_ui("Headless runtime stopped; closing the UI", stop_runtime=False)

    def _shutdown_ui(self, message: str, *, stop_runtime: bool) -> None:
        if self._is_exiting:
            self._log.info(f"{message}; UI shutdown is already in progress")
            QTimer.singleShot(0, self._app.quit)
            return

        self._is_exiting = True
        self._log.info(message)
        self._identity_poll.stop()
        self._controller.stop_speaker_event_monitor()
        self._tray.hide()
        self._window.hide()
        self._window.dispose()
        self._controller_bridge.dispose()
        if stop_runtime:
            self._runtime.request_stop()
        QTimer.singleShot(0, self._app.quit)
