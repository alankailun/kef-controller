from __future__ import annotations

import os
import subprocess
import sys

from .common import StartupLaunchSpec, is_frozen_runtime


def launch_helper_spec(extra_args: list[str]) -> StartupLaunchSpec:
    if is_frozen_runtime():
        return StartupLaunchSpec(command=os.path.abspath(sys.executable), arguments=subprocess.list2cmdline(extra_args))
    script = os.path.abspath(sys.argv[0])
    arguments = subprocess.list2cmdline([script, *extra_args])
    return StartupLaunchSpec(command=os.path.abspath(sys.executable), arguments=arguments)


def runtime_launch_spec(executable_override: str | None = None) -> StartupLaunchSpec:
    if is_frozen_runtime():
        command = os.path.abspath(executable_override or sys.executable)
        return StartupLaunchSpec(command=command)

    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    interpreter = pythonw if os.path.exists(pythonw) else sys.executable
    script = os.path.abspath(executable_override or sys.argv[0])
    return StartupLaunchSpec(command=os.path.abspath(interpreter), arguments=f'"{script}"')


def ensure_preferred_executable(_app_name: str, _log) -> StartupLaunchSpec:
    """Return the active executable without copying an onedir installation.

    Inno Setup owns deployment and upgrades.  A valid installation may live
    on any drive selected in the installer, so its active launcher is also
    the authoritative path for Windows startup registration.
    """
    if not is_frozen_runtime():
        return runtime_launch_spec()

    return StartupLaunchSpec(command=os.path.abspath(sys.executable))
