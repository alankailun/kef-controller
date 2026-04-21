from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...appdata import AppConfig, UserConfigStore
from ...models import INPUT_SOURCE_OPTIONS, normalize_input_source
from ...platform_windows import (
    is_startup_registered,
    remove_startup_task_with_uac,
    repair_task_startup_with_uac,
)
from .shared import (
    INPUTS,
    SPEAKER_POWER_OPTIONS,
    TASK_NAME,
    apply_runtime_config,
    get_startup_status_view,
    log_power_behavior_state_message,
    save_settings_and_sync_startup,
)


class SettingsWindow(QDialog):
    def __init__(self, config: AppConfig, config_store: UserConfigStore, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._config = config
        self._runtime_config = config
        self._config_store = config_store
        self._log = logging.getLogger("kef_controller")

        self.setWindowTitle("KEF Controller - Settings")
        self.setMinimumWidth(500)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        tabs = QTabWidget()
        tabs.addTab(self._build_device_tab(), "Device")
        tabs.addTab(self._build_behavior_tab(), "Speaker Power")
        tabs.addTab(self._build_discovery_tab(), "Discovery")
        tabs.addTab(self._build_advanced_tab(), "Advanced")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(
            QLabel(
                "Speaker behavior changes apply immediately after saving.",
                alignment=Qt.AlignmentFlag.AlignRight,
            )
        )
        layout.addWidget(buttons)

    def _apply_runtime_config(self, updated: AppConfig) -> None:
        self._config = apply_runtime_config(self._runtime_config, updated, self._config_store.USER_EDITABLE_FIELDS)

    def _log_power_behavior_state(self) -> None:
        self._log.info(log_power_behavior_state_message(self._runtime_config))

    def _try_elevated_startup_disable(self) -> bool:
        reply = QMessageBox.question(
            self,
            "Remove Windows Startup?",
            (
                "Windows blocked removal of the Task Scheduler startup entry.\n\n"
                "Do you want KEF Controller to retry this step with administrator approval now?"
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return False

        ok, detail = remove_startup_task_with_uac(TASK_NAME, log=self._log)
        if ok:
            QMessageBox.information(
                self,
                "Windows Startup Removed",
                "The Windows auto-start entry was removed with administrator approval.",
            )
            return True

        QMessageBox.warning(self, "Windows Startup Was Not Removed", detail)
        return False

    def _build_device_tab(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        config = self._config

        self._kef_ip = QLineEdit(config.kef_ip)
        self._kef_ip.setPlaceholderText("Leave blank if you want the app to find the speaker automatically")
        form.addRow("Speaker IP Address:", self._kef_ip)

        self._kef_mac = QLineEdit(config.kef_mac)
        self._kef_mac.setPlaceholderText("AA:BB:CC:DD:EE:FF (helps recover the IP after router or DHCP changes)")
        form.addRow("Speaker MAC Address:", self._kef_mac)

        self._expected_name = QLineEdit(config.expected_speaker_name)
        self._expected_name.setPlaceholderText("Use this only if you want the app to pick one specific KEF device")
        form.addRow("Expected Device Name:", self._expected_name)

        self._expected_mac = QLineEdit(config.expected_speaker_mac)
        self._expected_mac.setPlaceholderText("Use this only if you want to lock the app to one exact device")
        form.addRow("Expected MAC:", self._expected_mac)

        self._kef_input = QComboBox()
        self._kef_input.addItems([label for label, _ in INPUT_SOURCE_OPTIONS])
        current = normalize_input_source(config.kef_input)
        self._kef_input.setCurrentIndex(INPUTS.index(current) if current in INPUTS else 0)
        form.addRow("Default Input Source (used when the app wakes the speaker):", self._kef_input)

        return widget

    def _build_behavior_tab(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)
        config = self._config

        self._power_behavior_checks: dict[str, QCheckBox] = {}
        for option in SPEAKER_POWER_OPTIONS:
            checkbox = QCheckBox("Enable")
            checkbox.setChecked(bool(getattr(config, option.key)))
            form.addRow(f"{option.title}:", checkbox)
            self._power_behavior_checks[option.key] = checkbox

        return widget

    def _build_discovery_tab(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)
        config = self._config

        self._auto_mac = QCheckBox("Enable")
        self._auto_mac.setChecked(config.auto_discover_kef_ip_by_mac)
        form.addRow("Recover IP from MAC Address\n(use this if the speaker IP changes):", self._auto_mac)

        self._auto_blind = QCheckBox("Enable")
        self._auto_blind.setChecked(config.auto_discover_kef_ip_blind)
        form.addRow("Search the Local Network\n(scan your local network if needed):", self._auto_blind)

        return widget

    def _build_advanced_tab(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)
        config = self._config

        self._startup_with_windows = QCheckBox("Enable")
        self._startup_initial_checked = is_startup_registered(TASK_NAME)
        self._startup_with_windows.setChecked(self._startup_initial_checked)
        form.addRow("Start with Windows\n(launch automatically when you sign in):", self._startup_with_windows)

        self._startup_status = QLabel()
        self._startup_status.setWordWrap(True)
        form.addRow("Current Active Method:", self._startup_status)

        self._startup_stale_status = QLabel()
        form.addRow("Stale Task Found:", self._startup_stale_status)

        self._repair_fast_startup = QPushButton("Repair Startup")
        self._repair_fast_startup.clicked.connect(self._on_repair_fast_startup)
        form.addRow("Use Faster Startup\n(optional, may ask for administrator approval):", self._repair_fast_startup)

        self._enable_restart = QCheckBox("Enable")
        self._enable_restart.setChecked(config.enable_application_restart)
        form.addRow("Restart After a Crash\n(ask Windows to relaunch the app):", self._enable_restart)

        self._refresh_startup_status()
        return widget

    def _on_accept(self) -> None:
        updated = self._runtime_config.with_updates(
            kef_ip=self._kef_ip.text().strip(),
            kef_mac=self._kef_mac.text().strip(),
            expected_speaker_name=self._expected_name.text().strip(),
            expected_speaker_mac=self._expected_mac.text().strip(),
            kef_input=INPUTS[self._kef_input.currentIndex()],
            wake_on_startup=self._power_behavior_checks["wake_on_startup"].isChecked(),
            wake_on_unlock_only=self._power_behavior_checks["wake_on_unlock_only"].isChecked(),
            standby_on_sleep=self._power_behavior_checks["standby_on_sleep"].isChecked(),
            standby_on_lock=self._power_behavior_checks["standby_on_lock"].isChecked(),
            endsession_standby_on_shutdown=self._power_behavior_checks["endsession_standby_on_shutdown"].isChecked(),
            auto_discover_kef_ip_by_mac=self._auto_mac.isChecked(),
            auto_discover_kef_ip_blind=self._auto_blind.isChecked(),
            enable_application_restart=self._enable_restart.isChecked(),
        )

        desired_startup = self._startup_with_windows.isChecked()
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
            self._startup_with_windows.setChecked(save_result.actual_startup_registered)
        if config_ok:
            self._apply_runtime_config(updated)
            self._log_power_behavior_state()
        if startup_changed:
            self._refresh_startup_status()

        if config_ok and startup_ok:
            QMessageBox.information(self, "Saved", "Your changes were saved.\nSpeaker behavior updates apply immediately.")
            self.accept()
        elif config_ok:
            if startup_detail:
                self._log.info(
                    f"Settings save kept config changes but failed to update Windows startup | "
                    f"desired={desired_startup} | detail={startup_detail}"
                )
            message = "Your settings were saved, but the Windows auto-start entry could not be changed."
            if startup_detail:
                message = (
                    f"{message}\n\nReason: {startup_detail}\n\n"
                    "If you want the faster Task Scheduler method, use 'Use Faster Startup' in Advanced."
                )
            QMessageBox.warning(self, "Windows Startup Was Not Updated", message)
        elif startup_ok:
            QMessageBox.warning(self, "Save Failed", "The settings file could not be saved, even though the Windows auto-start entry may already have changed.")
        else:
            QMessageBox.warning(self, "Error", "The settings file could not be saved, and the Windows auto-start entry could not be updated.")

    def _on_repair_fast_startup(self) -> None:
        ok, detail = repair_task_startup_with_uac(TASK_NAME, log=self._log)
        if not ok:
            QMessageBox.warning(self, "Startup Repair Failed", detail)
            return

        updated = self._runtime_config.with_updates(startup_registration_mode="task")
        config_ok = self._config_store.save(updated)
        self._startup_with_windows.setChecked(True)
        if config_ok:
            self._apply_runtime_config(updated)
        self._refresh_startup_status()

        if config_ok:
            QMessageBox.information(
                self,
                "Startup Repair Completed",
                "Task Scheduler startup was repaired and saved as the preferred startup method.",
            )
            return

        QMessageBox.warning(
            self,
            "Startup Repaired, but Not Saved",
            "Task Scheduler startup was repaired, but the preferred startup mode could not be written to config.json.",
        )

    def _refresh_startup_status(self) -> None:
        status = get_startup_status_view(self._config, log=self._log, task_name=TASK_NAME)
        self._startup_status.setText(f"{status.current_label}\n{status.current_detail}\nPreferred method: {status.preferred_label}")
        self._startup_stale_status.setText(status.stale_text)
        self._repair_fast_startup.setText(status.repair_button_text)
        self._repair_fast_startup.setEnabled(not status.task_is_healthy)
