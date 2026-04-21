from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtCore import QSize
from PySide6.QtGui import QCloseEvent, QGuiApplication, QShowEvent
from qfluentwidgets import FluentIcon as FIF, FluentWindow, NavigationItemPosition

from ..appdata import AppConfig, UserConfigStore
from ..controller import KefPowerController
from .home_interface import HomeInterface
from .logs import LogInterface, UILogHandler
from .settings import SettingsInterface
from .test_interface import TestInterface


class KefMainWindow(FluentWindow):
    _DEFAULT_WIDTH = 980
    _DEFAULT_HEIGHT = 720

    def __init__(
        self,
        config: AppConfig,
        controller: KefPowerController,
        config_store: UserConfigStore,
        log_handler: UILogHandler,
    ) -> None:
        super().__init__()
        self._config = config
        self._controller = controller

        self.setWindowTitle("KEF Controller")
        self.resize(self._DEFAULT_WIDTH, self._DEFAULT_HEIGHT)
        self.setMinimumSize(700, 560)
        self._has_positioned_once = False

        self._home = HomeInterface(config, controller, config_store, log_handler, self)
        self._test_iface = TestInterface(config, controller, self)
        self._log_iface = LogInterface(config, log_handler, self)
        self._settings_iface = SettingsInterface(config, config_store, self)

        self.addSubInterface(self._home, FIF.HOME, "Home")
        self.addSubInterface(self._test_iface, FIF.SPEED_HIGH, "Tests")
        self.addSubInterface(self._log_iface, FIF.DOCUMENT, "Log")
        self.addSubInterface(
            self._settings_iface, FIF.SETTING, "Settings",
            NavigationItemPosition.BOTTOM,
        )

        self._poll = QTimer(self)
        self._poll.timeout.connect(self._refresh_pages)
        self._poll.setInterval(3000)
        self._poll.start()
        self._refresh_pages()

    def _on_wake(self) -> None:
        self._home.on_wake()

    def _on_standby(self) -> None:
        self._home.on_standby()

    def _refresh_pages(self) -> None:
        self._home.refresh()
        self._test_iface.refresh()

    def closeEvent(self, event: QCloseEvent) -> None:
        event.ignore()
        self.hide()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if not self._has_positioned_once:
            self._has_positioned_once = True
            QTimer.singleShot(0, lambda: self._restore_reasonable_geometry(force=True))

    def _available_geometry(self):
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return None
        return screen.availableGeometry()

    def _target_window_size(self) -> QSize:
        available = self._available_geometry()
        if available is None:
            return QSize(self._DEFAULT_WIDTH, self._DEFAULT_HEIGHT)

        desired_width = min(self._DEFAULT_WIDTH, int(available.width() * 0.72))
        desired_height = min(self._DEFAULT_HEIGHT, int(available.height() * 0.80))
        width = min(max(self.minimumWidth(), desired_width), available.width())
        height = min(max(self.minimumHeight(), desired_height), available.height())
        return QSize(width, height)

    def _restore_reasonable_geometry(self, force: bool = False) -> None:
        available = self._available_geometry()
        if available is None:
            return

        frame = self.frameGeometry()
        too_small = self.width() < 760 or self.height() < 600
        off_screen = not available.intersects(frame)
        if not force and not too_small and not off_screen:
            return

        self.resize(self._target_window_size())
        frame = self.frameGeometry()
        frame.moveCenter(available.center())
        self.move(frame.topLeft())

    def toggle(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            if self.isMinimized():
                self.showNormal()
            else:
                self.show()
            self._restore_reasonable_geometry()
            self.raise_()
            self.activateWindow()
