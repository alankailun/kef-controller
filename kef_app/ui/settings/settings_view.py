from __future__ import annotations

import logging
import threading
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QLabel, QHBoxLayout, QScrollArea as QtScrollArea, QVBoxLayout, QWidget
from qfluentwidgets import (
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    SettingCardGroup,
    TitleLabel,
)

from ...config import AppConfig
from ...storage import UserConfigStore
from ...controller import KefPowerController
from ...devices.scan import is_routable_ipv4
from ...devices.speaker_models import INPUT_SOURCE_OPTIONS, SpeakerIdentity, normalize_input_source, normalize_mac
from ...platform.windows import (
    is_startup_registered,
    remove_startup_task_with_uac,
    repair_task_startup_with_uac,
)
from ..background_tasks import start_background_task
from ..event_test_view import EventTestPanel
from ..svg_icons import AppIcon
from .settings_cards import ButtonCard, ComboCard, StatusCard, SwitchCard
from .settings_service import (
    INPUTS,
    SPEAKER_POWER_OPTIONS,
    STARTUP_METHOD_OPTIONS,
    STARTUP_METHOD_VALUES,
    TASK_NAME,
    apply_runtime_config,
    get_startup_status_view,
    log_power_behavior_state_message,
    save_settings_and_sync_startup,
    startup_mode_for_ui,
)


