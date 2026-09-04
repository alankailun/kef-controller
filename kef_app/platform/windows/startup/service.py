from __future__ import annotations

from ....structured_logging import log_structured
from .common import (
    NullLogger,
    clear_last_startup_error,
    get_last_startup_error,
    is_frozen_runtime,
    log_startup_failure,
    normalize_startup_mode,
    set_last_startup_error,
)
from .launch import ensure_preferred_executable, runtime_launch_spec
from .reconcile import StartupRegistrationState, read_startup_registration_state
from .registry import RegistryStartupEntry, delete_registry_commands, write_registry_command
from .status import get_effective_startup_registration_mode
from .task_scheduler import ScheduledTaskEntry, create_task, delete_task


def _delete_registry_entries(entries: tuple[RegistryStartupEntry, ...], *extra_names: str) -> None:
    names = {entry.name for entry in entries}
    names.update(name for name in extra_names if name)
    if names:
        delete_registry_commands(tuple(sorted(names)))


def _delete_tasks(entries: tuple[ScheduledTaskEntry, ...], logger, *, action: str, extra_names: tuple[str, ...] = ()) -> bool:
    names = {entry.name for entry in entries}
    names.update(name for name in extra_names if name)
    ok = True
    details: list[str] = []
    for name in sorted(names):
        deleted, detail = delete_task(name, logger)
        if not deleted:
            ok = False
            details.append(f"{name}: {detail}")
    if not ok:
        set_last_startup_error("; ".join(details))
        log_startup_failure(logger, action, ", ".join(sorted(names)), get_last_startup_error())
    return ok


def _cleanup_for_task_mode(state: StartupRegistrationState, logger) -> bool:
    _delete_registry_entries(state.registry_entries, state.task_name)
    extra_stale_tasks = tuple(
        entry
        for entry in state.stale_task_entries
        if entry.name.strip().lstrip("\\").lower() != state.task_name.lower()
    )
    return _delete_tasks(extra_stale_tasks, logger, action="cleanup_task_mode")


def _cleanup_for_registry_mode(state: StartupRegistrationState, logger) -> bool:
    # Keep the existing task until it is known to be removable.  If Windows
    # rejects the deletion (or the user cancels UAC), the prior Task Scheduler
    # registration remains the sole effective startup method.
    if not _delete_tasks(state.task_entries, logger, action="cleanup_registry_mode", extra_names=(state.task_name,)):
        return False
    extra_registry_entries = tuple(
        entry for entry in state.stale_registry_entries if entry.name.lower() != state.task_name.lower()
    )
    _delete_registry_entries(extra_registry_entries)
    return True


def _needs_reconcile(mode: str, state: StartupRegistrationState) -> bool:
    if mode == "off":
        return bool(state.has_registry or state.task_present or state.registry_entries or state.task_entries)
    if mode == "registry":
        return (
            not state.registry_is_current
            or state.task_present
            or state.has_stale_registry
            or state.has_stale_task
        )
    if mode == "task":
        return (
            not state.task_is_current
            or state.has_registry
            or bool(state.registry_entries)
            or state.has_stale_task
        )
    return (
        not state.task_is_current
        or state.has_registry
        or bool(state.registry_entries)
        or state.has_stale_task
    )


def set_startup_registered(
    enable: bool,
    task_name: str,
    launch_spec=None,
    log=None,
    mode: str = "task",
    *,
    preloaded_state: StartupRegistrationState | None = None,
) -> bool:
    logger = log or NullLogger()
    normalized_mode = normalize_startup_mode(mode)
    clear_last_startup_error()

    spec = launch_spec or (ensure_preferred_executable(task_name, logger) if is_frozen_runtime() else runtime_launch_spec())
    # UI changes only need the canonical task plus matching registry entries.
    # Enumerating every scheduled task can take seconds on some machines and is
    # reserved for the startup self-heal/migration path below.
    state = preloaded_state or read_startup_registration_state(task_name, spec, include_related_tasks=False)

    if not enable or normalized_mode == "off":
        # Task deletion can require elevation.  Do it first so a cancelled UAC
        # request leaves every existing startup entry untouched and the UI can
        # faithfully return to the currently active method.
        if not _delete_tasks(state.task_entries, logger, action="disable", extra_names=(task_name,)):
            return False
        _delete_registry_entries(state.registry_entries, task_name)
        return True

    if normalized_mode == "registry":
        if state.registry_is_current:
            return _cleanup_for_registry_mode(state, logger)
        registry_ok, registry_detail = write_registry_command(task_name, spec.run_value)
        if not registry_ok:
            set_last_startup_error(f"Could not write the registry startup entry: {registry_detail}")
            log_startup_failure(logger, "enable_registry", task_name, get_last_startup_error())
            return False
        cleanup_ok = _cleanup_for_registry_mode(state, logger)
        if cleanup_ok:
            return True

        # The Registry entry was just written, but the older scheduled task
        # could not be removed.  Restore the prior Registry value (or remove
        # the new one) so cancelling elevation does not silently switch modes.
        if state.registry_command:
            write_registry_command(task_name, state.registry_command)
        else:
            delete_registry_commands((task_name,))
        return False

    if state.task_is_current:
        cleanup_ok = _cleanup_for_task_mode(state, logger)
        if cleanup_ok:
            clear_last_startup_error()
        return cleanup_ok

    task_ok, task_detail = create_task(task_name, spec, logger)
    if task_ok:
        cleanup_ok = _cleanup_for_task_mode(state, logger)
        if cleanup_ok:
            clear_last_startup_error()
        return cleanup_ok

    set_last_startup_error(f"Could not create the Task Scheduler entry: {task_detail}")
    log_startup_failure(logger, "enable_task", task_name, get_last_startup_error())
    return False


def ensure_startup_registration(task_name: str, log, mode: str = "task") -> bool:
    if not is_frozen_runtime():
        return False

    normalized_mode = normalize_startup_mode(mode)
    desired = ensure_preferred_executable(task_name, log)
    # The common healthy path only needs two targeted queries. A full verbose
    # Task Scheduler enumeration is reserved for a state that actually needs
    # repair, keeping it off the pre-tray startup path.
    state = read_startup_registration_state(task_name, desired, include_related_tasks=False)
    has_complete_state = False
    if not state.has_registry and not state.task_present and not state.registry_entries and not state.task_entries:
        # The canonical task and Registry value may both be absent while an
        # older, differently named task still launches this application.
        # This full scan runs on the Web bridge's startup worker in the GUI.
        state = read_startup_registration_state(task_name, desired, include_related_tasks=True)
        has_complete_state = True
        if not state.has_registry and not state.task_present and not state.registry_entries and not state.task_entries:
            return False

    if not _needs_reconcile(normalized_mode, state):
        return False

    if not has_complete_state:
        state = read_startup_registration_state(task_name, desired, include_related_tasks=True)

    ok = set_startup_registered(
        normalized_mode != "off",
        task_name=task_name,
        launch_spec=desired,
        log=log,
        mode=normalized_mode,
        preloaded_state=state,
    )
    if ok:
        actual_mode = get_effective_startup_registration_mode(task_name, log=log)
        trigger = "disabled" if actual_mode == "none" else ("At log on" if actual_mode == "task" else "registry_run")
        log_structured(
            log,
            "STEP",
            action="STARTUP_REGISTRATION",
            reason="startup_reconcile",
            trigger=trigger,
            step="self_heal",
            status="completed",
            task=task_name,
            requested_mode=normalized_mode,
            actual_mode=actual_mode,
            command=desired.run_value,
        )
    return ok
