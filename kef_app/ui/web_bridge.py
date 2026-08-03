from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict
from typing import Any, Callable

from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl

from ..config import AppConfig
from ..devices.speaker_models import INPUT_SOURCE_OPTIONS, normalize_input_source, normalize_mac
from ..storage import UserConfigStore
from ..platform.windows import (
    remove_startup_task_with_uac,
    read_startup_registration_snapshot,
    repair_task_startup_with_uac,
)
from .background_tasks import start_background_task
from .controller_events import ControllerEventBridge
from .logs import UILogHandler
from .logs.log_history import (
    list_log_history_files,
    merge_recent_lines,
    read_log_tail_lines,
    resolve_log_history_file,
)
from .settings.settings_service import get_speaker_power_disabled_reason, save_settings_and_sync_startup


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


def _wake_is_confirmed(speaker_on: object, input_source: str | None) -> bool:
    """Only enable live controls after the speaker reports a usable source."""
    return speaker_on is True and bool(input_source) and input_source != "standby"


class WebControllerBridge(QObject):
    """A deliberately small, JSON-only boundary between the web UI and Python."""

    state_changed = Signal(str)
    toast = Signal(str)
    log_line = Signal(str)
    _api_requested = Signal(str, object, object)
    _polled_state = Signal(object, object, object)
    _poll_failed = Signal(str)

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
        startup_snapshot = read_startup_registration_snapshot("KEF Controller", log=controller.log)
        self._startup_registered = startup_snapshot.registered
        self._startup_mode = startup_snapshot.effective_mode
        self._startup_busy = False
        self._startup_requested_mode: str | None = None
        self._startup_requested_enabled: bool | None = None
        self._poll_lock = threading.Lock()
        self._last_action_started = 0.0
        self._last_action: dict[str, object] | None = None
        self._last_failure: tuple[str, float] | None = None
        self._last_poll_success_mono = 0.0
        self._awaiting_wake_confirmation = False
        self._ui_visible = False

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_speaker_state)
        self._polled_state.connect(self._on_polled_state)
        self._poll_failed.connect(self._on_poll_failed)
        self._api_requested.connect(self._handle_api_request)
        self._apply_poll_interval()

        controller_bridge.identity_changed.connect(lambda _identity: self.publish_state())
        controller_bridge.speaker_state_changed.connect(self._on_speaker_state_changed)
        controller_bridge.power_action_started.connect(self._on_power_action_started)
        controller_bridge.power_action_finished.connect(self._on_power_action_finished)
        log_handler.emitter.new_line.connect(self._on_log_line)

    @Slot(result=str)
    def initialState(self) -> str:
        return self._encode(self._initial_state_payload())

    def _initial_state_payload(self) -> dict[str, Any]:
        self._poll_speaker_state()
        return self._state()

    @Slot()
    def refresh(self) -> None:
        self._poll_speaker_state(force=True)
        self.publish_state()

    @Slot(bool)
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

    @Slot()
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

    @Slot(int)
    def setVolume(self, level: int) -> None:
        level = max(0, min(100, int(level)))

        def work() -> bool:
            return bool(self._controller.set_volume(level))

        def done(ok: bool) -> None:
            if ok:
                self._volume = level
            else:
                self._notify("error", "Volume update failed", "The speaker did not accept the volume change.")
            self.publish_state()

        start_background_task("WebSetVolume", work, on_success=done,
                              on_error=lambda _exc: done(False), log=self._controller.log)

    @Slot(str)
    def changeInput(self, source: str) -> None:
        source = normalize_input_source(source)
        valid = {value for _label, value in INPUT_SOURCE_OPTIONS}
        if source not in valid:
            self._notify("error", "Invalid input", "That input source is not supported.")
            return

        def work() -> bool:
            return bool(self._controller.change_input_live(source))

        def done(ok: bool) -> None:
            if ok:
                self._input = source
                self._config.kef_input = source
                saved = self._config_store.save(self._config)
                self._notify(
                    "success" if saved else "warning",
                    "Input changed",
                    f"Switched to {source}." if saved else f"Switched to {source}, but the default could not be saved.",
                )
            else:
                self._notify("error", "Input change failed", "The speaker did not accept the input change.")
            self.publish_state()

        start_background_task("WebChangeInput", work, on_success=done,
                              on_error=lambda _exc: done(False), log=self._controller.log)

    @Slot(str)
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
            self._notify("error", "Settings were not saved", str(exc))
            return

        for field_name in self._config_store.USER_EDITABLE_FIELDS:
            setattr(self._config, field_name, getattr(updated, field_name))
        saved = self._config_store.save(self._config)
        self._apply_poll_interval()
        self._notify(
            "success" if saved else "warning",
            "Settings saved" if saved else "Settings updated for this session",
            "Changes are now active." if saved else "config.json could not be written.",
        )
        self.publish_state()

    @Slot(str, str)
    def applyTarget(self, ip: str, mac: str) -> None:
        """Validate and apply a device target exactly as the former Qt dialog did."""
        requested_ip = str(ip or "").strip()
        requested_mac = normalize_mac(str(mac or ""))

        def work():
            return self._controller.validate_manual_target(
                requested_ip, requested_mac, reason="web_manual_target", trigger="web_settings"
            )

        def done(result: object) -> None:
            status = str(getattr(result, "status", "failed"))
            identity = getattr(result, "identity", None)
            if status in {"invalid_ip", "invalid_mac", "empty", "not_kef", "mac_mismatch", "failed"}:
                self._notify("error", "Target not saved", self._target_error_message(status, identity))
                return
            saved_ip = str(getattr(identity, "ip", "") or requested_ip)
            saved_mac = normalize_mac(str(getattr(identity, "mac", "") or requested_mac))
            self._config.kef_ip = saved_ip
            self._config.kef_mac = saved_mac
            self._controller.apply_configured_device_target(trigger="web_settings")
            saved = self._config_store.save(self._config)
            warning = status in {"mac_unverified", "mac_not_found", "unreachable"}
            self._notify(
                "warning" if warning or not saved else "success",
                "Target saved" if saved else "Target updated for this session",
                self._target_saved_message(status, saved_ip),
            )
            self.publish_state()

        start_background_task("WebApplyTarget", work, on_success=done,
                              on_error=lambda exc: self._notify("error", "Target not saved", str(exc)),
                              log=self._controller.log)

    @Slot(str, bool)
    def updateStartup(self, mode: str, enabled: bool) -> None:
        """Persist the startup preference and reconcile Windows registration."""
        try:
            normalized_mode = self._config_store.FIELD_COERCERS["startup_registration_mode"](mode)
            updated = self._config.with_updates(startup_registration_mode=normalized_mode)
        except (TypeError, ValueError) as exc:
            self._notify("error", "Startup setting was not saved", str(exc))
            return
        if self._startup_busy:
            self._notify("info", "Startup update in progress", "Wait for the current Windows startup update to finish.")
            return

        desired_startup = bool(enabled)
        startup_mode_changed = updated.startup_registration_mode != self._config.startup_registration_mode
        self._startup_busy = True
        self._startup_requested_mode = normalized_mode
        self._startup_requested_enabled = desired_startup
        self.publish_state()

        def work():
            return save_settings_and_sync_startup(
                updated,
                config_store=self._config_store,
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

        def done(result) -> None:
            for field_name in self._config_store.USER_EDITABLE_FIELDS:
                setattr(self._config, field_name, getattr(result.updated, field_name))
            self._startup_registered = result.actual_startup_registered
            self._startup_mode = result.actual_startup_mode
            if result.config_ok and result.startup_ok:
                self._notify("success", "Startup settings saved", "Windows startup registration is up to date.")
            else:
                self._notify(
                    "warning",
                    "Startup settings need attention",
                    result.startup_detail or "The setting could not be fully applied.",
                )

        def failed(exc: Exception) -> None:
            self._notify("error", "Startup setting was not saved", str(exc))

        def finished() -> None:
            self._startup_busy = False
            self._startup_requested_mode = None
            self._startup_requested_enabled = None
            self.publish_state()

        start_background_task(
            "WebUpdateStartup",
            work,
            on_success=done,
            on_error=failed,
            on_finished=finished,
            log=self._controller.log,
        )

    @Slot()
    def scanSpeakers(self) -> None:
        def candidate(identity: object) -> None:
            self.toast.emit(self._encode({"kind": "scan", "state": "candidate", "devices": [self._identity_dict(identity)]}))

        last_progress_emit = [0.0]

        def progress(checked: int) -> None:
            # Probe completions arrive per host; keep the UI feed lightweight.
            now = time.monotonic()
            if now - last_progress_emit[0] < 0.15:
                return
            last_progress_emit[0] = now
            self.toast.emit(self._encode({"kind": "scan", "state": "progress", "checked": checked}))

        def done(devices: list[object]) -> None:
            serialized = [self._identity_dict(device) for device in devices]
            self.toast.emit(self._encode({"kind": "scan", "state": "complete", "devices": serialized}))

        def failed(exc: Exception) -> None:
            self.toast.emit(self._encode({"kind": "scan", "state": "failed", "detail": str(exc)}))

        start_background_task(
            "WebScanSpeakers",
            lambda: self._controller.scan_kef_devices(on_candidate=candidate, on_progress=progress),
            on_success=done,
            on_error=failed,
            log=self._controller.log,
        )

    @Slot(str)
    def runEvent(self, event_key: str) -> None:
        event = _EVENTS.get(event_key)
        if event is None:
            self._notify("error", "Unknown test", event_key)
            return
        label, setting_key, runner = event
        if not bool(getattr(self._config, setting_key)):
            self._notify("warning", f"{label}: no action", get_speaker_power_disabled_reason(setting_key))
            self._emit_event_result(event_key, "warning", "No action", get_speaker_power_disabled_reason(setting_key))
            return

        started = time.perf_counter()
        self._emit_event_result(event_key, "running", "Running", "Waiting for the controller to respond…")

        def work() -> tuple[bool, str, int, bool | None]:
            scheduled = runner(self._controller)
            time.sleep(0.15)
            _source, _volume, speaker_on = self._controller.poll_external_ui_state(
                f"web_test_{event_key}", "web_event_test"
            )
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            return scheduled is not False, "Action scheduled" if scheduled is not False else "No action was scheduled", elapsed_ms, speaker_on

        def done(result: tuple[bool, str, int, bool | None]) -> None:
            ok, detail, elapsed_ms, speaker_on = result
            if speaker_on is not None:
                self._speaker_on = speaker_on
            state = "success" if ok else "warning"
            status = "Completed" if ok else "No action"
            self._emit_event_result(event_key, state, status, f"{detail} · {elapsed_ms} ms · Speaker: {self._speaker_label()}")
            self.publish_state()

        start_background_task(f"WebTest{label.replace(' ', '')}", work, on_success=done,
                              on_error=lambda exc: self._emit_event_result(event_key, "error", "Failed", str(exc)),
                              log=self._controller.log)

    @Slot(result=str)
    @Slot(str, result=str)
    def logs(self, selected_name: str = "") -> str:
        return self._encode(self._logs_payload(selected_name))

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

    @Slot(result=str)
    def logFiles(self) -> str:
        return self._encode(self._log_files_payload())

    def _log_files_payload(self) -> list[str]:
        return list_log_history_files(self._config.log_file)

    @Slot()
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
                "scanSpeakers": lambda: self._api_action(self.scanSpeakers),
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

    def publish_state(self) -> None:
        self.state_changed.emit(self._encode(self._state()))

    def _state(self) -> dict[str, Any]:
        identity = self._controller.get_current_identity()
        speaker_on = self._speaker_on if self._speaker_on is not None else bool(identity.available)
        prewarmed_health = self._controller.get_prewarmed_standby_health()
        return {
            "speaker": {
                "name": identity.speaker_name or "No device found",
                "model": identity.speaker_model,
                "ip": identity.ip,
                "mac": identity.mac_display or identity.mac,
                "firmware": identity.firmware_version,
                "available": bool(identity.available),
                "on": speaker_on,
                "status": "Connected" if speaker_on else ("Standby" if identity.ip else "Disconnected"),
                "input": self._input,
                "volume": self._volume,
            },
            "inputs": [{"label": label, "value": value} for label, value in INPUT_SOURCE_OPTIONS],
            "settings": asdict(self._config.user),
            "startup": {
                "registered": self._startup_registered,
                "mode": self._startup_mode,
                "busy": self._startup_busy,
                "requested_mode": self._startup_requested_mode,
                "requested_enabled": self._startup_requested_enabled,
            },
            "health": {
                "last_heartbeat_age_s": prewarmed_health["last_heartbeat_age_s"],
                "heartbeat_failures": prewarmed_health["failures"],
                "heartbeat_error": prewarmed_health["last_error"],
                "last_poll_age_s": (
                    round(max(0.0, time.monotonic() - self._last_poll_success_mono), 1)
                    if self._last_poll_success_mono
                    else None
                ),
                "last_action": self._last_action,
                "last_failure": self._recent_failure(),
            },
        }

    def _poll_speaker_state(self, force: bool = False) -> None:
        if not force and not self._config.home_event_poll_enabled:
            return
        if self._controller.is_system_sleep_pending():
            return
        if not self._controller.get_current_kef_ip():
            return

        def work():
            return self._controller.poll_external_ui_state("web_ui_poll", "web_ui_poll")

        start_background_task(
            "WebPollState",
            work,
            on_success=lambda result: self._polled_state.emit(*result),
            on_error=lambda exc: self._poll_failed.emit(str(exc)),
            lock=self._poll_lock,
            log=self._controller.log,
        )

    def _on_polled_state(self, input_source: object, volume: object, speaker_on: object) -> None:
        self._last_poll_success_mono = time.monotonic()
        normalized_input = normalize_input_source(str(input_source)) if input_source else None
        wake_confirmed = _wake_is_confirmed(speaker_on, normalized_input)
        if self._awaiting_wake_confirmation:
            if wake_confirmed:
                self._awaiting_wake_confirmation = False
            elif speaker_on is not None:
                # The wake request was accepted, but the speaker is not ready
                # for controls until a real poll can read a live input source.
                speaker_on = False
        if input_source:
            self._input = normalized_input or self._input
        if isinstance(volume, int):
            self._volume = volume
        if speaker_on is not None:
            self._speaker_on = bool(speaker_on)
        self.publish_state()

    def _on_poll_failed(self, detail: str) -> None:
        self._last_failure = (detail or "Speaker state check failed", time.monotonic())
        self.publish_state()

    def _on_speaker_state_changed(self, input_source: object, volume: object, speaker_on: object) -> None:
        self._on_polled_state(input_source, volume, speaker_on)

    def _on_power_action_started(self, action: str, _reason: str) -> None:
        normalized_action = str(action or "").upper()
        self._awaiting_wake_confirmation = normalized_action == "WAKE"
        self._last_action_started = time.monotonic()
        self._notify(
            "info",
            "Speaker action",
            f"{action.replace('_', ' ').title()} is running.",
            action=normalized_action,
            phase="started",
        )
        self.publish_state()

    def _on_power_action_finished(self, action: str, _reason: str, success: bool, outcome: str) -> None:
        elapsed = int((time.monotonic() - self._last_action_started) * 1000) if self._last_action_started else 0
        normalized_action = str(action or "").upper()
        self._last_action = {
            "name": normalized_action,
            "elapsed_ms": elapsed,
            "success": bool(success),
        }
        if not success:
            self._last_failure = (outcome or f"{normalized_action.title()} failed", time.monotonic())
        if normalized_action == "WAKE":
            if not success:
                self._awaiting_wake_confirmation = False
        elif success and normalized_action.endswith("STANDBY"):
            self._awaiting_wake_confirmation = False
            self._speaker_on = False
            self._input = "standby"
        # Publish the confirmed action result before the slower live poll. The
        # poll still reconciles later external changes from the speaker.
        self.publish_state()
        self._notify(
            "success" if success else "error",
            action.replace("_", " ").title(),
            f"{outcome or ('Completed' if success else 'Failed')} · {elapsed} ms",
            action=normalized_action,
            phase="finished",
            success=bool(success),
        )
        self._poll_speaker_state(force=True)

    def _on_log_line(self, line: str) -> None:
        self.log_line.emit(line)

    def _apply_poll_interval(self) -> None:
        self._poll_timer.setInterval(max(1000, int(self._config.home_external_poll_interval * 1000)))

    def _recent_failure(self) -> dict[str, object] | None:
        if self._last_failure is None:
            return None
        detail, occurred_mono = self._last_failure
        age_s = max(0.0, time.monotonic() - occurred_mono)
        if age_s > 300.0:
            return None
        return {"detail": detail, "age_s": round(age_s, 1)}

    def _start_action(self, name: str, work: Callable[[], object], message: str, *, action: str) -> None:
        normalized_action = str(action or "").upper()

        def failed(exc: Exception) -> None:
            self._notify(
                "error",
                "Speaker action failed",
                str(exc),
                action=normalized_action,
                phase="finished",
                success=False,
            )

        self._notify("info", "Speaker action", message)
        start_background_task(
            name,
            work,
            on_success=lambda _result: self._poll_speaker_state(force=True),
            on_error=failed,
            log=self._controller.log,
        )

    def _emit_event_result(self, key: str, state: str, title: str, detail: str) -> None:
        self.toast.emit(self._encode({"kind": "event", "key": key, "state": state, "title": title, "detail": detail}))

    def _notify(
        self,
        level: str,
        title: str,
        detail: str,
        *,
        action: str = "",
        phase: str = "",
        success: bool | None = None,
    ) -> None:
        payload: dict[str, object] = {"kind": "toast", "level": level, "title": title, "detail": detail}
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
