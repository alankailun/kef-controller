"""Facade for Windows startup registration helpers.

This module keeps the package-level Windows API stable while delegating the
implementation to smaller helpers under ``startup``.
"""

from __future__ import annotations

from .startup.common import (
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
from .startup.elevation import (
    maybe_handle_startup_task_repair,
    remove_startup_task_with_uac,
    repair_task_startup_with_uac,
)
from .startup.launch import ensure_preferred_executable, runtime_launch_spec
from .startup.service import ensure_startup_registration, set_startup_registered
from .startup.status import (
    describe_startup_registration_status,
    get_effective_startup_registration_mode,
    is_startup_registered,
    read_startup_registration_snapshot,
)

__all__ = [
    "REMOVE_TASK_FLAG",
    "REPAIR_TASK_FLAG",
    "STARTUP_KEY",
    "TASK_NAME_FLAG",
    "VALID_STARTUP_MODES",
    "StartupLaunchSpec",
    "describe_startup_registration_status",
    "ensure_preferred_executable",
    "ensure_startup_registration",
    "get_effective_startup_registration_mode",
    "get_last_startup_error",
    "is_startup_registered",
    "maybe_handle_startup_task_repair",
    "normalize_startup_mode",
    "remove_startup_task_with_uac",
    "repair_task_startup_with_uac",
    "runtime_launch_spec",
    "read_startup_registration_snapshot",
    "set_startup_registered",
    "startup_error_suggests_repair",
]
