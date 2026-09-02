from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from ...config import AppConfig
from ...platform.windows.startup.common import (
    get_last_startup_error,
    normalize_startup_mode,
    startup_error_suggests_repair,
)
from ...platform.windows.startup.service import set_startup_registered
from ...platform.windows.startup.status import read_startup_registration_snapshot
from ...storage import UserConfigStore
from ...structured_logging import log_structured

TASK_NAME = "KEF Controller"


def startup_mode_for_ui(value: str) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"off", "none", "disabled", "disable"}:
        return "off"
    normalized = normalize_startup_mode(value)
    if normalized == "registry":
        return "registry"
    if normalized == "off":
        return "off"
    return "task"


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
        "standby_on_display_off",
        "Put Speaker in Standby When the Screen Turns Off",
        "When the screen turns off, put the speaker into standby.",
    ),
    SpeakerPowerOption(
        "wake_on_display_on",
        "Wake Speaker When the Screen Turns On",
        "If the screen-off action put the speaker into standby, wake it when the screen turns on.",
    ),
    SpeakerPowerOption(
        "wake_on_unlock_only",
        "Wake Speaker When Windows Unlocks",
        "After sleep or resume, wait for Windows to unlock before waking the speaker.",
    ),
    SpeakerPowerOption(
        "standby_on_lock",
        "Put Speaker in Standby When Windows Locks",
        "When Windows locks, put the speaker into standby.",
    ),
    SpeakerPowerOption(
        "standby_on_sleep",
        "Put Speaker in Standby When Windows Sleeps",
        "When Windows goes to sleep, put the speaker into standby.",
    ),
    SpeakerPowerOption(
        "standby_on_lid_close",
        "Put Speaker in Standby When the Laptop Lid Closes",
        "When the laptop lid closes, put the speaker into standby.",
    ),
    SpeakerPowerOption(
        "endsession_standby_on_shutdown",
        "Put Speaker in Standby When Windows Shuts Down",
        "When Windows shuts down or signs out, put the speaker into standby.",
    ),
)

SPEAKER_POWER_OPTIONS_BY_KEY = {option.key: option for option in SPEAKER_POWER_OPTIONS}


@dataclass(frozen=True)
class SettingsSaveResult:
    updated: AppConfig
    config_ok: bool
    startup_ok: bool
    startup_detail: str
    actual_startup_registered: bool
    actual_startup_mode: str


def get_speaker_power_disabled_reason(key: str) -> str:
    option = SPEAKER_POWER_OPTIONS_BY_KEY.get(key)
    return f"{option.title if option else key} is currently off."


def save_settings_and_sync_startup(
    updated: AppConfig,
    *,
    config_store: UserConfigStore,
    desired_startup: bool,
    startup_mode_changed: bool,
    log: logging.Logger,
    task_name: str = TASK_NAME,
    retry_disable_with_uac: Callable[[], bool] | None = None,
    retry_enable_task_with_uac: Callable[[], bool] | None = None,
    retry_enable_registry_with_uac: Callable[[], bool] | None = None,
) -> SettingsSaveResult:
    config_ok = config_store.save(updated)
    startup_ok = True
    before = read_startup_registration_snapshot(task_name, log=log)
    actual_startup_registered_before = before.registered
    actual_startup_mode_before = before.effective_mode
    selected_mode = startup_mode_for_ui(updated.startup_registration_mode)
    actual_mode_matches_selection = startup_mode_for_ui(actual_startup_mode_before) == selected_mode
    startup_changed = (
        desired_startup != actual_startup_registered_before
        or (
            desired_startup
            and (
                startup_mode_changed
                or before.cleanup_needed
                or not before.healthy
                or not actual_mode_matches_selection
            )
        )
    )

    if startup_changed:
        startup_ok = set_startup_registered(
            desired_startup,
            task_name=task_name,
            log=log,
            mode=updated.startup_registration_mode,
        )

    startup_detail = get_last_startup_error()

    if (
        startup_changed
        and desired_startup
        and selected_mode == "task"
        and not startup_ok
        and startup_error_suggests_repair(startup_detail)
        and retry_enable_task_with_uac
    ):
        if retry_enable_task_with_uac():
            startup_ok = True
            startup_detail = ""
        else:
            startup_detail = get_last_startup_error() or startup_detail

    if (
        startup_changed
        and desired_startup
        and selected_mode == "registry"
        and not startup_ok
        and startup_error_suggests_repair(startup_detail)
        and retry_enable_registry_with_uac
    ):
        if retry_enable_registry_with_uac():
            startup_ok = set_startup_registered(
                True,
                task_name=task_name,
                log=log,
                mode="registry",
            )
            startup_detail = get_last_startup_error()
        else:
            startup_detail = get_last_startup_error() or startup_detail

    if (
        startup_changed
        and not desired_startup
        and not startup_ok
        and startup_error_suggests_repair(startup_detail)
        and retry_disable_with_uac
        and retry_disable_with_uac()
    ):
        startup_ok = set_startup_registered(False, task_name=task_name, log=log, mode="off")
        startup_detail = get_last_startup_error()
    elif startup_changed and not desired_startup and not startup_ok and retry_disable_with_uac:
        startup_detail = get_last_startup_error() or startup_detail

    after = read_startup_registration_snapshot(task_name, log=log)
    actual_startup_registered = after.registered
    actual_startup_mode = after.effective_mode

    if (
        actual_startup_registered
        and actual_startup_mode in {"task", "registry"}
        and startup_mode_for_ui(updated.startup_registration_mode) != actual_startup_mode
    ):
        # A failed change (including a cancelled UAC prompt) must show the
        # startup method that Windows actually retained, not the requested
        # method.  This also restores the master switch after a failed disable.
        updated = updated.with_updates(startup_registration_mode=actual_startup_mode)
        if config_ok:
            config_ok = config_store.save(updated)
        if config_ok:
            log_structured(
                log,
                "STEP",
                action="STARTUP_REGISTRATION",
                reason="startup_update",
                step="reconcile",
                status="retained_existing_registration",
                actual_mode=actual_startup_mode,
            )

    return SettingsSaveResult(
        updated=updated,
        config_ok=config_ok,
        startup_ok=startup_ok,
        startup_detail=startup_detail,
        actual_startup_registered=actual_startup_registered,
        actual_startup_mode=actual_startup_mode,
    )
