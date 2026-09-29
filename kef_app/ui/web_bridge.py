from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from ..config import AppConfig
from ..devices.speaker_models import INPUT_SOURCE_OPTIONS, normalize_input_source, normalize_mac
from ..platform.windows.startup.elevation import remove_startup_task_with_uac, repair_task_startup_with_uac
from ..platform.windows.startup.service import ensure_startup_registration
from ..platform.windows.startup.status import read_startup_registration_snapshot
from ..storage import UserConfigStore
from .background_tasks import start_background_task
from .controller_events import ControllerEventBridge
from .logs import UILogHandler
from .logs.log_history import (
    list_log_history_files,
    merge_recent_lines,
    read_log_tail_lines,
    resolve_log_history_file,
)
from .settings.settings_service import get_speaker_power_disabled_reason, startup_mode_for_ui, sync_startup_registration
from .web_state import WebStateMixin, _wake_is_confirmed

__all__ = ["WebControllerBridge", "_wake_is_confirmed"]

_EVENTS: dict[str, tuple[str, str, Callable[[Any], object]]] = {
    "startup": (
        "Startup", "wake_on_startup",
        lambda controller: controller.on_startup(),
    ),
    "display-off": (
        "Display Off", "standby_on_display_off",
        lambda controller: controller.on_display_off(controller.mono(), "WEB_TEST_DISPLAY_OFF"),
    ),
    "display-on": (
        "Display On", "wake_on_display_on",
        lambda controller: controller.on_display_on(controller.mono(), "WEB_TEST_DISPLAY_ON"),
    ),
    "lock": (
        "Lock", "standby_on_lock",
        lambda controller: controller.on_lock("WEB_TEST_LOCK"),
    ),
    "unlock": (
        "Unlock", "wake_on_unlock_only",
        lambda controller: controller.on_unlock("WEB_TEST_UNLOCK"),
    ),
    "sleep": (
        "Sleep", "standby_on_sleep",
        lambda controller: controller.on_suspend("WEB_TEST_SUSPEND"),
    ),
    "lid-close": (
        "Lid Close", "standby_on_lid_close",
        lambda controller: controller.on_lid_closed("WEB_TEST_LID_CLOSE"),
    ),
    "shutdown": (
        "Shutdown", "endsession_standby_on_shutdown",
        lambda controller: controller.standby_kef_end_session("WEB_TEST_ENDSESSION", "WEB_TEST"),
    ),
}


