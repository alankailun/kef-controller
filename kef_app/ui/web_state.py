from __future__ import annotations

import time
from dataclasses import asdict
from typing import Any

from ..config import AppConfig
from ..controller.state_models import SpeakerUIPollResult
from ..devices.speaker_models import INPUT_SOURCE_OPTIONS, normalize_input_source
from .background_tasks import start_background_task


def _wake_is_confirmed(speaker_on: object, input_source: str | None) -> bool:
    """Only enable live controls after the speaker reports a usable source."""
    return speaker_on is True and bool(input_source) and input_source != "standby"


class WebStateMixin:
    def publish_state(self) -> None:
        self.state_changed.emit(self._encode(self._runtime_state()))

    def publish_settings(self) -> None:
        self.settings_changed.emit(self._encode(self._settings_state()))

    def _state(self) -> dict[str, Any]:
        return {**self._runtime_state(), **self._settings_state()}

    def _settings_state(self) -> dict[str, Any]:
        return {
            "inputs": [{"label": label, "value": value} for label, value in INPUT_SOURCE_OPTIONS],
            "settings": asdict(self._config.user),
        }

    def _runtime_state(self) -> dict[str, Any]:
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
                "status": "Connected" if speaker_on else ("Standby" if identity.ip and identity.available else "Disconnected"),
                "input": self._input,
                "volume": self._volume,
            },
            "startup": {
                "registered": self._startup_registered,
                "mode": self._startup_mode,
                "busy": self._startup_busy,
                "pending": self._startup_snapshot_pending,
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
        target_generation = self._controller.get_target_generation()

        def work():
            return self._controller.poll_external_ui_state_result("web_ui_poll", "web_ui_poll")

        def completed(result: SpeakerUIPollResult) -> None:
            if result.status == "success":
                self._polled_state.emit(*result.values, target_generation)
            elif result.status == "failed":
                self._poll_failed.emit("Speaker state check failed: no verified state was received.")

        start_background_task(
            "WebPollState",
            work,
            on_success=completed,
            on_error=lambda exc: self._poll_failed.emit(str(exc)),
            lock=self._poll_lock,
            log=self._controller.log,
        )

    def _on_polled_state(
        self, input_source: object, volume: object, speaker_on: object,
        target_generation: object = None,
    ) -> None:
        if target_generation is not None and target_generation != self._controller.get_target_generation():
            return
        # Defensive for direct callbacks and future poll producers.
        if all(value is None for value in (input_source, volume, speaker_on)):
            return
        self._last_poll_success_mono = time.monotonic()
        self._last_failure = None
        self._apply_speaker_state(input_source, volume, speaker_on)

    def _on_speaker_state_changed(
        self, input_source: object, volume: object, speaker_on: object,
        target_generation: object = None,
    ) -> None:
        """Apply controller-owned state without presenting it as a network poll."""
        if target_generation is not None and target_generation != self._controller.get_target_generation():
            return
        self._apply_speaker_state(input_source, volume, speaker_on)

    def _apply_speaker_state(self, input_source: object, volume: object, speaker_on: object) -> None:
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

    def _on_identity_changed(self, _identity: object) -> None:
        target = self._controller.get_current_kef_target()
        if target != self._target_signature:
            self._target_signature = target
            self._input = ""
            self._volume = None
            self._speaker_on = None
            self._last_poll_success_mono = 0.0
            self._last_failure = None
            self._last_action_failure = None
        self.publish_state()

    def _on_power_action_started(self, action: str, _reason: str) -> None:
        normalized_action = str(action or "").upper()
        self._awaiting_wake_confirmation = normalized_action == "WAKE"
        self._last_action_started = time.monotonic()
        self._notify(
            "info",
            "Speaker action",
            f"{action.replace('_', ' ').title()} is running.",
            code="speaker_action_running",
            params={"action": normalized_action},
            channel="power",
            action=normalized_action,
            phase="started",
        )
        self.publish_state()

    def _on_power_action_finished(self, action: str, _reason: str, success: bool, outcome: str, confirmed: bool = False) -> None:
        elapsed = int((time.monotonic() - self._last_action_started) * 1000) if self._last_action_started else 0
        normalized_action = str(action or "").upper()
        self._last_action = {
            "name": normalized_action,
            "elapsed_ms": elapsed,
            "success": bool(success),
        }
        if not success:
            self._last_action_failure = (outcome or f"{normalized_action.title()} failed", time.monotonic())
        else:
            self._last_action_failure = None
        if normalized_action == "WAKE":
            if not success:
                self._awaiting_wake_confirmation = False
        elif confirmed:
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
            code="speaker_action_finished",
            params={
                "action": normalized_action,
                "outcome": outcome or ("Completed" if success else "Failed"),
                "elapsed": elapsed,
            },
            channel="power",
            action=normalized_action,
            phase="finished",
            success=bool(success),
        )
        self._poll_speaker_state(force=True)

    def _apply_poll_interval(self) -> None:
        self._poll_timer.setInterval(max(1000, int(self._config.home_external_poll_interval * 1000)))

    def _copy_user_editable_fields(self, source: AppConfig) -> None:
        for field_name in self._config_store.USER_EDITABLE_FIELDS:
            setattr(self._config, field_name, getattr(source, field_name))

    def _recent_failure(self) -> dict[str, object] | None:
        action_failure = getattr(self, "_last_action_failure", None)
        failure = action_failure if action_failure and time.monotonic() - action_failure[1] <= 300.0 else self._last_failure
        if failure is None:
            return None
        detail, occurred_mono = failure
        age_s = max(0.0, time.monotonic() - occurred_mono)
        if age_s > 300.0:
            return None
        return {"detail": detail, "age_s": round(age_s, 1)}

