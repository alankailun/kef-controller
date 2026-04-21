from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass


STARTUP_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
TASK_XML_NS = {"task": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
VALID_STARTUP_MODES = {"auto", "task", "registry"}
REPAIR_TASK_FLAG = "--repair-startup-task"
REMOVE_TASK_FLAG = "--remove-startup-task"
TASK_NAME_FLAG = "--startup-task-name"
_LAST_STARTUP_ERROR = ""
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class NullLogger:
    def info(self, _message: str) -> None:
        return None


@dataclass(slots=True)
class StartupLaunchSpec:
    command: str
    arguments: str = ""

    @property
    def run_value(self) -> str:
        if self.arguments:
            return f'"{self.command}" {self.arguments}'
        return f'"{self.command}"'


def set_last_startup_error(detail: str) -> None:
    global _LAST_STARTUP_ERROR
    _LAST_STARTUP_ERROR = str(detail or "").strip()


def clear_last_startup_error() -> None:
    set_last_startup_error("")


def get_last_startup_error() -> str:
    return _LAST_STARTUP_ERROR


def startup_error_suggests_repair(detail: str) -> bool:
    text = str(detail or "").strip().lower()
    if not text:
        return False
    return "access is denied" in text or "access denied" in text


def format_process_error(completed: subprocess.CompletedProcess[str], default: str) -> str:
    stderr = (completed.stderr or "").strip()
    stdout = (completed.stdout or "").strip()
    if stderr:
        return stderr
    if stdout:
        return stdout
    return f"{default} (exit code {completed.returncode})"


def log_startup_failure(logger, action: str, task_name: str, detail: str) -> None:
    logger.info(f"Windows startup update failed | action={action} | task={task_name} | detail={detail}")


def hidden_run(args: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        timeout=timeout,
        check=False,
        creationflags=_CREATE_NO_WINDOW,
    )


def normalize_startup_mode(mode: str) -> str:
    normalized = str(mode or "auto").strip().lower()
    return normalized if normalized in VALID_STARTUP_MODES else "auto"


def quote_ps(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def strip_wrapping_quotes(value: str) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        return text[1:-1]
    return text


def cli_flag_value(argv: list[str], flag: str) -> str:
    if flag not in argv:
        return ""
    idx = argv.index(flag)
    if idx + 1 >= len(argv):
        return ""
    return argv[idx + 1]


def is_frozen_runtime() -> bool:
    return bool(getattr(sys, "frozen", False))
