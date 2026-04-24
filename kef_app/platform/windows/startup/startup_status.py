from __future__ import annotations

import os

from .startup_common import NullLogger, is_frozen_runtime
from .startup_launch import ensure_preferred_executable, runtime_launch_spec
from .startup_registry import read_registry_command
from .task_scheduler import read_task_launch_spec, task_exists


def is_startup_registered(task_name: str) -> bool:
    return bool(read_registry_command(task_name)) or task_exists(task_name)


def get_startup_registration_mode(task_name: str) -> str:
    if task_exists(task_name):
        return "task"
    if read_registry_command(task_name):
        return "registry"
    return "none"


def describe_startup_registration(task_name: str) -> tuple[str, str]:
    mode = get_startup_registration_mode(task_name)
    if mode == "task":
        return "Task Scheduler / At log on", "Fast login startup is active."
    if mode == "registry":
        return "Registry Run", "Normal login startup is active."
    return "Disabled", "No Windows startup entry is currently registered."


def is_task_startup_current(task_name: str, log=None) -> bool:
    current_task = read_task_launch_spec(task_name)
    if current_task is None:
        return False
    logger = log or NullLogger()
    desired = ensure_preferred_executable(task_name, logger) if is_frozen_runtime() else runtime_launch_spec()
    if not os.path.exists(desired.command):
        return False
    return current_task.run_value == desired.run_value


def get_effective_startup_registration_mode(task_name: str, log=None) -> str:
    registry_command = read_registry_command(task_name)
    has_task = task_exists(task_name)
    task_is_current = has_task and is_task_startup_current(task_name, log=log)
    if task_is_current:
        return "task"
    if registry_command:
        return "registry"
    if has_task:
        return "task"
    return "none"


def describe_startup_registration_status(task_name: str, log=None) -> tuple[str, str, bool, bool]:
    registry_command = read_registry_command(task_name)
    has_task = task_exists(task_name)
    task_is_current = has_task and is_task_startup_current(task_name, log=log)
    if task_is_current:
        return "Task Scheduler / At log on", "Fast login startup is active.", False, True
    if registry_command:
        if has_task:
            return (
                "Registry Run",
                "Normal login startup is active. A stale Task Scheduler entry also exists and can be repaired.",
                True,
                False,
            )
        return "Registry Run", "Normal login startup is active.", False, False
    if has_task:
        return (
            "Task Scheduler / Needs Repair",
            "A Task Scheduler startup entry exists, but it does not point to the current app build.",
            True,
            False,
        )
    return "Disabled", "No Windows startup entry is currently registered.", False, False
