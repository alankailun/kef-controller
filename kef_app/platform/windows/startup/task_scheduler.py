from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Optional

from .startup_common import TASK_XML_NS, StartupLaunchSpec, format_process_error, hidden_run, strip_wrapping_quotes


def task_exists(task_name: str) -> bool:
    try:
        completed = hidden_run(["schtasks", "/query", "/tn", task_name], timeout=5)
        return completed.returncode == 0
    except Exception:
        return False


def read_task_launch_spec(task_name: str) -> Optional[StartupLaunchSpec]:
    try:
        completed = hidden_run(["schtasks", "/query", "/tn", task_name, "/xml"], timeout=5)
        if completed.returncode != 0:
            return None

        root = ET.fromstring(completed.stdout)
        command = strip_wrapping_quotes(
            root.findtext(".//task:Actions/task:Exec/task:Command", default="", namespaces=TASK_XML_NS)
        )
        arguments = root.findtext(".//task:Actions/task:Exec/task:Arguments", default="", namespaces=TASK_XML_NS).strip()
        if not command:
            return None
        return StartupLaunchSpec(command=command, arguments=arguments)
    except Exception:
        return None


def delete_task(task_name: str, log=None) -> tuple[bool, str]:
    try:
        completed = hidden_run(["schtasks", "/delete", "/tn", task_name, "/f"], timeout=10)
        deleted = completed.returncode == 0 or "cannot find" in (completed.stderr or "").lower()
        if not deleted and log is not None:
            detail = format_process_error(completed, "Task deletion failed")
            log.info(f"Failed to delete startup task | task={task_name} | detail={detail}")
            return False, detail
        return True, ""
    except Exception as exc:
        if log is not None:
            log.info(f"Failed to delete startup task | task={task_name} | detail={exc}")
        return False, str(exc)


def create_task(task_name: str, spec: StartupLaunchSpec, log) -> tuple[bool, str]:
    try:
        completed = hidden_run(
            [
                "schtasks",
                "/create",
                "/tn",
                task_name,
                "/tr",
                spec.run_value,
                "/sc",
                "ONLOGON",
                "/f",
            ],
            timeout=10,
        )
        if completed.returncode == 0:
            return True, ""
        detail = format_process_error(completed, "Task creation failed")
        log.info(
            f"Failed to create startup task | task={task_name} | command={spec.run_value} | "
            f"detail={detail}"
        )
        return False, detail
    except Exception as exc:
        log.info(f"Failed to create startup task | task={task_name} | command={spec.run_value} | detail={exc}")
        return False, str(exc)
