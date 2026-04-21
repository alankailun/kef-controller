from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QMessageBox, QVBoxLayout, QWidget
from qfluentwidgets import (
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
    PrimaryPushButton,
    ScrollArea,
    SettingCardGroup,
    TitleLabel,
)

from ...appdata import AppConfig, UserConfigStore
from ...models import INPUT_SOURCE_OPTIONS, normalize_input_source
from ...platform_windows import (
    is_startup_registered,
    remove_startup_task_with_uac,
    repair_task_startup_with_uac,
)
from .cards import ButtonCard, ComboCard, StatusCard, SwitchCard, TextCard
from .shared import (
    INPUTS,
    SPEAKER_POWER_OPTIONS,
    TASK_NAME,
    apply_runtime_config,
    get_startup_status_view,
    log_power_behavior_state_message,
    save_settings_and_sync_startup,
)


class SettingsInterface(ScrollArea):
    settings_saved = Signal()

    def __init__(
        self,
        config: AppConfig,
        config_store: UserConfigStore,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("settingsInterface")
        self._runtime_config = config
        self._config_store = config_store
        self._log = logging.getLogger("kef_controller")

        container = QWidget()
        container.setObjectName("settingsContainer")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(36, 20, 36, 36)
        layout.setSpacing(20)

        layout.addWidget(TitleLabel("Settings"))
        layout.addSpacing(4)

        self._build_device_group(container, layout)
        self._build_behavior_group(container, layout)
        self._build_discovery_group(container, layout)
        self._build_advanced_group(container, layout)
        self._build_save_row(layout)

        layout.addStretch()

        self.setWidget(container)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def _build_device_group(self, container: QWidget, layout: QVBoxLayout) -> None:
        config = self._runtime_config
        group = SettingCardGroup("Speaker", container)

        self._kef_ip = TextCard(
            FIF.WIFI,
            "Speaker IP Address",
            "Optional. Leave this blank if you want the app to find the speaker automatically.",
            placeholder="192.168.1.xxx",
        )
        self._kef_ip.set_text(config.kef_ip)
        group.addSettingCard(self._kef_ip)

        self._kef_mac = TextCard(
            FIF.TAG,
            "Speaker MAC Address",
            "Optional. Helps the app recover the speaker IP after router or DHCP changes.",
            placeholder="AA:BB:CC:DD:EE:FF",
        )
        self._kef_mac.set_text(config.kef_mac)
        group.addSettingCard(self._kef_mac)

        self._expected_name = TextCard(
            FIF.SEARCH,
            "Expected Device Name",
            "Optional. Use this when you have more than one KEF device and want this app to choose a specific one.",
            placeholder="Leave blank if not needed",
        )
        self._expected_name.set_text(config.expected_speaker_name)
        group.addSettingCard(self._expected_name)

        self._expected_mac = TextCard(
            FIF.FINGERPRINT,
            "Expected MAC",
            "Optional. Use this only if you want to lock the app to one exact device.",
            placeholder="Leave blank if not needed",
        )
        self._expected_mac.set_text(config.expected_speaker_mac)
        group.addSettingCard(self._expected_mac)

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
            "wake_on_startup": FIF.POWER_BUTTON,
            "endsession_standby_on_shutdown": FIF.POWER_BUTTON,
            "standby_on_lock": FIF.POWER_BUTTON,
            "wake_on_unlock_only": FIF.SYNC,
            "standby_on_sleep": FIF.POWER_BUTTON,
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
        config = self._runtime_config
        group = SettingCardGroup("Finding the Speaker", container)

        self._auto_mac = SwitchCard(
            FIF.IOT,
            "Recover IP from MAC Address",
            "If the speaker IP changes, use the MAC address to find it again.",
        )
        self._auto_mac.set_checked(config.auto_discover_kef_ip_by_mac)
        group.addSettingCard(self._auto_mac)

        self._auto_blind = SwitchCard(
            FIF.SEARCH,
            "Search the Local Network",
            "If needed, scan your local network to look for a supported KEF speaker.",
        )
        self._auto_blind.set_checked(config.auto_discover_kef_ip_blind)
        group.addSettingCard(self._auto_blind)

        layout.addWidget(group)

    def _build_advanced_group(self, container: QWidget, layout: QVBoxLayout) -> None:
        config = self._runtime_config
        group = SettingCardGroup("Advanced", container)

        self._startup_with_windows = SwitchCard(
            FIF.POWER_BUTTON,
            "Start with Windows",
            "Launch KEF Controller automatically when you sign in to Windows.",
        )
        self._startup_initial_checked = is_startup_registered(TASK_NAME)
        self._startup_with_windows.set_checked(self._startup_initial_checked)
        group.addSettingCard(self._startup_with_windows)

        self._startup_status = StatusCard(
            FIF.INFO,
            "Current Active Method",
            "Shows which Windows startup method is active right now.",
        )
        group.addSettingCard(self._startup_status)

        self._repair_fast_startup = ButtonCard(
            FIF.SPEED_HIGH,
            "Use Faster Startup",
            "Optional. Recreate Windows startup with Task Scheduler. This may ask for administrator approval.",
            "Use Task Scheduler",
        )
        self._repair_fast_startup.button.clicked.connect(self._on_repair_fast_startup)
        group.addSettingCard(self._repair_fast_startup)

        self._enable_restart = SwitchCard(
            FIF.ROTATE,
            "Restart After a Crash",
            "Ask Windows to restart this app automatically if it crashes.",
        )
        self._enable_restart.set_checked(config.enable_application_restart)
        group.addSettingCard(self._enable_restart)

        self._diagnostic_logging = SwitchCard(
            FIF.DOCUMENT,
            "Diagnostic Logging",
            "Write detailed internal logs for troubleshooting. Leave this off unless you are debugging a problem.",
        )
        self._diagnostic_logging.set_checked(config.diagnostic_logging)
        group.addSettingCard(self._diagnostic_logging)

        self._refresh_startup_status()
        layout.addWidget(group)

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
        self._apply_runtime_logging()

    def _apply_runtime_logging(self) -> None:
        self._log.setLevel(logging.DEBUG if self._runtime_config.diagnostic_logging else logging.INFO)

    def _log_power_behavior_state(self) -> None:
        self._log.info(log_power_behavior_state_message(self._runtime_config))

    def _try_elevated_startup_disable(self) -> bool:
        prompt = QMessageBox(self.window())
        prompt.setIcon(QMessageBox.Icon.Question)
        prompt.setWindowTitle("Remove Windows Startup?")
        prompt.setText(
            "Windows blocked removal of the Task Scheduler startup entry.\n\n"
            "Do you want KEF Controller to retry this step with administrator approval now?"
        )
        prompt.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        prompt.setDefaultButton(QMessageBox.StandardButton.Yes)
        if prompt.exec() != int(QMessageBox.StandardButton.Yes):
            return False

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

    def _on_save(self) -> None:
        updated = self._runtime_config.with_updates(
            kef_ip=self._kef_ip.text(),
            kef_mac=self._kef_mac.text(),
            expected_speaker_name=self._expected_name.text(),
            expected_speaker_mac=self._expected_mac.text(),
            kef_input=INPUTS[self._kef_input.current_index()],
            wake_on_startup=self._power_behavior_cards["wake_on_startup"].is_checked(),
            wake_on_unlock_only=self._power_behavior_cards["wake_on_unlock_only"].is_checked(),
            standby_on_sleep=self._power_behavior_cards["standby_on_sleep"].is_checked(),
            standby_on_lock=self._power_behavior_cards["standby_on_lock"].is_checked(),
            endsession_standby_on_shutdown=self._power_behavior_cards["endsession_standby_on_shutdown"].is_checked(),
            auto_discover_kef_ip_by_mac=self._auto_mac.is_checked(),
            auto_discover_kef_ip_blind=self._auto_blind.is_checked(),
            enable_application_restart=self._enable_restart.is_checked(),
            diagnostic_logging=self._diagnostic_logging.is_checked(),
        )

        desired_startup = self._startup_with_windows.is_checked()
        save_result = save_settings_and_sync_startup(
            updated,
            config_store=self._config_store,
            desired_startup=desired_startup,
            startup_initial_checked=self._startup_initial_checked,
            log=self._log,
            task_name=TASK_NAME,
            retry_disable_with_uac=self._try_elevated_startup_disable,
        )
        updated = save_result.updated
        config_ok = save_result.config_ok
        startup_ok = save_result.startup_ok
        startup_changed = save_result.startup_changed
        startup_detail = save_result.startup_detail
        self._startup_initial_checked = save_result.startup_initial_checked
        if startup_changed:
            self._startup_with_windows.set_checked(save_result.actual_startup_registered)
        if config_ok:
            self._apply_runtime_config(updated)
            self._log_power_behavior_state()
            self.settings_saved.emit()
        if startup_changed:
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
                        f"Reason: {startup_detail} If you want the faster Task Scheduler method, use 'Use Faster Startup' in Advanced."
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

    def _on_repair_fast_startup(self) -> None:
        ok, detail = repair_task_startup_with_uac(TASK_NAME, log=self._log)
        win = self.window()
        if not ok:
            InfoBar.warning(
                "Startup Repair Failed",
                detail,
                duration=5000,
                parent=win,
                position=InfoBarPosition.TOP_RIGHT,
            )
            return

        updated = self._runtime_config.with_updates(startup_registration_mode="task")
        config_ok = self._config_store.save(updated)
        self._startup_with_windows.set_checked(True)
        if config_ok:
            self._apply_runtime_config(updated)
            self.settings_saved.emit()
        self._refresh_startup_status()

        if config_ok:
            InfoBar.success(
                "Startup Repair Completed",
                "Future launches will prefer Task Scheduler at sign-in.",
                duration=5000,
                parent=win,
                position=InfoBarPosition.TOP_RIGHT,
            )
            return

        InfoBar.warning(
            "Startup Repaired, but Not Saved",
            "Task Scheduler startup was repaired, but the preferred startup mode could not be written to config.json.",
            duration=5000,
            parent=win,
            position=InfoBarPosition.TOP_RIGHT,
        )

    def _refresh_startup_status(self) -> None:
        status = get_startup_status_view(self._runtime_config, log=self._log, task_name=TASK_NAME)
        self._startup_status.setContent(
            f"Stale Task Found: {status.stale_text}\n{status.current_detail} Preferred method: {status.preferred_label}."
        )
        self._startup_status.set_value(status.current_label)
        self._repair_fast_startup.button.setText(status.repair_button_text)
        self._repair_fast_startup.button.setEnabled(not status.task_is_healthy)
