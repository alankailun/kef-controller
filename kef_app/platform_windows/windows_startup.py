from __future__ import annotations

"""Facade for Windows startup registration helpers.

This module keeps the original public API stable while delegating the
implementation to smaller helpers under ``startup_support``.
"""

from .startup_support.common import (
    REMOVE_TASK_FLAG,
    REPAIR_TASK_FLAG,
    STARTUP_KEY,
    TASK_NAME_FLAG,
    VALID_STARTUP_MODES,
    StartupLaunchSpec,
    get_last_startup_error,
    normalize_startup_mode,
    startup_error_suggests_repair,
)
from .startup_support.elevation import (
    maybe_handle_startup_task_repair,
    remove_startup_task_with_uac,
    repair_task_startup_with_uac,
)
from .startup_support.launch import ensure_preferred_executable, preferred_executable_path, runtime_launch_spec
from .startup_support.service import ensure_startup_registration, set_startup_registered
from .startup_support.status import (
    describe_startup_registration,
    describe_startup_registration_status,
    get_effective_startup_registration_mode,
    get_startup_registration_mode,
    is_startup_registered,
    is_task_startup_current,
)

__all__ = [
    "REMOVE_TASK_FLAG",
    "REPAIR_TASK_FLAG",
    "STARTUP_KEY",
    "TASK_NAME_FLAG",
    "VALID_STARTUP_MODES",
    "StartupLaunchSpec",
    "describe_startup_registration",
    "describe_startup_registration_status",
    "ensure_preferred_executable",
    "ensure_startup_registration",
    "get_effective_startup_registration_mode",
    "get_last_startup_error",
    "get_startup_registration_mode",
    "is_startup_registered",
    "is_task_startup_current",
    "maybe_handle_startup_task_repair",
    "normalize_startup_mode",
    "preferred_executable_path",
    "remove_startup_task_with_uac",
    "repair_task_startup_with_uac",
    "runtime_launch_spec",
    "set_startup_registered",
    "startup_error_suggests_repair",
]