class WebControllerBridge(WebStateMixin, QObject):
    """A deliberately small, JSON-only boundary between the web UI and Python."""

    state_changed = Signal(str)
    settings_changed = Signal(str)
    toast = Signal(str)
    log_line = Signal(str)
    _api_requested = Signal(str, object, object)
    _polled_state = Signal(object, object, object, object)
    _poll_failed = Signal(str)
    _volume_completed = Signal(object)
    _input_completed = Signal(object)
    _target_completed = Signal(object)
    _startup_completed = Signal(object)
    _startup_failed = Signal(str)
    _startup_snapshot_ready = Signal(object)
    _event_completed = Signal(object)
    _action_refresh_requested = Signal()
    _action_failed = Signal(object)

    def __init__(
        self,
        config: AppConfig,
        controller,
        config_store: UserConfigStore,
        controller_bridge: ControllerEventBridge,
        log_handler: UILogHandler,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._controller = controller
        self._config_store = config_store
        self._controller_bridge = controller_bridge
        self._log_handler = log_handler
        self._speaker_on: bool | None = None
        self._input = normalize_input_source(config.kef_input)
        self._volume: int | None = None
        self._startup_registered = False
        configured_startup_mode = startup_mode_for_ui(config.startup_registration_mode)
        self._startup_mode = "none" if configured_startup_mode == "off" else configured_startup_mode
        self._startup_busy = False
        self._startup_snapshot_pending = True
        self._startup_requested_mode: str | None = None
        self._startup_requested_enabled: bool | None = None
        self._poll_lock = threading.Lock()
        self._volume_lock = threading.Lock()
        self._volume_pending: tuple[int, int] | None = None
        self._volume_worker_running = False
        self._volume_sequence = 0
        self._target_sequence = 0
        self._scan_id = ""
        self._scan_cancel = threading.Event()
        self._last_action_started = 0.0
        self._last_action: dict[str, object] | None = None
        self._last_failure: tuple[str, float] | None = None
        self._last_action_failure: tuple[str, float] | None = None
        self._target_signature = self._controller.get_current_kef_target()
        self._last_poll_success_mono = 0.0
        self._awaiting_wake_confirmation = False
        self._ui_visible = False

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_speaker_state)
        self._polled_state.connect(self._on_polled_state)
        self._poll_failed.connect(self._on_poll_failed)
        self._volume_completed.connect(self._on_volume_completed)
        self._input_completed.connect(self._on_input_completed)
        self._target_completed.connect(self._on_target_completed)
        self._startup_completed.connect(self._on_startup_completed)
        self._startup_failed.connect(self._on_startup_failed)
        self._startup_snapshot_ready.connect(self._on_startup_snapshot_ready)
        self._event_completed.connect(self._on_event_completed)
        self._action_refresh_requested.connect(lambda: self._poll_speaker_state(force=True))
        self._action_failed.connect(self._on_action_failed)
        self._api_requested.connect(self._handle_api_request)
        self._apply_poll_interval()

        controller_bridge.identity_changed.connect(self._on_identity_changed)
        controller_bridge.speaker_state_changed.connect(self._on_speaker_state_changed)
        controller_bridge.power_action_started.connect(self._on_power_action_started)
        controller_bridge.power_action_finished.connect(self._on_power_action_finished)
        log_handler.emitter.new_line.connect(self.log_line.emit)

        start_background_task(
            "WebReadStartupState",
            self._read_reconciled_startup_snapshot,
            on_success=self._startup_snapshot_ready.emit,
            on_error=lambda exc: self._startup_failed.emit(str(exc)),
            log=controller.log,
        )

    def _read_reconciled_startup_snapshot(self):
        ensure_startup_registration(
            self._config.app_name,
            self._controller.log,
            mode=self._config.startup_registration_mode,
        )
        return read_startup_registration_snapshot(self._config.app_name, log=self._controller.log)

    def _initial_state_payload(self) -> dict[str, Any]:
        self._poll_speaker_state()
        return self._state()

    def refresh(self) -> None:
        self._poll_speaker_state(force=True)
        self.publish_state()

    def set_ui_visible(self, visible: bool) -> None:
        """Only poll the speaker while the controller window is visible."""
        visible = bool(visible)
        if visible == self._ui_visible:
            return
        self._ui_visible = visible
        if visible:
            self._poll_timer.start()
            self._poll_speaker_state(force=True)
            return
        self._poll_timer.stop()

    def togglePower(self) -> None:
        identity = self._controller.get_current_identity()
        is_on = self._speaker_on if self._speaker_on is not None else bool(identity.available)
        action = "standby" if is_on else "wake"
        def power_work() -> bool:
            return bool(self._controller.run_user_power_action(action, "web_ui"))
        self._start_action(
            f"Web{action.title()}",
            power_work,
            f"{action.title()} requested",
            action=action,
        )

    def setVolume(self, level: int) -> None:
        level = max(0, min(100, int(level)))
        with self._volume_lock:
            self._volume_sequence += 1
            self._volume_pending = (self._volume_sequence, level)
            if self._volume_worker_running:
                return
            self._volume_worker_running = True

        def work() -> None:
            while True:
                with self._volume_lock:
                    pending = self._volume_pending
                    self._volume_pending = None
                    if pending is None:
                        self._volume_worker_running = False
                        return
                sequence, requested_level = pending
                try:
                    ok = bool(self._controller.set_volume(requested_level))
                    error = ""
                except Exception as exc:
                    ok = False
                    error = str(exc)
                self._volume_completed.emit((sequence, requested_level, ok, error))

        start_background_task("WebSetVolume", work, log=self._controller.log)

    @Slot(object)
    def _on_volume_completed(self, payload: object) -> None:
        sequence, level, ok, error = payload
        if int(sequence) != self._volume_sequence:
            return
        if bool(ok):
            self._volume = int(level)
        else:
            self._notify(
                "error",
                "Volume update failed",
                str(error) or "The speaker did not accept the volume change.",
                code="volume_update_failed",
                params={"error": f" ({error})" if error else ""},
                channel="volume",
            )
        self.publish_state()

    def changeInput(self, source: str) -> None:
        source = normalize_input_source(source)
        valid = {value for _label, value in INPUT_SOURCE_OPTIONS}
        if source not in valid:
            self._notify(
                "error",
                "Invalid input",
                "That input source is not supported.",
                code="invalid_input",
                channel="input",
            )
            return

        def work() -> bool:
            return bool(self._controller.change_input_live(source))

        start_background_task(
            "WebChangeInput",
            work,
            on_success=lambda ok: self._input_completed.emit((source, bool(ok), "")),
            on_error=lambda exc: self._input_completed.emit((source, False, str(exc))),
            log=self._controller.log,
        )

    @Slot(object)
    def _on_input_completed(self, payload: object) -> None:
        source, ok, error = payload
        if bool(ok):
            self._input = str(source)
            self._config.kef_input = str(source)
            saved = self._config_store.save(self._config)
            self._notify(
                "success" if saved else "warning",
                "Input changed",
                f"Switched to {source}." if saved else f"Switched to {source}, but the default could not be saved.",
                code="input_changed" if saved else "input_changed_unsaved",
                params={"source": str(source)},
                channel="input",
            )
            self.publish_settings()
        else:
            self._notify(
                "error",
                "Input change failed",
                str(error) or "The speaker did not accept the input change.",
                code="input_change_failed",
                params={"error": f" ({error})" if error else ""},
                channel="input",
            )
        self.publish_state()

    def updateSettings(self, payload: str) -> None:
        try:
            changes = json.loads(payload)
            if not isinstance(changes, dict):
                raise ValueError("Settings payload must be an object")
            updated = self._config.clone()
            for key, value in changes.items():
                if key not in self._config_store.USER_EDITABLE_FIELDS:
                    continue
                coerce = self._config_store.FIELD_COERCERS[key]
                setattr(updated, key, coerce(value))
        except (ValueError, TypeError, KeyError) as exc:
            self._notify(
                "error",
                "Settings were not saved",
                str(exc),
                code="settings_invalid",
                params={"detail": str(exc)},
                channel="save",
            )
            return

        self._copy_user_editable_fields(updated)
        saved = self._config_store.save(self._config)
        self._apply_poll_interval()
        self._notify(
            "success" if saved else "warning",
            "Settings saved" if saved else "Settings updated for this session",
            "Changes are now active." if saved else "config.json could not be written.",
            code="settings_saved" if saved else "settings_session_only",
            channel="save",
        )
        self.publish_settings()
        self.publish_state()

    def applyTarget(self, ip: str, mac: str) -> None:
        """Validate and apply a device target exactly as the former Qt dialog did."""
        requested_ip = str(ip or "").strip()
        requested_mac = str(mac or "").strip()
        self._target_sequence += 1
        sequence = self._target_sequence

        def work():
            return self._controller.validate_manual_target(
                requested_ip, requested_mac, reason="web_manual_target", trigger="web_settings"
            )

        start_background_task(
            "WebApplyTarget",
            work,
            on_success=lambda result: self._target_completed.emit(
                (sequence, requested_ip, requested_mac, result, "")
            ),
            on_error=lambda exc: self._target_completed.emit(
                (sequence, requested_ip, requested_mac, None, str(exc))
            ),
            log=self._controller.log,
        )

    @Slot(object)
    def _on_target_completed(self, payload: object) -> None:
        sequence, requested_ip, requested_mac, result, error = payload
        if int(sequence) != self._target_sequence:
            return
        if error:
            self._notify(
                "error",
                "Target not saved",
                str(error),
                code="target_not_saved",
                params={"detail": str(error)},
                channel="target",
            )
            return
        status = str(getattr(result, "status", "failed"))
        identity = getattr(result, "identity", None)
        if status in {"invalid_ip", "invalid_mac", "empty", "not_kef", "mac_mismatch", "failed"}:
            detail = self._target_error_message(status, identity)
            self._notify(
                "error",
                "Target not saved",
                detail,
                code=f"target_{status}",
                params={
                    "detail": detail,
                    "mac": str(getattr(identity, "mac_display", "") or getattr(identity, "mac", "") or "not reported"),
                },
                channel="target",
            )
            return
        saved_ip = str(getattr(identity, "ip", "") or requested_ip)
        saved_mac = normalize_mac(str(getattr(identity, "mac", "") or requested_mac))
        self._config.kef_ip = saved_ip
        self._config.kef_mac = saved_mac
        self._controller.apply_configured_device_target(
            trigger="web_settings", info=identity if status in {"verified", "recovered"} else None,
        )
        saved = self._config_store.save(self._config)
        warning = status in {"mac_unverified", "mac_not_found", "unreachable"}
        self._notify(
            "warning" if warning or not saved else "success",
            "Target saved" if saved else "Target updated for this session",
            self._target_saved_message(status, saved_ip),
            code=f"target_{status}" if saved else "target_session_only",
            params={"ip": saved_ip, "status": status},
            channel="target",
        )
        self.publish_settings()
        self.publish_state()
        self._poll_speaker_state(force=True)

    def updateStartup(self, mode: str, enabled: bool) -> None:
        """Persist the startup preference and reconcile Windows registration."""
        try:
            normalized_mode = self._config_store.FIELD_COERCERS["startup_registration_mode"](mode)
        except (TypeError, ValueError) as exc:
            self._notify(
                "error",
                "Startup setting was not saved",
                str(exc),
                code="startup_invalid",
                params={"detail": str(exc)},
                channel="save",
            )
            return
        if self._startup_busy:
            self._notify(
                "info",
                "Startup update in progress",
                "Wait for the current Windows startup update to finish.",
                code="startup_busy",
                channel="save",
            )
            return

        desired_startup = bool(enabled)
        startup_mode_changed = normalized_mode != self._config.startup_registration_mode
        self._startup_busy = True
        self._startup_snapshot_pending = False
        self._startup_requested_mode = normalized_mode
        self._startup_requested_enabled = desired_startup
        self.publish_state()

        def work():
            return sync_startup_registration(
                normalized_mode,
                desired_startup=desired_startup,
                startup_mode_changed=startup_mode_changed,
                log=self._controller.log,
                retry_enable_task_with_uac=lambda: repair_task_startup_with_uac(
                    "KEF Controller", self._controller.log
                )[0],
                retry_enable_registry_with_uac=lambda: remove_startup_task_with_uac(
                    "KEF Controller", self._controller.log
                )[0],
                retry_disable_with_uac=lambda: remove_startup_task_with_uac(
                    "KEF Controller", self._controller.log
                )[0],
            )

        start_background_task(
            "WebUpdateStartup",
            work,
            on_success=self._startup_completed.emit,
            on_error=lambda exc: self._startup_failed.emit(str(exc)),
            log=self._controller.log,
        )

    @Slot(object)
    def _on_startup_completed(self, result: object) -> None:
        self._config.startup_registration_mode = str(result.configured_mode)
        config_ok = self._config_store.save(self._config)
        self._startup_registered = bool(result.actual_startup_registered)
        self._startup_mode = str(result.actual_startup_mode)
        self._finish_startup_update()
        if config_ok and result.startup_ok:
            self._notify(
                "success",
                "Startup settings saved",
                "Windows startup registration is up to date.",
                code="startup_saved",
                channel="save",
            )
        else:
            self._notify(
                "warning",
                "Startup settings need attention",
                result.startup_detail or "The setting could not be fully applied.",
                code="startup_attention",
                params={"detail": result.startup_detail or "The setting could not be fully applied."},
                channel="save",
            )
        self.publish_settings()
        self.publish_state()

    @Slot(str)
    def _on_startup_failed(self, detail: str) -> None:
        if self._startup_snapshot_pending:
            self._startup_snapshot_pending = False
            self.publish_state()
            return
        if self._startup_busy:
            self._finish_startup_update()
            self._notify(
                "error",
                "Startup setting was not saved",
                detail,
                code="startup_failed",
                params={"detail": detail},
                channel="save",
            )
            self.publish_state()

    @Slot(object)
    def _on_startup_snapshot_ready(self, snapshot: object) -> None:
        if not self._startup_snapshot_pending:
            return
        self._startup_snapshot_pending = False
        self._startup_registered = bool(snapshot.registered)
        self._startup_mode = str(snapshot.effective_mode)
        self.publish_state()

    def _finish_startup_update(self) -> None:
        self._startup_busy = False
        self._startup_requested_mode = None
        self._startup_requested_enabled = None

    def cancelScan(self, scan_id: str) -> None:
        if scan_id == self._scan_id:
            self._scan_cancel.set()

    def scanSpeakers(self, scan_id: str) -> None:
        self._scan_cancel.set()
        self._scan_id = scan_id
        cancelled = threading.Event()
        self._scan_cancel = cancelled

        def publish(**payload: object) -> None:
            if not cancelled.is_set():
                self.toast.emit(self._encode({"kind": "scan", "scan_id": scan_id, **payload}))

        def candidate(identity: object) -> None:
            publish(state="candidate", devices=[self._identity_dict(identity)])

        last_progress_emit = [0.0]

        def progress(checked: int) -> None:
            # Probe completions arrive per host; keep the UI feed lightweight.
            now = time.monotonic()
            if now - last_progress_emit[0] < 0.15:
                return
            last_progress_emit[0] = now
            publish(state="progress", checked=checked)

        busy = threading.Event()

        def done(devices: list[object]) -> None:
            if busy.is_set():
                # The lock wait timed out behind a background recovery sweep;
                # an empty list here does not mean the network has no speaker.
                publish(state="failed", code="scan_busy", detail="A background speaker search is still running.")
                return
            serialized = [self._identity_dict(device) for device in devices]
            publish(state="complete", devices=serialized)

        def failed(exc: Exception) -> None:
            publish(state="failed", detail=str(exc))

        start_background_task(
            "WebScanSpeakers",
            lambda: self._controller.scan_kef_devices(
                on_candidate=candidate, on_progress=progress, should_continue=lambda: not cancelled.is_set(),
                on_busy=busy.set,
            ),
            on_success=done,
            on_error=failed,
            log=self._controller.log,
        )

    def runEvent(self, event_key: str) -> None:
        event = _EVENTS.get(event_key)
        if event is None:
            self._notify(
                "error",
                "Unknown test",
                event_key,
                code="unknown_test",
                params={"event": event_key},
                channel="event",
            )
            return
        label, setting_key, runner = event
        if not bool(getattr(self._config, setting_key)):
            disabled_detail = get_speaker_power_disabled_reason(setting_key)
            self._notify(
                "warning",
                f"{label}: no action",
                disabled_detail,
                code="event_disabled",
                params={"event": event_key},
                channel="event",
            )
            self._emit_event_result(
                event_key,
                "warning",
                "No action",
                disabled_detail,
                code="event_no_action",
            )
            return

        started = time.perf_counter()
        self._emit_event_result(
            event_key,
            "running",
            "Running",
            "Waiting for the controller to respond…",
            code="event_running",
        )

        def work() -> tuple[bool, str, int, bool | None]:
            scheduled = runner(self._controller)
            time.sleep(0.15)
            _source, _volume, speaker_on = self._controller.poll_external_ui_state(
                f"web_test_{event_key}", "web_event_test"
            )
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            ok = scheduled is not False
            if event_key == "startup":
                detail = "Action completed" if ok else "No action was completed"
            else:
                detail = "Action scheduled" if ok else "No action was scheduled"
            return ok, detail, elapsed_ms, speaker_on

        start_background_task(
            f"WebTest{label.replace(' ', '')}",
            work,
            on_success=lambda result: self._event_completed.emit((event_key, result, "")),
            on_error=lambda exc: self._event_completed.emit((event_key, None, str(exc))),
            log=self._controller.log,
        )

    @Slot(object)
    def _on_event_completed(self, payload: object) -> None:
        event_key, result, error = payload
        if error:
            self._emit_event_result(
                str(event_key),
                "error",
                "Failed",
                str(error),
                code="event_failed",
                params={"detail": str(error)},
            )
            return
        ok, detail, elapsed_ms, speaker_on = result
        if speaker_on is not None:
            self._speaker_on = speaker_on
        state = "success" if ok else "warning"
        status = "Completed" if ok else "No action"
        completed_code = (
            "event_completed"
            if str(event_key) == "startup" and ok
            else "event_not_completed"
            if str(event_key) == "startup"
            else "event_scheduled"
            if ok
            else "event_not_scheduled"
        )
        self._emit_event_result(
            str(event_key),
            state,
            status,
            f"{detail} · {elapsed_ms} ms · Speaker: {self._speaker_label()}",
            code=completed_code,
            params={"elapsed": elapsed_ms, "speaker": self._speaker_label()},
        )
        self.publish_state()

    def _logs_payload(self, selected_name: str = "") -> list[str]:
        # Poll diagnostics are emitted at DEBUG, so normal INFO logs stay clean
        # at the source rather than relying on a brittle presentation filter.
        log_path = resolve_log_history_file(self._config.log_file, selected_name)
        file_lines = read_log_tail_lines(log_path, max_lines=8000)
        if log_path != self._config.log_file:
            return file_lines[-800:]
        return merge_recent_lines(
            file_lines,
            self._log_handler.snapshot_lines(),
            max_lines=800,
        )

    def _log_files_payload(self) -> list[str]:
        return list_log_history_files(self._config.log_file)

    def openLogFolder(self) -> None:
        os.makedirs(self._config.log_dir, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(self._config.log_dir))

    def invoke_api(self, method: str, args: list[object]) -> object:
        """Run a local HTTP request on this QObject's Qt thread and wait for it."""
        reply: dict[str, object] = {"event": threading.Event()}
        self._api_requested.emit(method, args, reply)
        if not reply["event"].wait(10):
            raise TimeoutError(f"Timed out handling web API request: {method}")
        if "error" in reply:
            raise RuntimeError(str(reply["error"]))
        return reply.get("result", {"ok": True})

    @Slot(str, object, object)
    def _handle_api_request(self, method: str, args: object, reply: object) -> None:
        response = reply if isinstance(reply, dict) else {}
        values = list(args) if isinstance(args, list) else []
        try:
            handlers: dict[str, Callable[[], object]] = {
                "initialState": self._initial_state_payload,
                "logs": lambda: self._logs_payload(str(values[0]) if values else ""),
                "logFiles": self._log_files_payload,
                "refresh": lambda: self._api_action(self.refresh),
                "togglePower": lambda: self._api_action(self.togglePower),
                "setVolume": lambda: self._api_action(lambda: self.setVolume(int(values[0]))),
                "changeInput": lambda: self._api_action(lambda: self.changeInput(str(values[0]))),
                "updateSettings": lambda: self._api_action(lambda: self.updateSettings(str(values[0]))),
                "applyTarget": lambda: self._api_action(lambda: self.applyTarget(str(values[0]), str(values[1]))),
                "updateStartup": lambda: self._api_action(lambda: self.updateStartup(str(values[0]), bool(values[1]))),
                "runEvent": lambda: self._api_action(lambda: self.runEvent(str(values[0]))),
                "scanSpeakers": lambda: self._api_action(lambda: self.scanSpeakers(str(values[0]))),
                "cancelScan": lambda: self._api_action(lambda: self.cancelScan(str(values[0]))),
                "openLogFolder": lambda: self._api_action(self.openLogFolder),
            }
            try:
                response["result"] = handlers[method]()
            except KeyError as exc:
                raise ValueError(f"Unknown web API method: {method}") from exc
        except Exception as exc:
            response["error"] = str(exc)
        finally:
            event = response.get("event")
            if isinstance(event, threading.Event):
                event.set()

    @staticmethod
    def _api_action(callback: Callable[[], None]) -> dict[str, bool]:
        callback()
        return {"ok": True}

    def _start_action(self, name: str, work: Callable[[], object], message: str, *, action: str) -> None:
        normalized_action = str(action or "").upper()

        self._notify(
            "info",
            "Speaker action",
            message,
            code="speaker_action_requested",
            params={"action": normalized_action},
            channel="power",
            action=normalized_action,
        )
        start_background_task(
            name,
            work,
            on_success=lambda _result: self._action_refresh_requested.emit(),
            on_error=lambda exc: self._action_failed.emit((normalized_action, str(exc))),
            log=self._controller.log,
        )

    @Slot(object)
    def _on_action_failed(self, payload: object) -> None:
        action, detail = payload
        self._notify(
            "error",
            "Speaker action failed",
            str(detail),
            code="speaker_action_failed",
            params={"action": str(action), "detail": str(detail)},
            channel="power",
            action=str(action),
            phase="finished",
            success=False,
        )

    def _emit_event_result(
        self,
        key: str,
        state: str,
        title: str,
        detail: str,
        *,
        code: str = "",
        params: dict[str, object] | None = None,
    ) -> None:
        payload: dict[str, object] = {
            "kind": "event",
            "key": key,
            "state": state,
            "title": title,
            "detail": detail,
        }
        if code:
            payload["code"] = code
        if params:
            payload["params"] = params
        self.toast.emit(self._encode(payload))

    def _notify(
        self,
        level: str,
        title: str,
        detail: str,
        *,
        action: str = "",
        phase: str = "",
        success: bool | None = None,
        code: str = "",
        params: dict[str, object] | None = None,
        channel: str = "",
    ) -> None:
        payload: dict[str, object] = {"kind": "toast", "level": level, "title": title, "detail": detail}
        if code:
            payload["code"] = code
        if params:
            payload["params"] = params
        if channel:
            payload["channel"] = channel
        if action:
            payload["action"] = action
        if phase:
            payload["phase"] = phase
        if success is not None:
            payload["success"] = success
        self.toast.emit(self._encode(payload))

    def _speaker_label(self) -> str:
        if self._speaker_on is True:
            return "On"
        if self._speaker_on is False:
            return "Standby"
        return "Unknown"

    @staticmethod
    def _identity_dict(identity: object) -> dict[str, str]:
        return {
            "name": str(getattr(identity, "speaker_name", "")),
            "model": str(getattr(identity, "speaker_model", "")),
            "ip": str(getattr(identity, "ip", "")),
            "mac": str(getattr(identity, "mac_display", "") or getattr(identity, "mac", "")),
        }

    @staticmethod
    def _target_error_message(status: str, identity: object) -> str:
        if status == "mac_mismatch":
            return f"That IP belongs to a speaker with MAC {getattr(identity, 'mac_display', '') or getattr(identity, 'mac', '') or 'not reported'}."
        return {
            "invalid_ip": "Enter a routable IPv4 address.",
            "invalid_mac": "Enter a 12-digit MAC address.",
            "empty": "Enter an IP address or MAC address.",
            "not_kef": "That IP responded, but is not a supported KEF speaker.",
        }.get(status, "The target details could not be validated.")

    @staticmethod
    def _target_saved_message(status: str, ip: str) -> str:
        if status == "verified":
            return f"Verified {ip} and saved the target."
        if status == "recovered":
            return f"Recovered {ip} from the MAC address and saved it."
        if status == "mac_unverified":
            return "Saved the target, but the speaker did not report its MAC during verification."
        if status == "mac_not_found":
            return "Saved the MAC target. The app will recover its IP when the speaker appears."
        return "Saved the target as a connection hint; it is not reachable right now."

    @staticmethod
    def _encode(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, default=str)
