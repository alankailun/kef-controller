from __future__ import annotations

import threading
from typing import Callable, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QHideEvent, QShowEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    CaptionLabel,
    ComboBox,
    InfoBar,
    InfoBarPosition,
    PrimaryPushButton,
    PushButton,
    Slider,
    SubtitleLabel,
)

from ..appdata import AppConfig, UserConfigStore
from ..controller import KefPowerController
from ..models import INPUT_SOURCE_OPTIONS, normalize_input_source
from .controller_bridge import ControllerEventBridge

_INPUT_CHOICES = list(INPUT_SOURCE_OPTIONS)
_INPUT_VALUES = [value for _, value in _INPUT_CHOICES]
_BTN_GREEN = "background-color: #1f8f5f; color: white; border: 1px solid #1f8f5f;"
_BTN_DANGER = "background-color: #c2410c; color: white; border: 1px solid #c2410c;"
_BTN_IDLE = ""


class HomeInterface(QWidget):
    _apply_done = Signal(str, bool)
    _external_state_fetched = Signal(object, object, object)
    _vol_fetched = Signal(object)
    _vol_set_done = Signal(bool)

    def __init__(
        self,
        config: AppConfig,
        controller: KefPowerController,
        config_store: UserConfigStore,
        controller_bridge: ControllerEventBridge,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("homeInterface")
        self._config = config
        self._controller = controller
        self._config_store = config_store
        self._working = False
        self._active_power_actions = 0
        self._power_on_hint: Optional[bool] = None
        self._applying_input = False
        self._external_poll_lock = threading.Lock()
        self._last_input = normalize_input_source(config.kef_input)
        self._prev_ip = ""
        self._vol_dragging = False
        self._vol_pending: Optional[int] = None
        self._vol_fetching = False

        self._vol_debounce = QTimer(self)
        self._vol_debounce.setSingleShot(True)
        self._vol_debounce.timeout.connect(self._commit_volume)

        self._external_poll = QTimer(self)
        self._external_poll.timeout.connect(self._poll_external_state)
        self._apply_polling_config()

        self._controller_bridge = controller_bridge
        self._apply_done.connect(self._on_apply_done)
        self._external_state_fetched.connect(self._on_external_state_fetched)
        self._vol_fetched.connect(self._on_vol_fetched)
        self._vol_set_done.connect(self._on_vol_set_done)
        self._controller_bridge.identity_changed.connect(self._on_identity_changed)
        self._controller_bridge.power_action_started.connect(self._on_power_action_started)
        self._controller_bridge.power_action_finished.connect(self._on_power_action_finished)

        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(36, 20, 36, 20)
        root.setSpacing(12)
        root.addWidget(self._build_status_card())
        root.addWidget(self._build_control_card())
        root.addWidget(self._build_info_card())
        root.addStretch()

    def _build_status_card(self) -> CardWidget:
        card = CardWidget()
        row = QHBoxLayout(card)
        row.setContentsMargins(20, 16, 20, 16)
        row.setSpacing(16)

        self._dot = QLabel("●")
        self._dot.setStyleSheet("color: #94a3b8; font-size: 22px;")
        self._dot.setFixedWidth(28)
        row.addWidget(self._dot, alignment=Qt.AlignmentFlag.AlignVCenter)

        col = QVBoxLayout()
        col.setSpacing(2)
        self._status_lbl = SubtitleLabel("Disconnected")
        self._device_lbl = BodyLabel("No device found")
        col.addWidget(self._status_lbl)
        col.addWidget(self._device_lbl)
        row.addLayout(col, stretch=1)

        self._ip_lbl = CaptionLabel("")
        self._ip_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(self._ip_lbl, alignment=Qt.AlignmentFlag.AlignVCenter)

        return card

    def _build_control_card(self) -> CardWidget:
        card = CardWidget()
        vbox = QVBoxLayout(card)
        vbox.setContentsMargins(20, 16, 20, 16)
        vbox.setSpacing(12)

        inp_row = QHBoxLayout()
        inp_row.addWidget(BodyLabel("Input Source"))
        inp_row.addStretch()
        self._input_combo = ComboBox()
        self._input_combo.addItems([label for label, _ in _INPUT_CHOICES])
        cur = normalize_input_source(self._config.kef_input)
        if cur in _INPUT_VALUES:
            self._input_combo.setCurrentIndex(_INPUT_VALUES.index(cur))
        self._input_combo.setFixedWidth(160)
        self._input_combo.setEnabled(False)
        self._input_combo.currentIndexChanged.connect(self._on_input_selected)
        inp_row.addWidget(self._input_combo)
        self._input_feedback = CaptionLabel("")
        self._input_feedback.setFixedWidth(70)
        inp_row.addWidget(self._input_feedback)
        vbox.addLayout(inp_row)

        vol_row = QHBoxLayout()
        vol_row.addWidget(BodyLabel("Volume"))
        vol_row.addStretch()
        self._vol_lbl = CaptionLabel("--")
        self._vol_lbl.setFixedWidth(28)
        self._vol_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._vol_slider = Slider(Qt.Orientation.Horizontal)
        self._vol_slider.setRange(0, 100)
        self._vol_slider.setFixedWidth(220)
        self._vol_slider.sliderPressed.connect(self._on_vol_press)
        self._vol_slider.sliderReleased.connect(self._on_vol_release)
        self._vol_slider.valueChanged.connect(self._on_vol_value_changed)
        vol_row.addWidget(self._vol_lbl)
        vol_row.addSpacing(8)
        vol_row.addWidget(self._vol_slider)
        vbox.addLayout(vol_row)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self._wake_btn = PrimaryPushButton("On")
        self._wake_btn.setMinimumHeight(40)
        self._wake_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._wake_btn.clicked.connect(self.on_wake)
        btn_row.addWidget(self._wake_btn)
        self._standby_btn = PushButton("Off")
        self._standby_btn.setMinimumHeight(40)
        self._standby_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._standby_btn.clicked.connect(self.on_standby)
        btn_row.addWidget(self._standby_btn)
        vbox.addLayout(btn_row)

        return card

    def _build_info_card(self) -> CardWidget:
        card = CardWidget()
        vbox = QVBoxLayout(card)
        vbox.setContentsMargins(20, 16, 20, 16)
        vbox.setSpacing(8)
        vbox.addWidget(SubtitleLabel("Device Info"))

        self._info: dict[str, QLabel] = {}
        for key in ("IP Address", "MAC Address", "Model", "Firmware"):
            row = QHBoxLayout()
            k_lbl = BodyLabel(f"{key}:")
            k_lbl.setFixedWidth(110)
            v_lbl = BodyLabel("—")
            v_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            row.addWidget(k_lbl)
            row.addWidget(v_lbl, stretch=1)
            vbox.addLayout(row)
            self._info[key] = v_lbl

        return card

    def refresh(self) -> None:
        identity = self._controller.get_current_identity()
        ip = identity.ip
        speaker_available = identity.available
        speaker_on = self._resolve_speaker_on(speaker_available)

        if self._working:
            color, status = "#f59e0b", "Working..."
        elif speaker_on:
            color, status = "#22c55e", "Connected"
        elif ip and speaker_available:
            color, status = "#94a3b8", "Standby"
        else:
            color, status = "#94a3b8", "Disconnected"

        self._dot.setStyleSheet(f"color: {color}; font-size: 22px;")
        self._status_lbl.setText(status)

        name, model = identity.speaker_name, identity.speaker_model
        if name and model:
            self._device_lbl.setText(f"{name}  -  {model}")
        elif name or model:
            self._device_lbl.setText(name or model)
        else:
            self._device_lbl.setText("No device found")
        self._ip_lbl.setText(ip or "")

        self._info["IP Address"].setText(ip or "-")
        self._info["MAC Address"].setText(identity.mac_display or identity.mac or "-")
        self._info["Model"].setText(model or "-")
        self._info["Firmware"].setText(identity.firmware_version or "-")

        self._input_combo.setEnabled(speaker_on and not self._working and not self._applying_input)
        self._refresh_action_buttons(speaker_on=speaker_on)

        if speaker_on and ip and not self._prev_ip:
            self._maybe_fetch_volume()
        self._prev_ip = ip or ""

    def request_state_refresh(self) -> None:
        self.refresh()
        self._poll_external_state(force=self.isVisible())

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._external_poll.start()
        QTimer.singleShot(0, self.request_state_refresh)

    def hideEvent(self, event: QHideEvent) -> None:
        super().hideEvent(event)
        self._external_poll.stop()

    def _maybe_fetch_volume(self) -> None:
        if (
            self._vol_dragging
            or self._working
            or self._vol_fetching
            or self._power_on_hint is False
            or not self.isVisible()
        ):
            return
        if not self._controller.get_current_kef_ip():
            return
        self._vol_fetching = True
        threading.Thread(target=self._fetch_volume, daemon=True, name="FetchVolume").start()

    def _fetch_volume(self) -> None:
        try:
            vol = self._controller.get_volume()
            self._vol_fetched.emit(vol)
        finally:
            self._vol_fetching = False

    def _on_vol_fetched(self, vol: Optional[int]) -> None:
        if vol is not None and not self._vol_dragging:
            self._vol_slider.setValue(vol)

    def _poll_external_state(self, force: bool = False) -> None:
        if self._working or self._applying_input or self._vol_dragging:
            return
        if not force and not self.isVisible():
            return
        if not self._controller.get_current_kef_ip():
            return
        if not self._external_poll_lock.acquire(blocking=False):
            return

        def run() -> None:
            try:
                input_source, volume, speaker_on = self._controller.poll_external_ui_state("ui_home_poll", "ui_home_poll")
                self._external_state_fetched.emit(input_source, volume, speaker_on)
            finally:
                self._external_poll_lock.release()

        threading.Thread(target=run, daemon=True, name="PollExternalState").start()

    def _apply_polling_config(self) -> None:
        self._external_poll.setInterval(max(250, int(self._config.home_external_poll_interval * 1000)))

    def _on_external_state_fetched(
        self,
        input_source: Optional[str],
        volume: Optional[int],
        speaker_on: Optional[bool],
    ) -> None:
        if input_source and input_source in _INPUT_VALUES and input_source != self._last_input:
            self._last_input = input_source
            self._input_combo.blockSignals(True)
            self._input_combo.setCurrentIndex(_INPUT_VALUES.index(input_source))
            self._input_combo.blockSignals(False)

        if volume is not None and not self._vol_dragging:
            self._vol_slider.setValue(volume)

        current_ip = self._controller.get_current_kef_ip()
        if current_ip and speaker_on is not None:
            self._power_on_hint = speaker_on

        self.refresh()

    def _on_input_selected(self, idx: int) -> None:
        new_input = _INPUT_VALUES[idx]
        if new_input == self._last_input:
            return
        self._applying_input = True
        self._input_combo.setEnabled(False)
        self._input_feedback.setText("Applying...")

        def run():
            ok = self._controller.change_input_live(new_input)
            self._apply_done.emit(new_input, ok)

        threading.Thread(target=run, daemon=True, name="UIChangeInput").start()

    def _on_apply_done(self, new_input: str, ok: bool) -> None:
        self._applying_input = False
        identity = self._controller.get_current_identity()
        self._input_combo.setEnabled(identity.available and not self._working)
        if ok:
            actual_input = self._controller.get_input_source() or normalize_input_source(new_input)
            self._last_input = actual_input
            self._config.kef_input = actual_input
            persisted = self._config_store.save(self._config)
            if actual_input in _INPUT_VALUES:
                self._input_combo.blockSignals(True)
                self._input_combo.setCurrentIndex(_INPUT_VALUES.index(actual_input))
                self._input_combo.blockSignals(False)
            self._input_feedback.setText("Applied")
            InfoBar.success(
                "Input Changed", f"The speaker input was changed to {actual_input.capitalize()}.",
                duration=2000, parent=self.window(), position=InfoBarPosition.TOP_RIGHT,
            )
            if not persisted:
                InfoBar.warning(
                    "Default Input Not Saved",
                    "The input changed on the speaker, but the saved default input could not be written to config.json.",
                    duration=3000, parent=self.window(), position=InfoBarPosition.TOP_RIGHT,
                )
        else:
            self._input_combo.blockSignals(True)
            idx = _INPUT_VALUES.index(self._last_input) if self._last_input in _INPUT_VALUES else 0
            self._input_combo.setCurrentIndex(idx)
            self._input_combo.blockSignals(False)
            self._input_feedback.setText("Failed")
            InfoBar.error(
                "Input Change Failed", "The app could not change the speaker input.",
                duration=3000, parent=self.window(), position=InfoBarPosition.TOP_RIGHT,
            )
        QTimer.singleShot(2500, lambda: self._input_feedback.setText(""))

    def _on_vol_press(self) -> None:
        self._vol_dragging = True

    def _on_vol_release(self) -> None:
        self._vol_dragging = False
        self._vol_pending = self._vol_slider.value()
        self._commit_volume()

    def _on_vol_value_changed(self, val: int) -> None:
        self._vol_lbl.setText(str(val))
        if self._vol_dragging:
            self._vol_pending = val
            self._vol_debounce.start(400)

    def _commit_volume(self) -> None:
        val = self._vol_pending
        if val is None:
            return
        self._vol_pending = None

        def run():
            ok = self._controller.set_volume(val)
            self._vol_set_done.emit(ok)

        threading.Thread(target=run, daemon=True, name="UISetVolume").start()

    def _on_vol_set_done(self, ok: bool) -> None:
        if not ok:
            InfoBar.warning(
                "Volume Update Failed", "The app could not change the speaker volume.",
                duration=2000, parent=self.window(), position=InfoBarPosition.TOP_RIGHT,
            )

    def on_wake(self) -> None:
        self._run_power_action("wake", self._controller.wake_kef, "UIWake")

    def on_standby(self) -> None:
        self._run_power_action("sleep", self._controller.standby_kef, "UIStandby")

    def _run_power_action(
        self,
        desired_state: str,
        runner: Callable[[int, str], bool],
        thread_name: str,
    ) -> None:
        def run():
            generation = self._controller._new_generation(desired_state, "ui_button")
            runner(generation, "ui_button")

        threading.Thread(target=run, daemon=True, name=thread_name).start()

    def _set_working(self, working: bool) -> None:
        self._working = working
        self._wake_btn.setEnabled(not working)
        self._standby_btn.setEnabled(not working)
        self.refresh()

    def _on_identity_changed(self, _identity: object) -> None:
        self.request_state_refresh()

    def _on_power_action_started(self, _action: str, _reason: str) -> None:
        self._active_power_actions += 1
        self._set_working(True)

    def _on_power_action_finished(self, action: str, _reason: str, success: bool, _outcome: str) -> None:
        self._active_power_actions = max(0, self._active_power_actions - 1)
        if success:
            if action == "WAKE":
                self._power_on_hint = True
            elif action in {"STANDBY", "LOCK_PRE_STANDBY", "ENDSESSION_STANDBY"}:
                self._power_on_hint = False
        self._set_working(self._active_power_actions > 0)
        if success and action == "WAKE":
            self._maybe_fetch_volume()

    def _refresh_action_buttons(self, speaker_on: bool) -> None:
        if self._working:
            self._wake_btn.setStyleSheet(_BTN_IDLE)
            self._standby_btn.setStyleSheet(_BTN_IDLE)
            self._wake_btn.setText("Working...")
            self._standby_btn.setText("Please Wait")
            return

        if speaker_on:
            self._wake_btn.setStyleSheet(_BTN_GREEN)
            self._standby_btn.setStyleSheet(_BTN_IDLE)
            self._wake_btn.setText("On")
            self._standby_btn.setText("Off")
            return

        self._wake_btn.setStyleSheet(_BTN_IDLE)
        self._standby_btn.setStyleSheet(_BTN_DANGER)
        self._wake_btn.setText("On")
        self._standby_btn.setText("Off")

    def _resolve_speaker_on(self, speaker_available: bool) -> bool:
        if not speaker_available:
            return False
        if self._power_on_hint is not None:
            return self._power_on_hint
        return True
