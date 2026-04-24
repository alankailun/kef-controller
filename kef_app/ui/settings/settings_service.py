from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

from ...config import AppConfig
from ...storage import UserConfigStore
from ...devices.speaker_models import INPUT_SOURCE_OPTIONS
from ...platform.windows import (
    describe_startup_registration_status,
    get_effective_startup_registration_mode,
    get_last_startup_error,
    is_startup_registered,
    set_startup_registered,
    startup_error_suggests_repair,
)

TASK_NAME = "KEF Controller"
INPUTS = [value for _, value in INPUT_SOURCE_OPTIONS]


@dataclass(frozen=True)
class SpeakerPowerOption:
    key: str
    title: str
    description: str


SPEAKER_POWER_OPTIONS: tuple[SpeakerPowerOption, ...] = (
    SpeakerPowerOption(
        "wake_on_startup",
        "Wake Speaker When the App Starts",
        "When the app starts, wake the speaker and switch to the default input.",
    ),
    SpeakerPowerOption(
        "endsession_standby_on_shutdown",
        "Put Speaker in Standby When Windows Shuts Down",
        "When Windows shuts down or signs out, put the speaker into standby.",
    ),
    SpeakerPowerOption(
        "standby_on_lock",
        "Put Speaker in Standby When Windows Locks",
        "When Windows locks, put the speaker into standby.",
    ),
    SpeakerPowerOption(
        "wake_on_unlock_only",
        "Wake Speaker When Windows Unlocks",
        "After sleep or resume, wait for Windows to unlock before waking the speaker.",
    ),
    SpeakerPowerOption(
        "standby_on_sleep",
        "Put Speaker in Standby When Windows Sleeps",
        "When Windows goes to sleep, put the speaker into standby.",
    ),
)

SPEAKER_POWER_OPTIONS_BY_KEY = {option.key: option for option in SPEAKER_POWER_OPTIONS}


@dataclass(frozen=True)
class SettingsSaveResult:
    updated: AppConfig
    config_ok: bool
    startup_ok: bool
    startup_changed: bool
    startup_detail: str
    actual_startup_registered: bool
    actual_startup_mode: str
    startup_initial_checked: bool


@dataclass(frozen=True)
class StartupStatusView:
    current_label: str
    current_detail: str
    stale_task_found: bool
    task_is_healthy: bool
    preferred_label: str
    repair_button_text: str
    stale_text: str


def apply_runtime_config(
    runtime_config: AppConfig,
    updated: AppConfig,
    editable_fields: tuple[str, ...],
) -> AppConfig:
    for field_name in editable_fields:
        setattr(runtime_config, field_name, getattr(updated, field_name))
    return runtime_config


def log_power_behavior_state_message(config: AppConfig) -> str:
    return (
        "POWER_BEHAVIOR_APPLIED | "
        f"wake_on_startup={config.wake_on_startup} | "
        f"shutdown_standby={config.endsession_standby_on_shutdown} | "
        f"lock_standby={config.standby_on_lock} | "
        f"wake_after_unlock={config.wake_on_unlock_only} | "
        f"sleep_standby={config.standby_on_sleep}"
    )


def get_speaker_power_disabled_reason(key: str) -> str:
    return f"{SPEAKER_POWER_OPTIONS_BY_KEY[key].title} is currently off."


def save_settings_and_sync_startup(
    updated: AppConfig,
    *,
    config_store: UserConfigStore,
    desired_startup: bool,
    startup_initial_checked: bool,
    log: logging.Logger,
    task_name: str = TASK_NAME,
    retry_disable_with_uac: Optional[Callable[[], bool]] = None,
) -> SettingsSaveResult:
    config_ok = config_store.save(updated)
    startup_ok = True
    startup_changed = desired_startup != startup_initial_checked

    if startup_changed:
        startup_ok = set_startup_registered(
            desired_startup,
            task_name=task_name,
            log=log,
            mode="auto" if desired_startup else updated.startup_registration_mode,
        )

    startup_detail = get_last_startup_error()

    if (
        startup_changed
        and not desired_startup
        and not startup_ok
        and startup_error_suggests_repair(startup_detail)
        and retry_disable_with_uac
        and retry_disable_with_uac()
    ):
        startup_ok = True
        startup_detail = ""

    actual_startup_registered = is_startup_registered(task_name)
    next_startup_initial_checked = actual_startup_registered if startup_changed else startup_initial_checked
    actual_startup_mode = (
        get_effective_startup_registration_mode(task_name, log=log)
        if actual_startup_registered and startup_ok
        else "none"
    )

    if (
        actual_startup_registered
        and startup_ok
        and actual_startup_mode == "registry"
        and updated.startup_registration_mode != "registry"
    ):
        updated = updated.with_updates(startup_registration_mode="registry")
        if config_ok:
            config_ok = config_store.save(updated)
        if config_ok:
            log.info("Windows startup was enabled with Registry Run; updated the preferred startup mode to registry")

    return SettingsSaveResult(
        updated=updated,
        config_ok=config_ok,
        startup_ok=startup_ok,
        startup_changed=startup_changed,
        startup_detail=startup_detail,
        actual_startup_registered=actual_startup_registered,
        actual_startup_mode=actual_startup_mode,
        startup_initial_checked=next_startup_initial_checked,
    )


def get_startup_status_view(
    config: AppConfig,
    *,
    log: logging.Logger,
    task_name: str = TASK_NAME,
) -> StartupStatusView:
    current_label, current_detail, stale_task_found, task_is_healthy = describe_startup_registration_status(
        task_name,
        log=log,
    )
    preferred_label = "Task Scheduler / At log on" if config.startup_registration_mode == "task" else "Registry Run"
    return StartupStatusView(
        current_label=current_label,
        current_detail=current_detail,
        stale_task_found=stale_task_found,
        task_is_healthy=task_is_healthy,
        preferred_label=preferred_label,
        repair_button_text="Faster Startup Is Active" if task_is_healthy else "Use Task Scheduler",
        stale_text="Yes" if stale_task_found else "No",
    )
