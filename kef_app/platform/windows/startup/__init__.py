from .common import StartupLaunchSpec
from .elevation import maybe_handle_startup_task_repair, remove_startup_task_with_uac, repair_task_startup_with_uac
from .launch import ensure_preferred_executable, runtime_launch_spec
from .service import ensure_startup_registration, set_startup_registered
from .status import describe_startup_registration_status, get_effective_startup_registration_mode

__all__ = [
    "StartupLaunchSpec",
    "describe_startup_registration_status",
    "ensure_preferred_executable",
    "ensure_startup_registration",
    "get_effective_startup_registration_mode",
    "maybe_handle_startup_task_repair",
    "remove_startup_task_with_uac",
    "repair_task_startup_with_uac",
    "runtime_launch_spec",
    "set_startup_registered",
]
