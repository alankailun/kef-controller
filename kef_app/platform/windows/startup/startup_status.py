from __future__ import annotations

from .startup_common import NullLogger, is_frozen_runtime
from .startup_launch import ensure_preferred_executable, runtime_launch_spec
from .startup_reconcile import StartupRegistrationState, read_startup_registration_state


def _desired_spec(task_name: str, log=None):
    logger = log or NullLogger()
    return ensure_preferred_executable(task_name, logger) if is_frozen_runtime() else runtime_launch_spec()


def _state(task_name: str, log=None) -> StartupRegistrationState:
    return read_startup_registration_state(
        task_name,
        _desired_spec(task_name, log=log),
        include_related_tasks=False,
    )


def is_startup_registered(task_name: str) -> bool:
    state = _state(task_name)
    return bool(state.registry_entries or state.has_registry or state.task_entries or state.task_present)


def get_startup_registration_mode(task_name: str) -> str:
    state = _state(task_name)
    if state.task_is_current:
        return "task"
    if state.registry_is_current:
        return "registry"
    if state.task_present or state.task_entries:
        return "task"
    if state.has_registry or state.registry_entries:
        return "registry"
    return "none"


def describe_startup_registration(task_name: str) -> tuple[str, str]:
    mode = get_startup_registration_mode(task_name)
    if mode == "task":
        return "Task Scheduler / At log on", "Task Scheduler startup is active."
    if mode == "registry":
        return "Registry Run", "Registry Run startup is active."
    return "Disabled", "No Windows startup entry is currently registered."


def is_task_startup_current(task_name: str, log=None) -> bool:
    return _state(task_name, log=log).task_is_current


def get_effective_startup_registration_mode(task_name: str, log=None) -> str:
    state = _state(task_name, log=log)
    if state.task_is_current:
        return "task"
    if state.registry_is_current or state.has_registry or state.registry_entries:
        return "registry"
    if state.task_present or state.task_entries:
        return "task"
    return "none"


def describe_startup_registration_status(task_name: str, log=None) -> tuple[str, str, bool, bool]:
    state = _state(task_name, log=log)
    has_extra_registry = bool(state.has_registry or state.registry_entries)
    has_extra_task = bool(state.stale_task_entries)
    has_stale_entry = has_extra_registry or has_extra_task or state.has_stale_registry
    if state.task_is_current:
        if has_stale_entry:
            return (
                "Task Scheduler / At log on",
                "Task Scheduler startup is active. Extra startup entries can be cleaned up by saving.",
                True,
                True,
            )
        return "Task Scheduler / At log on", "Task Scheduler startup is active.", False, True
    if state.registry_is_current:
        if state.task_present or state.task_entries or state.has_stale_registry:
            return (
                "Registry Run",
                "Registry Run startup is active. Extra startup entries can be cleaned up by saving.",
                True,
                True,
            )
        return "Registry Run", "Registry Run startup is active.", False, True
    if state.has_registry or state.registry_entries:
        return (
            "Registry Run / Needs Repair",
            "Registry Run exists, but it points to an old app path.",
            True,
            False,
        )
    if state.task_present or state.task_entries:
        return (
            "Task Scheduler / Needs Repair",
            "Task Scheduler exists, but it points to an old app path.",
            True,
            False,
        )
    return "Disabled", "No Windows startup entry is currently registered.", False, False
