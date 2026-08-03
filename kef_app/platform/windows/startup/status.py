from __future__ import annotations

from dataclasses import dataclass

from .common import NullLogger, is_frozen_runtime
from .launch import ensure_preferred_executable, runtime_launch_spec
from .reconcile import StartupRegistrationState, read_startup_registration_state


@dataclass(frozen=True, slots=True)
class StartupRegistrationSnapshot:
    registered: bool
    effective_mode: str
    label: str
    detail: str
    cleanup_needed: bool
    healthy: bool


def _desired_spec(task_name: str, log=None):
    logger = log or NullLogger()
    return ensure_preferred_executable(task_name, logger) if is_frozen_runtime() else runtime_launch_spec()


def _state(task_name: str, log=None) -> StartupRegistrationState:
    return read_startup_registration_state(
        task_name,
        _desired_spec(task_name, log=log),
        include_related_tasks=False,
    )


def _effective_mode(state: StartupRegistrationState) -> str:
    if state.task_is_current:
        return "task"
    if state.registry_is_current:
        return "registry"
    if state.task_present or state.task_entries:
        return "task"
    if state.has_registry or state.registry_entries:
        return "registry"
    return "none"


def read_startup_registration_snapshot(task_name: str, log=None) -> StartupRegistrationSnapshot:
    """Read Windows startup state once and derive every UI-facing property."""
    state = _state(task_name, log=log)
    registered = bool(state.registry_entries or state.has_registry or state.task_entries or state.task_present)
    effective_mode = _effective_mode(state)
    has_extra_registry = bool(state.has_registry or state.registry_entries)
    has_extra_task = bool(state.stale_task_entries)
    has_stale_entry = has_extra_registry or has_extra_task or state.has_stale_registry
    if state.task_is_current:
        if has_stale_entry:
            return StartupRegistrationSnapshot(
                registered, effective_mode, "Task Scheduler / At log on",
                "Task Scheduler startup is active. Extra startup entries can be cleaned up by saving.", True, True,
            )
        return StartupRegistrationSnapshot(registered, effective_mode, "Task Scheduler / At log on", "Task Scheduler startup is active.", False, True)
    if state.registry_is_current:
        if state.task_present or state.task_entries or state.has_stale_registry:
            return StartupRegistrationSnapshot(
                registered, effective_mode, "Registry Run",
                "Registry Run startup is active. Extra startup entries can be cleaned up by saving.", True, True,
            )
        return StartupRegistrationSnapshot(registered, effective_mode, "Registry Run", "Registry Run startup is active.", False, True)
    if state.has_registry or state.registry_entries:
        return StartupRegistrationSnapshot(
            registered, effective_mode, "Registry Run / Needs Repair",
            "Registry Run exists, but it points to an old app path.", True, False,
        )
    if state.task_present or state.task_entries:
        return StartupRegistrationSnapshot(
            registered, effective_mode, "Task Scheduler / Needs Repair",
            "Task Scheduler exists, but it points to an old app path.", True, False,
        )
    return StartupRegistrationSnapshot(
        registered, effective_mode, "Disabled", "No Windows startup entry is currently registered.", False, False,
    )


def is_startup_registered(task_name: str) -> bool:
    return read_startup_registration_snapshot(task_name).registered


def get_effective_startup_registration_mode(task_name: str, log=None) -> str:
    return read_startup_registration_snapshot(task_name, log=log).effective_mode


def describe_startup_registration_status(task_name: str, log=None) -> tuple[str, str, bool, bool]:
    snapshot = read_startup_registration_snapshot(task_name, log=log)
    return snapshot.label, snapshot.detail, snapshot.cleanup_needed, snapshot.healthy
