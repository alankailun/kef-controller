from __future__ import annotations

import os
from dataclasses import dataclass

from .common import (
    StartupLaunchSpec,
    launch_spec_matches_app,
    split_run_value,
    startup_specs_match,
)
from .registry import RegistryStartupEntry, read_registry_command, read_registry_commands
from .task_scheduler import ScheduledTaskEntry, list_task_launch_specs, read_task_launch_spec, task_exists


@dataclass(frozen=True, slots=True)
class StartupRegistrationState:
    task_name: str
    desired: StartupLaunchSpec
    registry_command: str
    registry_entries: tuple[RegistryStartupEntry, ...]
    registry_is_current: bool
    task_present: bool
    task_spec: StartupLaunchSpec | None
    task_is_current: bool
    task_entries: tuple[ScheduledTaskEntry, ...]
    stale_registry_entries: tuple[RegistryStartupEntry, ...]
    stale_task_entries: tuple[ScheduledTaskEntry, ...]

    @property
    def has_registry(self) -> bool:
        return bool(self.registry_command)

    @property
    def has_stale_registry(self) -> bool:
        return bool(self.stale_registry_entries)

    @property
    def has_stale_task(self) -> bool:
        return bool(self.stale_task_entries)


def normalize_task_name(task_name: str) -> str:
    return str(task_name or "").strip().lstrip("\\").lower()


def _entry_is_current_registry(entry: RegistryStartupEntry, task_name: str, desired: StartupLaunchSpec) -> bool:
    return entry.name.lower() == task_name.lower() and startup_specs_match(split_run_value(entry.command), desired)


def _entry_is_current_task(entry: ScheduledTaskEntry, task_name: str, desired: StartupLaunchSpec) -> bool:
    return normalize_task_name(entry.name) == normalize_task_name(task_name) and startup_specs_match(entry.spec, desired)


def _task_entry_exists(entries: tuple[ScheduledTaskEntry, ...], task_name: str) -> bool:
    normalized = normalize_task_name(task_name)
    return any(normalize_task_name(entry.name) == normalized for entry in entries)


def read_startup_registration_state(
    task_name: str,
    desired: StartupLaunchSpec,
    *,
    include_related_tasks: bool = True,
) -> StartupRegistrationState:
    app_name = task_name
    registry_command = read_registry_command(task_name)
    registry_entries = tuple(
        entry
        for entry in read_registry_commands()
        if launch_spec_matches_app(split_run_value(entry.command), app_name)
    )
    registry_is_current = bool(registry_command) and startup_specs_match(split_run_value(registry_command), desired)
    stale_registry_entries = tuple(
        entry
        for entry in registry_entries
        if not _entry_is_current_registry(entry, task_name, desired)
    )
    if registry_command and not registry_is_current and not any(entry.name.lower() == task_name.lower() for entry in stale_registry_entries):
        stale_registry_entries = (*stale_registry_entries, RegistryStartupEntry(task_name, registry_command))

    task_present = task_exists(task_name)
    task_spec = read_task_launch_spec(task_name) if task_present else None
    desired_exists = os.path.exists(desired.command)
    task_is_current = bool(desired_exists and startup_specs_match(task_spec, desired))

    if include_related_tasks:
        task_entries = tuple(
            entry
            for entry in list_task_launch_specs()
            if launch_spec_matches_app(entry.spec, app_name)
        )
    else:
        task_entries = ()
    if task_present and (not task_entries or not _task_entry_exists(task_entries, task_name)):
        task_entries = (*task_entries, ScheduledTaskEntry(name=task_name, spec=task_spec))

    stale_task_entries = tuple(
        entry
        for entry in task_entries
        if not _entry_is_current_task(entry, task_name, desired)
    )
    if task_present and not task_is_current and not _task_entry_exists(stale_task_entries, task_name):
        stale_task_entries = (*stale_task_entries, ScheduledTaskEntry(name=task_name, spec=task_spec))

    return StartupRegistrationState(
        task_name=task_name,
        desired=desired,
        registry_command=registry_command,
        registry_entries=registry_entries,
        registry_is_current=registry_is_current,
        task_present=task_present,
        task_spec=task_spec,
        task_is_current=task_is_current,
        task_entries=task_entries,
        stale_registry_entries=stale_registry_entries,
        stale_task_entries=stale_task_entries,
    )
