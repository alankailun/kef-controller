from __future__ import annotations

import threading
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
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
from .logs import UILogHandler

_INPUT_CHOICES = list(INPUT_SOURCE_OPTIONS)
_INPUT_VALUES = [value for _, value in _INPUT_CHOICES]
_BTN_GREEN = "background-color: #1f8f5f; color: white; border: 1px solid #1f8f5f;"
_BTN_DANGER = "background-color: #c2410c; color: white; border: 1px solid #c2410c;"
_BTN_IDLE = ""


class HomeInterface(QWidget):
    _work_done = Signal(str, bool)
    _apply_done = Signal(str, bool)
    _vol_fetched = Signal(object)
    _vol_set_done = Signal(bool)

    def __init__(
        self,
        config: AppConfig,
        controller: KefPowerController,
        config_store: UserConfigStore,
        log_handler: UILogHandler,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("homeInterface")
        self._config = config
        self._controller = controller
        self._config_store = config_store
        self._working = False
        self._power_on_hint: Optional[bool] = None
        self._applying_input = False
        self._last_input = normalize_input_source(config.kef_input)
        self._prev_ip = ""
        self._vol_dragging = False
        self._vol_pending: Optional[int] = None
        self._vol_fetching = False

        self._vol_debounce = QTimer(self)
        self._vol_debounce.setSingleShot(True)
        self._vol_debounce.timeout.connect(self._commit_volume)

        self._vol_poll = QTimer(self)
        self._vol_poll.setInterval(8000)
        self._vol_poll.timeout.connect(self._maybe_fetch_volume)
        self._vol_poll.start()

        self._work_done.connect(self._on_work_done)
        self._apply_done.connect(self._on_apply_done)
        self._vol_fetched.connect(self._on_vol_fetched)
        self._vol_set_done.connect(self._on_vol_set_done)

        self._build_ui()
        self._refresh_action_buttons(speaker_on=False)

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
        self._input_combo.setEnabled(False)  # enabled by refresh() once speaker is connected
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
        speaker_on = self._resolve_speaker_on(bool(ip))

        if self._working:
            color, status = "#f59e0b", "Working..."
        elif speaker_on:
            color, status = "#22c55e", "Connected"
        elif ip:
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

        # Enable input combo only when connected and not busy
        self._input_combo.setEnabled(speaker_on and not self._working and not self._applying_input)
        self._refresh_action_buttons(speaker_on=speaker_on)

        # On first connect (IP transitions from empty to non-empty), fetch volume immediately
        if speaker_on and ip and not self._prev_ip:
            self._maybe_fetch_volume()
        self._prev_ip = ip or ""

    def _maybe_fetch_volume(self) -> None:
        if self._vol_dragging or self._working or self._vol_fetching or self._power_on_hint is False:
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
            # _on_vol_value_changed updates the label; no need to set it here

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
        # Re-enable based on current connection state
        ip = self._controller.get_current_kef_ip()
        self._input_combo.setEnabled(bool(ip) and not self._working)
        if ok:
            actual_input = self._controller.get_input_source() or normalize_input_source(new_input)
            self._last_input = actual_input
            # Persist the new selection as the default so next wake uses it
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
        self._set_working(True)

        def run():
            ok = False
            try:
                gen = self._controller._new_generation("wake", "ui_button")
                ok = self._controller.wake_kef(gen, "ui_button")
            finally:
                self._work_done.emit("wake", ok)

        threading.Thread(target=run, daemon=True, name="UIWake").start()

    def on_standby(self) -> None:
        self._set_working(True)

        def run():
            ok = False
            try:
                gen = self._controller._new_generation("sleep", "ui_button")
                ok = self._controller.standby_kef(gen, "ui_button")
            finally:
                self._work_done.emit("standby", ok)

        threading.Thread(target=run, daemon=True, name="UIStandby").start()

    def _on_work_done(self, action: str, ok: bool) -> None:
        if ok:
            self._power_on_hint = action == "wake"
        self._set_working(False)
        self.refresh()
        self._maybe_fetch_volume()

    def _set_working(self, working: bool) -> None:
        self._working = working
        self._wake_btn.setEnabled(not working)
        self._standby_btn.setEnabled(not working)
        self.refresh()

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

    def _resolve_speaker_on(self, has_ip: bool) -> bool:
        if self._power_on_hint is not None:
            return self._power_on_hint
        return has_ip