class SpeakerSelectionDialog(QDialog):
    def __init__(
        self,
        speakers: list[SpeakerIdentity],
        *,
        current_ip: str,
        current_mac: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.selected_speaker: Optional[SpeakerIdentity] = None
        self._current_ip = current_ip
        self._current_mac = normalize_mac(current_mac)
        self.setWindowTitle("Select Speaker")
        self.setMinimumWidth(680)
        self.setMinimumHeight(360)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        title = TitleLabel("Select Speaker")
        root.addWidget(title)

        self._hint = QLabel()
        self._hint.setWordWrap(True)
        root.addWidget(self._hint)

        scroll = QtScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtScrollArea.Shape.NoFrame)

        content = QWidget()
        self._content_layout = QVBoxLayout(content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(8)
        scroll.setWidget(content)
        root.addWidget(scroll, stretch=1)

        close_row = QHBoxLayout()
        close_row.addStretch()
        close_btn = PushButton("Close")
        close_btn.clicked.connect(self.reject)
        close_row.addWidget(close_btn)
        root.addLayout(close_row)
        self.update_speakers(speakers, scanning=False)

    def update_speakers(self, speakers: list[SpeakerIdentity], *, scanning: bool) -> None:
        self._hint.setText(
            "Choose the KEF speaker this app should control. The network scan is still running, so this list may update."
            if scanning
            else "Choose the KEF speaker this app should control."
        )
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        for speaker in speakers:
            selected = self._speaker_is_selected(speaker, self._current_ip, self._current_mac)
            card = ButtonCard(
                FIF.SPEAKERS,
                self._speaker_title(speaker),
                self._speaker_content(speaker),
                "Selected" if selected else "Select",
            )
            card.button.setEnabled(not selected)
            card.button.clicked.connect(lambda _checked=False, item=speaker: self._select(item))
            self._content_layout.addWidget(card)

        self._content_layout.addStretch()

    def _select(self, speaker: SpeakerIdentity) -> None:
        self.selected_speaker = speaker
        self.accept()

    @staticmethod
    def _speaker_title(speaker: SpeakerIdentity) -> str:
        if speaker.speaker_name and speaker.speaker_model:
            return f"{speaker.speaker_name} - {speaker.speaker_model}"
        return speaker.speaker_name or speaker.speaker_model or speaker.ip or "KEF Speaker"

    @staticmethod
    def _speaker_content(speaker: SpeakerIdentity) -> str:
        mac = speaker.mac_display or speaker.mac or "Not reported"
        return f"IP: {speaker.ip or 'Unknown'}    MAC: {mac}"

    @staticmethod
    def _speaker_is_selected(speaker: SpeakerIdentity, current_ip: str, current_mac: str) -> bool:
        speaker_mac = normalize_mac(speaker.mac or speaker.mac_display or "")
        if speaker_mac and current_mac:
            return speaker_mac == current_mac
        return bool(current_ip and speaker.ip == current_ip)


class ManualTargetDialog(QDialog):
    def __init__(self, *, ip: str, mac: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Manual Target Details")
        self.setMinimumWidth(520)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        root.addWidget(TitleLabel("Manual Target Details"))

        hint = QLabel("Use these fields only when you want to type the target address by hand.")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self.ip_edit = LineEdit()
        self.ip_edit.setPlaceholderText("192.168.1.xxx")
        self.ip_edit.setText(ip)
        self.mac_edit = LineEdit()
        self.mac_edit.setPlaceholderText("AA:BB:CC:DD:EE:FF")
        self.mac_edit.setText(mac)

        root.addWidget(QLabel("Speaker IP Address"))
        root.addWidget(self.ip_edit)
        root.addWidget(QLabel("Target Speaker MAC"))
        root.addWidget(self.mac_edit)

        row = QHBoxLayout()
        row.addStretch()
        cancel_btn = PushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        save_btn = PrimaryPushButton("Apply")
        save_btn.clicked.connect(self.accept)
        row.addWidget(cancel_btn)
        row.addWidget(save_btn)
        root.addLayout(row)

    def values(self) -> tuple[str, str, str]:
        raw_mac = self.mac_edit.text().strip()
        return self.ip_edit.text().strip(), raw_mac, normalize_mac(raw_mac)


class SettingsInterface(ScrollArea):
    settings_saved = Signal()
    _speaker_scan_candidate = Signal(object)
    _speaker_scan_finished = Signal(object)
    _speaker_scan_failed = Signal(str)
    _manual_target_apply_finished = Signal(object)
    _manual_target_apply_failed = Signal(str)

    def __init__(
        self,
        config: AppConfig,
        controller: KefPowerController,
        config_store: UserConfigStore,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("settingsInterface")
        self._runtime_config = config
        self._controller = controller
        self._config_store = config_store
        self._log = logging.getLogger("kef_controller")
        self._last_scanned_speakers: list[SpeakerIdentity] = []
        self._speaker_selection_dialog: SpeakerSelectionDialog | None = None
        self._speaker_scan_cancel: threading.Event | None = None
        self._speaker_scan_in_progress = False
        self._speaker_scan_dialog_closed = False
        self._pending_manual_target: tuple[str, str] | None = None
        self._event_tests_expanded = False

        self._speaker_scan_candidate.connect(self._on_speaker_scan_candidate)
        self._speaker_scan_finished.connect(self._on_speaker_scan_finished)
        self._speaker_scan_failed.connect(self._on_speaker_scan_failed)
        self._manual_target_apply_finished.connect(self._on_manual_target_apply_finished)
        self._manual_target_apply_failed.connect(self._on_manual_target_apply_failed)

        container = QWidget()
        container.setObjectName("settingsContainer")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(36, 20, 36, 36)
        layout.setSpacing(20)

        layout.addWidget(TitleLabel("Settings"))
        layout.addSpacing(4)

        self._build_device_group(container, layout)
        self._build_discovery_group(container, layout)
        self._build_behavior_group(container, layout)
        self._build_advanced_group(container, layout)
        self._build_diagnostics_group(container, layout)
        self._build_save_row(layout)

        layout.addStretch()

        self.setWidget(container)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def _build_device_group(self, container: QWidget, layout: QVBoxLayout) -> None:
        config = self._runtime_config
        group = SettingCardGroup("Speaker", container)

        self._kef_input = ComboCard(
            FIF.MUSIC,
            "Default Input Source",
            "This input will be selected whenever the app wakes the speaker.",
            [label for label, _ in INPUT_SOURCE_OPTIONS],
        )
        current = normalize_input_source(config.kef_input)
        self._kef_input.set_index(INPUTS.index(current) if current in INPUTS else 0)
        group.addSettingCard(self._kef_input)

        layout.addWidget(group)

    def _build_behavior_group(self, container: QWidget, layout: QVBoxLayout) -> None:
        config = self._runtime_config
        group = SettingCardGroup("Speaker Power Behavior", container)
        icon_by_key = {
            "wake_on_startup": AppIcon.DESKTOP,
            "endsession_standby_on_shutdown": FIF.POWER_BUTTON,
            "standby_on_lock": AppIcon.LOCK_CLOSED,
            "wake_on_unlock_only": AppIcon.LOCK_OPEN,
            "standby_on_sleep": FIF.QUIET_HOURS,
            "standby_on_display_off": AppIcon.DESKTOP_OFF,
        }
        self._power_behavior_cards: dict[str, SwitchCard] = {}
        for option in SPEAKER_POWER_OPTIONS:
            card = SwitchCard(
                icon_by_key[option.key],
                option.title,
                option.description,
            )
            card.set_checked(bool(getattr(config, option.key)))
            group.addSettingCard(card)
            self._power_behavior_cards[option.key] = card

        layout.addWidget(group)

    def _build_discovery_group(self, container: QWidget, layout: QVBoxLayout) -> None:
        group = SettingCardGroup("Finding the Speaker", container)

        self._scan_speakers = ButtonCard(
            FIF.SEARCH,
            "Select Speaker",
            "Scan the local network and choose the speaker this app should control.",
            "Select Speaker...",
        )
        self._scan_speakers.button.clicked.connect(self._on_scan_speakers)
        group.addSettingCard(self._scan_speakers)

        self._target_summary = StatusCard(
            FIF.INFO,
            "Current Target",
            "The MAC is the speaker identity. The IP is only the current address hint.",
        )
        group.addSettingCard(self._target_summary)

        self._manual_target = ButtonCard(
            FIF.EDIT,
            "Manual Target Details",
            "Edit the target IP and MAC directly. Apply validates before saving.",
            "Edit...",
        )
        self._manual_target.button.clicked.connect(self._on_edit_manual_target)
        group.addSettingCard(self._manual_target)

        self._refresh_target_summary()
        layout.addWidget(group)

    def _build_advanced_group(self, container: QWidget, layout: QVBoxLayout) -> None:
        group = SettingCardGroup("Windows Startup", container)

        self._startup_initial_checked = is_startup_registered(TASK_NAME)

        self._startup_method = ComboCard(
            FIF.POWER_BUTTON,
            "Startup Method",
            "Off removes both startup entries. Task Scheduler may ask for administrator approval. Registry Run is simpler.",
            [label for label, _ in STARTUP_METHOD_OPTIONS],
        )
        current_mode = (
            startup_mode_for_ui(self._runtime_config.startup_registration_mode)
            if self._startup_initial_checked
            else "off"
        )
        self._startup_method.set_index(STARTUP_METHOD_VALUES.index(current_mode))
        group.addSettingCard(self._startup_method)

        self._startup_status = StatusCard(
            FIF.INFO,
            "Current Active Method",
            "Shows which Windows startup method is active right now.",
        )
        group.addSettingCard(self._startup_status)

        self._refresh_startup_status()
        layout.addWidget(group)

    def _build_diagnostics_group(self, container: QWidget, layout: QVBoxLayout) -> None:
        group = SettingCardGroup("Diagnostics", container)

        self._event_tests_toggle = ButtonCard(
            AppIcon.BEAKER,
            "Event Tests",
            "Simulate startup, shutdown, lock, unlock, display-off, and sleep behavior.",
            "Show Tests",
        )
        self._event_tests_toggle.button.clicked.connect(self._toggle_event_tests)
        group.addSettingCard(self._event_tests_toggle)

        layout.addWidget(group)

        self._event_tests = EventTestPanel(self._runtime_config, self._controller, container)
        self._event_tests.setVisible(False)
        layout.addWidget(self._event_tests)

    def _build_save_row(self, layout: QVBoxLayout) -> None:
        row = QHBoxLayout()
        row.addStretch()
        self._save_btn = PrimaryPushButton("Save Settings")
        self._save_btn.setMinimumWidth(160)
        self._save_btn.setMinimumHeight(40)
        self._save_btn.clicked.connect(self._on_save)
        row.addWidget(self._save_btn)
        layout.addLayout(row)

    def _apply_runtime_config(self, updated: AppConfig) -> None:
        apply_runtime_config(self._runtime_config, updated, self._config_store.USER_EDITABLE_FIELDS)
        self._controller.apply_configured_device_target(source="settings_save")

    def _log_power_behavior_state(self) -> None:
        self._log.info(log_power_behavior_state_message(self._runtime_config))

    def _toggle_event_tests(self) -> None:
        self._event_tests_expanded = not self._event_tests_expanded
        self._event_tests.setVisible(self._event_tests_expanded)
        self._event_tests_toggle.button.setText("Hide Tests" if self._event_tests_expanded else "Show Tests")
        if self._event_tests_expanded:
            self._event_tests.refresh()

    def _try_elevated_startup_disable(self) -> bool:
        ok, detail = remove_startup_task_with_uac(TASK_NAME, log=self._log)
        if ok:
            InfoBar.success(
                "Windows Startup Removed",
                "The Windows auto-start entry was removed with administrator approval.",
                duration=4000,
                parent=self.window(),
                position=InfoBarPosition.TOP_RIGHT,
            )
            return True

        InfoBar.warning(
            "Windows Startup Was Not Removed",
            detail,
            duration=5000,
            parent=self.window(),
            position=InfoBarPosition.TOP_RIGHT,
        )
        return False

    def _try_elevated_startup_enable(self) -> bool:
        ok, _detail = repair_task_startup_with_uac(TASK_NAME, log=self._log)
        return ok

    def _on_save(self) -> None:
        selected_startup_mode = STARTUP_METHOD_VALUES[self._startup_method.current_index()]
        updated = self._runtime_config.with_updates(
            kef_ip=self._runtime_config.kef_ip,
            kef_mac=self._runtime_config.kef_mac,
            kef_input=INPUTS[self._kef_input.current_index()],
            wake_on_startup=self._power_behavior_cards["wake_on_startup"].is_checked(),
            wake_on_unlock_only=self._power_behavior_cards["wake_on_unlock_only"].is_checked(),
            standby_on_sleep=self._power_behavior_cards["standby_on_sleep"].is_checked(),
            standby_on_lock=self._power_behavior_cards["standby_on_lock"].is_checked(),
            standby_on_display_off=self._power_behavior_cards["standby_on_display_off"].is_checked(),
            endsession_standby_on_shutdown=self._power_behavior_cards["endsession_standby_on_shutdown"].is_checked(),
            startup_registration_mode=selected_startup_mode,
        )

        desired_startup = selected_startup_mode != "off"
        startup_mode_changed = updated.startup_registration_mode != startup_mode_for_ui(
            self._runtime_config.startup_registration_mode
        )
        save_result = save_settings_and_sync_startup(
            updated,
            config_store=self._config_store,
            desired_startup=desired_startup,
            startup_initial_checked=self._startup_initial_checked,
            startup_mode_changed=startup_mode_changed,
            log=self._log,
            task_name=TASK_NAME,
            retry_disable_with_uac=self._try_elevated_startup_disable,
            retry_enable_task_with_uac=self._try_elevated_startup_enable,
        )
        updated = save_result.updated
        config_ok = save_result.config_ok
        startup_ok = save_result.startup_ok
        startup_changed = save_result.startup_changed
        startup_detail = save_result.startup_detail
        self._startup_initial_checked = save_result.startup_initial_checked
        if startup_changed and not save_result.actual_startup_registered:
            self._startup_method.set_index(STARTUP_METHOD_VALUES.index("off"))
        if config_ok:
            self._apply_runtime_config(updated)
            self._log_power_behavior_state()
            self._refresh_target_summary()
            self._event_tests.refresh()
            self.settings_saved.emit()
        self._refresh_startup_status()

        win = self.window()
        if config_ok and startup_ok:
            InfoBar.success(
                "Settings Saved",
                "Your changes were saved. Speaker behavior updates apply immediately.",
                duration=4000,
                parent=win,
                position=InfoBarPosition.TOP_RIGHT,
            )
        elif config_ok:
            if startup_detail:
                self._log.info(
                    f"Settings save kept config changes but failed to update Windows startup | "
                    f"desired={desired_startup} | detail={startup_detail}"
                )
            InfoBar.warning(
                "Windows Startup Was Not Updated",
                (
                    "Your settings were saved, but the Windows auto-start entry could not be changed."
                    if not startup_detail
                    else (
                        "Your settings were saved, but the Windows auto-start entry could not be changed. "
                        f"Reason: {startup_detail}"
                    )
                ),
                duration=5000,
                parent=win,
                position=InfoBarPosition.TOP_RIGHT,
            )
        elif startup_ok:
            InfoBar.error(
                "Save Failed",
                "The settings file could not be saved, even though the Windows auto-start entry may already have changed.",
                duration=5000,
                parent=win,
                position=InfoBarPosition.TOP_RIGHT,
            )
        else:
            InfoBar.error(
                "Save Failed",
                "The settings file could not be saved, and the Windows auto-start entry could not be updated.",
                duration=4000,
                parent=win,
                position=InfoBarPosition.TOP_RIGHT,
            )

    def _refresh_startup_status(self) -> None:
        status = get_startup_status_view(self._runtime_config, log=self._log, task_name=TASK_NAME)
        lines = [f"Selected: {status.preferred_label}"]
        if status.cleanup_needed:
            lines.append(f"Cleanup: {status.cleanup_text}")
        lines.append(status.current_detail)
        self._startup_status.setContent("\n".join(lines))
        self._startup_status.set_value(status.current_label)

    def _refresh_target_summary(self) -> None:
        identity = self._controller.get_current_identity()
        ip = self._runtime_config.kef_ip or identity.ip
        mac = self._runtime_config.kef_mac or identity.mac or identity.mac_display
        name = identity.speaker_name or identity.speaker_model

        if name and ip:
            value = f"{name} / {ip}"
        elif ip:
            value = ip
        elif mac:
            value = f"MAC {mac}"
        else:
            value = "Not selected"
        self._target_summary.set_value(value)

    def _on_edit_manual_target(self) -> None:
        dialog = ManualTargetDialog(
            ip=self._runtime_config.kef_ip,
            mac=self._runtime_config.kef_mac,
            parent=self.window(),
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        ip, raw_mac, mac = dialog.values()
        if raw_mac and len(mac) != 12:
            InfoBar.error(
                "Target Not Saved",
                "Target Speaker MAC must contain exactly 12 hexadecimal characters.",
                duration=4000,
                parent=self.window(),
                position=InfoBarPosition.TOP_RIGHT,
            )
            return
        if ip and not is_routable_ipv4(ip):
            InfoBar.error(
                "Target Not Saved",
                "Speaker IP Address must be a usable IPv4 address.",
                duration=4000,
                parent=self.window(),
                position=InfoBarPosition.TOP_RIGHT,
            )
            return

        if not ip and not mac:
            saved = self._save_manual_target("", "")
            self._show_manual_target_saved(
                "Target Cleared",
                "No speaker is selected. Use Select Speaker before controlling a device.",
                saved=saved,
                warning=True,
            )
            return

        self._pending_manual_target = (ip, mac)
        self._manual_target.button.setEnabled(False)
        self._manual_target.button.setText("Applying...")

        start_background_task(
            "ApplyManualTarget",
            lambda: self._controller.validate_manual_target(
                ip,
                raw_mac,
                reason="settings_manual_target",
                trigger="manual_target_dialog",
            ),
            on_success=self._manual_target_apply_finished.emit,
            on_error=lambda exc: self._manual_target_apply_failed.emit(str(exc)),
            log=self._log,
        )

    def _save_manual_target(self, ip: str, mac: str) -> bool:
        self._runtime_config.kef_ip = ip
        self._runtime_config.kef_mac = mac
        self._controller.apply_configured_device_target(source="manual_target_dialog")
        saved = self._config_store.save(self._runtime_config)
        self._refresh_target_summary()
        self.settings_saved.emit()
        return saved

    def _show_manual_target_saved(self, title: str, message: str, *, saved: bool, warning: bool = False) -> None:
        if not saved:
            InfoBar.warning(
                title,
                f"{message} The target changed for this session, but config.json could not be saved.",
                duration=5000,
                parent=self.window(),
                position=InfoBarPosition.TOP_RIGHT,
            )
            return

        bar = InfoBar.warning if warning else InfoBar.success
        bar(
            title,
            message,
            duration=5000 if warning else 4000,
            parent=self.window(),
            position=InfoBarPosition.TOP_RIGHT,
        )

    def _finish_manual_target_apply_ui(self) -> None:
        self._manual_target.button.setEnabled(True)
        self._manual_target.button.setText("Edit...")
        self._pending_manual_target = None

    def _on_manual_target_apply_finished(self, result: object) -> None:
        pending = self._pending_manual_target
        self._finish_manual_target_apply_ui()
        if pending is None:
            return

        status = str(getattr(result, "status", "failed"))
        requested_ip = str(getattr(result, "requested_ip", ""))
        requested_mac = normalize_mac(str(getattr(result, "requested_mac", "")))
        identity = getattr(result, "identity", None) or SpeakerIdentity()
        if pending != (requested_ip, requested_mac):
            return

        if status == "mac_mismatch":
            actual = identity.mac_display or identity.mac or "not reported"
            InfoBar.error(
                "Target Not Saved",
                f"That IP belongs to a KEF speaker with a different MAC ({actual}).",
                duration=6000,
                parent=self.window(),
                position=InfoBarPosition.TOP_RIGHT,
            )
            return

        if status == "not_kef":
            InfoBar.error(
                "Target Not Saved",
                "That IP responded, but it did not look like a supported KEF speaker.",
                duration=5000,
                parent=self.window(),
                position=InfoBarPosition.TOP_RIGHT,
            )
            return

        if status in {"invalid_ip", "invalid_mac", "failed"}:
            InfoBar.error(
                "Target Not Saved",
                "The target details could not be applied.",
                duration=4000,
                parent=self.window(),
                position=InfoBarPosition.TOP_RIGHT,
            )
            return

        save_ip = requested_ip
        save_mac = requested_mac
        if status in {"verified", "recovered", "mac_unverified"}:
            save_ip = identity.ip or save_ip
            save_mac = identity.mac or save_mac

        saved = self._save_manual_target(save_ip, save_mac)

        if status == "verified":
            self._show_manual_target_saved(
                "Manual Target Saved",
                "The target was verified and saved.",
                saved=saved,
            )
        elif status == "recovered":
            self._show_manual_target_saved(
                "Manual Target Saved",
                "The IP was recovered from the target MAC and saved.",
                saved=saved,
            )
        elif status == "mac_unverified":
            self._show_manual_target_saved(
                "Target Saved, MAC Not Verified",
                "The IP is a supported KEF speaker, but it did not report a MAC during verification.",
                saved=saved,
                warning=True,
            )
        elif status == "mac_not_found":
            self._show_manual_target_saved(
                "Target MAC Saved",
                "The MAC format is valid, but the speaker was not found right now. The app will recover its IP when it appears.",
                saved=saved,
                warning=True,
            )
        elif status == "unreachable":
            self._show_manual_target_saved(
                "Target Saved, Not Verified",
                "The IP did not respond right now. It was saved as a target hint.",
                saved=saved,
                warning=True,
            )
        else:
            self._show_manual_target_saved(
                "Manual Target Saved",
                "The target details were saved.",
                saved=saved,
            )

    def _on_manual_target_apply_failed(self, detail: str) -> None:
        pending = self._pending_manual_target
        self._finish_manual_target_apply_ui()
        if pending is None:
            return
        InfoBar.error(
            "Target Not Saved",
            detail or "The target details could not be verified.",
            duration=4000,
            parent=self.window(),
            position=InfoBarPosition.TOP_RIGHT,
        )

    def _on_scan_speakers(self) -> None:
        self._scan_speakers.button.setEnabled(False)
        self._scan_speakers.button.setText("Scanning...")
        self._last_scanned_speakers = []
        cancel_event = threading.Event()
        self._speaker_scan_cancel = cancel_event
        self._speaker_scan_in_progress = True
        self._speaker_scan_dialog_closed = False

        start_background_task(
            "SettingsScanSpeakers",
            lambda: self._controller.scan_kef_devices(
                on_candidate=lambda speaker: self._speaker_scan_candidate.emit([speaker]),
                should_continue=lambda: not cancel_event.is_set(),
            ),
            on_success=self._speaker_scan_finished.emit,
            on_error=lambda exc: self._speaker_scan_failed.emit(str(exc)),
            log=self._log,
        )

    def _on_speaker_scan_candidate(self, speakers: object) -> None:
        devices = list(speakers) if isinstance(speakers, list) else []
        if not devices or self._speaker_scan_dialog_closed:
            return

        self._last_scanned_speakers = self._merge_speaker_lists(self._last_scanned_speakers, devices)
        self._scan_speakers.button.setText(f"Scanning... Found {len(self._last_scanned_speakers)}")
        self._show_or_update_speaker_selection_dialog(self._last_scanned_speakers, scanning=True)

    def _on_speaker_scan_finished(self, speakers: object) -> None:
        canceled = self._consume_speaker_scan_cancelled()
        self._speaker_scan_in_progress = False
        self._scan_speakers.button.setEnabled(True)
        self._scan_speakers.button.setText("Select Speaker...")
        devices = self._merge_speaker_lists(
            self._last_scanned_speakers,
            list(speakers) if isinstance(speakers, list) else [],
        )
        self._last_scanned_speakers = devices
        if canceled:
            return
        if not devices:
            InfoBar.warning(
                "No Speakers Found",
                "The scan did not find a supported KEF speaker on the local network.",
                duration=4000,
                parent=self.window(),
                position=InfoBarPosition.TOP_RIGHT,
            )
            return

        if self._speaker_scan_dialog_closed and (
            self._speaker_selection_dialog is None or not self._speaker_selection_dialog.isVisible()
        ):
            return
        self._show_or_update_speaker_selection_dialog(devices, scanning=False)

    def _on_speaker_scan_failed(self, detail: str) -> None:
        canceled = self._consume_speaker_scan_cancelled()
        self._speaker_scan_in_progress = False
        self._scan_speakers.button.setEnabled(True)
        self._scan_speakers.button.setText("Select Speaker...")
        self._last_scanned_speakers = []
        if canceled:
            return
        InfoBar.error(
            "Scan Failed",
            detail or "The speaker scan could not complete.",
            duration=4000,
            parent=self.window(),
            position=InfoBarPosition.TOP_RIGHT,
        )

    def _cancel_speaker_scan(self) -> None:
        if self._speaker_scan_cancel is not None:
            self._speaker_scan_cancel.set()

    def _consume_speaker_scan_cancelled(self) -> bool:
        cancel_event = self._speaker_scan_cancel
        self._speaker_scan_cancel = None
        return bool(cancel_event is not None and cancel_event.is_set())

    @staticmethod
    def _speaker_identity_key(speaker: SpeakerIdentity) -> tuple[str, str]:
        speaker_mac = normalize_mac(speaker.mac or speaker.mac_display or "")
        return ("mac", speaker_mac) if speaker_mac else ("ip", speaker.ip or "")

    @classmethod
    def _merge_speaker_lists(
        cls,
        existing: list[SpeakerIdentity],
        incoming: list[SpeakerIdentity],
    ) -> list[SpeakerIdentity]:
        merged: dict[tuple[str, str], SpeakerIdentity] = {}
        fallback_index = 0
        for speaker in [*existing, *incoming]:
            key = cls._speaker_identity_key(speaker)
            if not key[1]:
                fallback_index += 1
                key = ("item", str(fallback_index))
            merged[key] = speaker
        return list(merged.values())

    def _open_speaker_selection_dialog(self, speakers: list[SpeakerIdentity]) -> None:
        self._show_or_update_speaker_selection_dialog(speakers, scanning=False)

    def _show_or_update_speaker_selection_dialog(self, speakers: list[SpeakerIdentity], *, scanning: bool) -> None:
        dialog = self._speaker_selection_dialog
        if dialog is None or not dialog.isVisible():
            dialog = SpeakerSelectionDialog(
                speakers,
                current_ip=self._runtime_config.kef_ip,
                current_mac=self._runtime_config.kef_mac or self._controller.get_effective_target_mac(),
                parent=self.window(),
            )
            self._speaker_selection_dialog = dialog
            dialog.finished.connect(lambda result, item=dialog: self._on_speaker_selection_dialog_finished(item, result))
            dialog.update_speakers(speakers, scanning=scanning)
            dialog.open()
            return

        dialog.update_speakers(speakers, scanning=scanning)
        dialog.raise_()
        dialog.activateWindow()

    def _on_speaker_selection_dialog_finished(self, dialog: SpeakerSelectionDialog, result: int) -> None:
        if self._speaker_selection_dialog is dialog:
            self._speaker_selection_dialog = None
        if self._speaker_scan_in_progress:
            self._speaker_scan_dialog_closed = True
            self._cancel_speaker_scan()
        if result == QDialog.DialogCode.Accepted and dialog.selected_speaker:
            self._use_speaker(dialog.selected_speaker)

    def _use_speaker(self, speaker: SpeakerIdentity) -> None:
        target_mac = normalize_mac(speaker.mac or speaker.mac_display or "")
        self._finish_manual_target_apply_ui()

        self._runtime_config.kef_ip = speaker.ip
        self._runtime_config.kef_mac = target_mac

        selected = self._controller.select_kef_device(speaker, source="settings_card")
        saved = self._config_store.save(self._runtime_config)
        self._refresh_target_summary()
        self.settings_saved.emit()

        win = self.window()
        if saved:
            message = "This speaker is now the app target."
            if not target_mac:
                message = "This speaker is now the app target by IP only because its MAC was not reported."
            InfoBar.success(
                "Speaker Selected",
                message if selected else "This speaker was already selected.",
                duration=4000,
                parent=win,
                position=InfoBarPosition.TOP_RIGHT,
            )
        else:
            InfoBar.warning(
                "Speaker Selected",
                "The target changed for this session, but config.json could not be saved.",
                duration=5000,
                parent=win,
                position=InfoBarPosition.TOP_RIGHT,
            )
