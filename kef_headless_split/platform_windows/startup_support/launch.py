from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
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
    if not is_frozen_runtime():
        return runtime_launch_spec()

    source_exe = os.path.abspath(sys.executable)
    target_exe = os.path.abspath(preferred_executable_path(app_name))
    if os.path.normcase(source_exe) == os.path.normcase(target_exe):
        return StartupLaunchSpec(command=target_exe)

    try:
        os.makedirs(os.path.dirname(target_exe), exist_ok=True)
        source_stat = os.stat(source_exe)
        if os.path.exists(target_exe):
            target_stat = os.stat(target_exe)
            if int(source_stat.st_size) == int(target_stat.st_size) and int(source_stat.st_mtime) == int(target_stat.st_mtime):
                log.info(f"Startup install path already up to date | target={target_exe}")
                return StartupLaunchSpec(command=target_exe)

        fd, tmp_path = tempfile.mkstemp(prefix="kef_install_", suffix=".exe", dir=os.path.dirname(target_exe))
        os.close(fd)
        try:
            shutil.copy2(source_exe, tmp_path)
            os.replace(tmp_path, target_exe)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        log.info(f"Installed executable to preferred path | source={source_exe} | target={target_exe}")
        return StartupLaunchSpec(command=target_exe)
    except Exception as exc:
        log.info(
            f"Preferred install path sync failed, keeping current executable | "
            f"source={source_exe} | target={target_exe} | {exc}"
        )
        return StartupLaunchSpec(command=source_exe)
