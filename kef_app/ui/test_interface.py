from __future__ import annotations

import threading
from typing import Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    InfoBar,
    InfoBarPosition,
    PushButton,
    ScrollArea,
    SubtitleLabel,
    TitleLabel,
)

from ..appdata import AppConfig
from ..controller import KefPowerController
from .settings import SPEAKER_POWER_OPTIONS, get_speaker_power_disabled_reason


class TestInterface(ScrollArea):
    _test_finished = Signal(str, bool, str)

    def __init__(
        self,
        config: AppConfig,
        controller: KefPowerController,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("testInterface")
        self._config = config
        self._controller = controller
        self._log = controller.log
        self._active_tests = 0
        self._active_label: Optional[str] = None
        self._buttons: list[PushButton] = []
        self._buttons_by_label: dict[str, PushButton] = {}
        self._value_labels: dict[str, BodyLabel] = {}

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(36, 20, 36, 36)
        layout.setSpacing(16)

        layout.addWidget(TitleLabel("Event Tests"))
        layout.addWidget(
            BodyLabel(
                "Use these buttons to simulate the same speaker actions that normally happen during startup, "
                "shutdown, lock, unlock, and sleep."
            )
        )
        layout.addWidget(self._build_current_behavior_card())
        layout.addWidget(self._build_test_card())
        layout.addStretch()

        self.setWidget(container)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._test_finished.connect(self._on_test_finished)
        self.refresh()

    def _build_current_behavior_card(self) -> CardWidget:
        card = CardWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(8)
        layout.addWidget(SubtitleLabel("Current Speaker Power Behavior"))

        for option in SPEAKER_POWER_OPTIONS:
            row = QHBoxLayout()
            row.addWidget(BodyLabel(f"{option.title}:"))
            row.addStretch()
            value_label = BodyLabel("")
            row.addWidget(value_label)
            layout.addLayout(row)
            self._value_labels[option.key] = value_label

        return card

    def _build_test_card(self) -> CardWidget:
        card = CardWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)
        layout.addWidget(SubtitleLabel("Run Events"))

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)

        buttons = [
            ("Startup", self._test_startup),
            ("Shutdown", self._test_shutdown),
            ("Lock", self._test_lock),
            ("Unlock", self._test_unlock),
            ("Sleep", self._test_suspend),
        ]

        for index, (label, handler) in enumerate(buttons):
            button = PushButton(label)
            button.setMinimumHeight(40)
            button.clicked.connect(handler)
            if label == "Sleep":
                grid.addWidget(button, 2, 0, 1, 2)
            else:
                grid.addWidget(button, index // 2, index % 2)
            self._buttons.append(button)
            self._buttons_by_label[label] = button

        layout.addLayout(grid)
        self._event_status = CaptionLabel("Choose a test to simulate a Windows event.")
        layout.addWidget(self._event_status)
        layout.addWidget(
            BodyLabel(
                "These tests use the live configuration. Startup, shutdown, lock, unlock, and sleep each simulate "
                "their matching Windows event."
            )
        )
        return card

    def refresh(self) -> None:
        for option in SPEAKER_POWER_OPTIONS:
            self._value_labels[option.key].setText(self._bool_text(bool(getattr(self._config, option.key))))

    @staticmethod
    def _bool_text(value: bool) -> str:
        return "On" if value else "Off"

    def _set_button_states(self) -> None:
        for label, button in self._buttons_by_label.items():
            if self._active_label == label:
                button.setText(f"{label} (Running...)")
                button.setEnabled(False)
            else:
                button.setText(label)
                button.setEnabled(self._active_label is None)

    def _start_test(
        self,
        label: str,
        runner: Callable[[], None],
        *,
        enabled: Optional[bool] = None,
        disabled_reason: str = "",
    ) -> None:
        self.refresh()
        if enabled is False:
            self._log.info(f"TEST_EVENT_SKIPPED | label={label} | reason={disabled_reason}")
            InfoBar.warning(
                "Test Skipped",
                disabled_reason,
                duration=3500,
                parent=self.window(),
                position=InfoBarPosition.TOP_RIGHT,
            )
            self._event_status.setText(f"Skipped: {label}.")
            return

        self._active_tests += 1
        self._active_label = label
        self._set_button_states()
        self._event_status.setText(f"Running: {label}...")
        self._log.info(f"TEST_EVENT_BEGIN | label={label}")

        def worker() -> None:
            ok = True
            detail = "The test event was sent."
            try:
                runner()
            except Exception as exc:
                ok = False
                detail = str(exc)
                self._log.info(f"TEST_EVENT_FAILED | label={label} | error={detail}")
            finally:
                self._test_finished.emit(label, ok, detail)

        threading.Thread(target=worker, daemon=True, name=f"UITest-{label.replace(' ', '')}").start()

    def _on_test_finished(self, label: str, ok: bool, detail: str) -> None:
        self._active_tests = max(0, self._active_tests - 1)
        if self._active_tests == 0:
            self._active_label = None
        self._set_button_states()

        if ok:
            self._log.info(f"TEST_EVENT_END | label={label} | status=queued_or_completed")
            self._event_status.setText(f"Sent: {label}. Check the log for the full action trace.")
            InfoBar.success(
                "Test Started",
                f"{label} was sent. Check the log for the full action trace.",
                duration=3000,
                parent=self.window(),
                position=InfoBarPosition.TOP_RIGHT,
            )
            return

        self._event_status.setText(f"Failed: {label}.")
        InfoBar.error(
            "Test Failed",
            f"{label} could not be started. {detail}",
            duration=4000,
            parent=self.window(),
            position=InfoBarPosition.TOP_RIGHT,
        )

    def _test_startup(self) -> None:
        self._start_test(
            "Startup",
            self._controller.on_startup,
            enabled=self._config.wake_on_startup,
            disabled_reason=get_speaker_power_disabled_reason("wake_on_startup"),
        )

    def _test_suspend(self) -> None:
        self._start_test(
            "Sleep",
            lambda: self._controller.on_suspend("UI_TEST_SUSPEND"),
            enabled=self._config.standby_on_sleep,
            disabled_reason=get_speaker_power_disabled_reason("standby_on_sleep"),
        )

    def _test_lock(self) -> None:
        self._start_test(
            "Lock",
            lambda: self._controller.on_lock("UI_TEST_LOCK"),
            enabled=self._config.standby_on_lock,
            disabled_reason=get_speaker_power_disabled_reason("standby_on_lock"),
        )

    def _test_unlock(self) -> None:
        self._start_test(
            "Unlock",
            lambda: self._controller.on_unlock("UI_TEST_UNLOCK"),
            enabled=self._config.wake_on_unlock_only,
            disabled_reason=get_speaker_power_disabled_reason("wake_on_unlock_only"),
        )

    def _test_shutdown(self) -> None:
        self._start_test(
            "Shutdown",
            lambda: self._controller.standby_kef_end_session("UI_TEST_ENDSESSION", "UI_TEST"),
            enabled=self._config.endsession_standby_on_shutdown,
            disabled_reason=get_speaker_power_disabled_reason("endsession_standby_on_shutdown"),
        )
