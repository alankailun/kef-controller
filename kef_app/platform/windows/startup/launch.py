from __future__ import annotations

import os
import subprocess
import sys
from typing import Optional

from .common import StartupLaunchSpec, is_frozen_runtime


def launch_helper_spec(extra_args: list[str]) -> StartupLaunchSpec:
    if is_frozen_runtime():
        return StartupLaunchSpec(command=os.path.abspath(sys.executable), arguments=subprocess.list2cmdline(extra_args))
    script = os.path.abspath(sys.argv[0])
    arguments = subprocess.list2cmdline([script, *extra_args])
    return StartupLaunchSpec(command=os.path.abspath(sys.executable), arguments=arguments)


def preferred_install_root(app_name: str) -> str:
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        return os.path.join(local_appdata, "Programs", app_name)
    return os.path.join(os.path.expanduser("~"), "Programs", app_name)


def preferred_executable_path(app_name: str) -> str:
    return os.path.join(preferred_install_root(app_name), f"{app_name}.exe")


def runtime_launch_spec(executable_override: Optional[str] = None) -> StartupLaunchSpec:
    if is_frozen_runtime():
        command = os.path.abspath(executable_override or sys.executable)
        return StartupLaunchSpec(command=command)

    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    interpreter = pythonw if os.path.exists(pythonw) else sys.executable
    script = os.path.abspath(executable_override or sys.argv[0])
    return StartupLaunchSpec(command=os.path.abspath(interpreter), arguments=f'"{script}"')


def ensure_preferred_executable(app_name: str, log) -> StartupLaunchSpec:
    """Return the active executable without copying an onedir installation.

    Inno Setup owns deployment and upgrades.  Copying only the launcher EXE
    would leave its required ``runtime`` directory behind, so a frozen build
    launched outside the normal install folder is merely reported and remains
    the executable used for any startup registration.
    """
    if not is_frozen_runtime():
        return runtime_launch_spec()

    source_exe = os.path.abspath(sys.executable)
    target_exe = os.path.abspath(preferred_executable_path(app_name))
    if os.path.normcase(source_exe) != os.path.normcase(target_exe):
        # Status checks use NullLogger, whose deliberately small interface is
        # limited to info().  This remains a visible startup diagnostic for
        # the real application logger without making read-only checks fail.
        log.info(
            "Running outside the Inno Setup installation directory; no files were copied | "
            f"source={source_exe} | expected={target_exe}"
        )
    return StartupLaunchSpec(command=source_exe)
