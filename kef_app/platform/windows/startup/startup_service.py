from __future__ import annotations

import os

from .startup_common import (
    NullLogger,
    clear_last_startup_error,
    get_last_startup_error,
    is_frozen_runtime,
    log_startup_failure,
    normalize_startup_mode,
    set_last_startup_error,
)
from .startup_launch import ensure_preferred_executable, runtime_launch_spec
from .startup_registry import delete_registry_command, read_registry_command, write_registry_command
from .startup_status import get_effective_startup_registration_mode, get_startup_registration_mode
from .task_scheduler import create_task, delete_task, read_task_launch_spec, task_exists


def set_startup_registered(
    enable: bool,
    task_name: str,
    launch_spec=None,
    log=None,
    mode: str = "auto",
) -> bool:
    logger = log or NullLogger()
    normalized_mode = normalize_startup_mode(mode)
    clear_last_startup_error()

    if not enable:
        delete_registry_command(task_name)
        deleted, detail = delete_task(task_name, logger)
        if not deleted:
            set_last_startup_error(f"Could not remove the Task Scheduler entry: {detail}")
            log_startup_failure(logger, "disable", task_name, get_last_startup_error())
        return deleted

    spec = launch_spec or (ensure_preferred_executable(task_name, logger) if is_frozen_runtime() else runtime_launch_spec())
    if normalized_mode == "registry":
        delete_task(task_name, logger)
        registry_ok, registry_detail = write_registry_command(task_name, spec.run_value)
        if not registry_ok:
            set_last_startup_error(f"Could not write the registry startup entry: {registry_detail}")
            log_startup_failure(logger, "enable_registry", task_name, get_last_startup_error())
        return registry_ok

    task_ok, task_detail = create_task(task_name, spec, logger)
    if task_ok:
        delete_registry_command(task_name)
        clear_last_startup_error()
        return True

    if normalized_mode == "task":
        set_last_startup_error(f"Could not create the Task Scheduler entry: {task_detail}")
        log_startup_failure(logger, "enable_task", task_name, get_last_startup_error())
        return False

    registry_ok, registry_detail = write_registry_command(task_name, spec.run_value)
    if registry_ok:
        logger.info(f"Fell back to registry startup | task={task_name} | command={spec.run_value}")
        clear_last_startup_error()
    else:
        set_last_startup_error(
            "Task Scheduler creation failed and the registry fallback also failed. "
            f"Task Scheduler: {task_detail} | Registry: {registry_detail}"
        )
        log_startup_failure(logger, "enable_auto", task_name, get_last_startup_error())
    return registry_ok


def ensure_startup_registration(task_name: str, log, mode: str = "auto") -> bool:
    if not is_frozen_runtime():
        return False

    normalized_mode = normalize_startup_mode(mode)
    registry_command = read_registry_command(task_name)
    has_task = task_exists(task_name)
    if not registry_command and not has_task:
        return False

    desired = ensure_preferred_executable(task_name, log)
    current_task = read_task_launch_spec(task_name)
    current_task_run_value = current_task.run_value if current_task is not None else ""
    current_mode = get_startup_registration_mode(task_name)

    if normalized_mode == "registry":
        needs_update = (
            current_mode != "registry"
            or registry_command != desired.run_value
            or not os.path.exists(desired.command)
        )
    elif normalized_mode == "task":
        needs_update = (
            current_mode != "task"
            or current_task_run_value != desired.run_value
            or not os.path.exists(desired.command)
        )
    else:
        needs_update = (
            registry_command != ""
            or current_task_run_value != desired.run_value
            or not has_task
            or not os.path.exists(desired.command)
        )

    if not needs_update:
        return False

    ok = set_startup_registered(True, task_name=task_name, launch_spec=desired, log=log, mode=normalized_mode)
    if ok:
        actual_mode = get_effective_startup_registration_mode(task_name, log=log)
        trigger = "At log on" if actual_mode == "task" else "registry_run"
        log.info(
            f"Startup registration self-healed | task={task_name} | requested_mode={normalized_mode} | "
            f"method={actual_mode} | trigger={trigger} | command={desired.run_value}"
        )
    return ok
