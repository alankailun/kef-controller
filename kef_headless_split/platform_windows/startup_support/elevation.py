from __future__ import annotations

import sys
from typing import Optional

from .common import (
    NullLogger,
    REPAIR_TASK_FLAG,
    REMOVE_TASK_FLAG,
    TASK_NAME_FLAG,
    clear_last_startup_error,
    cli_flag_value,
    format_process_error,
    get_last_startup_error,
    hidden_run,
    log_startup_failure,
    quote_ps,
    set_last_startup_error,
)
from .launch import ensure_preferred_executable, launch_helper_spec, runtime_launch_spec
from .registry import delete_registry_command
from .service import set_startup_registered
from .task_scheduler import delete_task, read_task_launch_spec, task_exists


def maybe_handle_startup_task_repair(log=None) -> Optional[int]:
    argv = sys.argv[1:]
    logger = log or NullLogger()
    task_name = cli_flag_value(argv, TASK_NAME_FLAG) or "KEF Controller"
    if REPAIR_TASK_FLAG in argv:
        launch_spec = ensure_preferred_executable(task_name, logger) if getattr(sys, "frozen", False) else runtime_launch_spec()
        ok = set_startup_registered(True, task_name=task_name, launch_spec=launch_spec, log=logger, mode="task")
        return 0 if ok else 1
    if REMOVE_TASK_FLAG in argv:
        delete_registry_command(task_name)
        deleted, detail = delete_task(task_name, logger)
        if not deleted:
            set_last_startup_error(f"Could not remove the Task Scheduler entry: {detail}")
            log_startup_failure(logger, "disable", task_name, get_last_startup_error())
        else:
            clear_last_startup_error()
        return 0 if deleted else 1
    return None


def repair_task_startup_with_uac(task_name: str, log=None) -> tuple[bool, str]:
    logger = log or NullLogger()
    desired = ensure_preferred_executable(task_name, logger) if getattr(sys, "frozen", False) else runtime_launch_spec()
    current_task = read_task_launch_spec(task_name)
    if current_task is not None and current_task.run_value == desired.run_value:
        delete_registry_command(task_name)
        return True, "Task Scheduler startup is already configured."

    helper = launch_helper_spec([REPAIR_TASK_FLAG, TASK_NAME_FLAG, task_name])
    powershell = (
        "$p = Start-Process "
        f"-FilePath {quote_ps(helper.command)} "
        f"-ArgumentList {quote_ps(helper.arguments)} "
        "-Verb RunAs -WindowStyle Hidden -Wait -PassThru; "
        "exit $p.ExitCode"
    )
    try:
        completed = hidden_run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", powershell],
            timeout=120,
        )
    except Exception as exc:
        set_last_startup_error(f"Failed to start elevated repair helper: {exc}")
        log_startup_failure(logger, "repair", task_name, get_last_startup_error())
        return False, f"Failed to start elevated repair helper: {exc}"

    if completed.returncode == 0:
        clear_last_startup_error()
        return True, "Task Scheduler startup was repaired."

    message = format_process_error(completed, "Elevated repair failed")
    lowered = message.lower()
    if "canceled" in lowered or "cancelled" in lowered:
        set_last_startup_error("UAC was canceled.")
        log_startup_failure(logger, "repair", task_name, get_last_startup_error())
        return False, "UAC was canceled."
    set_last_startup_error(f"Elevated repair failed: {message}")
    log_startup_failure(logger, "repair", task_name, get_last_startup_error())
    return False, f"Elevated repair failed: {message}"


def remove_startup_task_with_uac(task_name: str, log=None) -> tuple[bool, str]:
    logger = log or NullLogger()
    if not task_exists(task_name):
        delete_registry_command(task_name)
        clear_last_startup_error()
        return True, "Windows startup task is already removed."

    helper = launch_helper_spec([REMOVE_TASK_FLAG, TASK_NAME_FLAG, task_name])
    powershell = (
        "$p = Start-Process "
        f"-FilePath {quote_ps(helper.command)} "
        f"-ArgumentList {quote_ps(helper.arguments)} "
        "-Verb RunAs -WindowStyle Hidden -Wait -PassThru; "
        "exit $p.ExitCode"
    )
    try:
        completed = hidden_run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", powershell],
            timeout=120,
        )
    except Exception as exc:
        set_last_startup_error(f"Failed to start elevated startup removal helper: {exc}")
        log_startup_failure(logger, "disable_repair", task_name, get_last_startup_error())
        return False, f"Failed to start elevated startup removal helper: {exc}"

    if completed.returncode == 0:
        delete_registry_command(task_name)
        clear_last_startup_error()
        return True, "Windows startup entry was removed."

    message = format_process_error(completed, "Elevated startup removal failed")
    lowered = message.lower()
    if "canceled" in lowered or "cancelled" in lowered:
        set_last_startup_error("UAC was canceled.")
        log_startup_failure(logger, "disable_repair", task_name, get_last_startup_error())
        return False, "UAC was canceled."
    set_last_startup_error(f"Elevated startup removal failed: {message}")
    log_startup_failure(logger, "disable_repair", task_name, get_last_startup_error())
    return False, f"Elevated startup removal failed: {message}"
