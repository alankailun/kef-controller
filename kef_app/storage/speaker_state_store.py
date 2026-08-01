from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass

from ..config import AppConfig
from ..structured_logging import log_structured
from .json_file import write_json_atomic
from ..devices.speaker_models import SpeakerIdentity, normalize_mac


@dataclass(slots=True)
class PersistedSpeakerState:
    last_ip: str = ""
    last_mac: str = ""
    last_speaker_name: str = ""
    last_speaker_model: str = ""
    last_firmware_version: str = ""
    backend: str = "w2"
    matched_by: str = ""
    last_seen_at: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "PersistedSpeakerState":
        return cls(
            last_ip=str(data.get("last_ip", "") or ""),
            last_mac=normalize_mac(str(data.get("last_mac", "") or "")),
            last_speaker_name=str(data.get("last_speaker_name", "") or ""),
            last_speaker_model=str(data.get("last_speaker_model", "") or ""),
            last_firmware_version=str(data.get("last_firmware_version", "") or ""),
            backend=str(data.get("backend", "w2") or "w2"),
            matched_by=str(data.get("matched_by", "") or ""),
            last_seen_at=str(data.get("last_seen_at", "") or ""),
        )

    def to_dict(self) -> dict:
        return {
            "last_ip": self.last_ip,
            "last_mac": self.last_mac,
            "last_speaker_name": self.last_speaker_name,
            "last_speaker_model": self.last_speaker_model,
            "last_firmware_version": self.last_firmware_version,
            "backend": self.backend,
            "matched_by": self.matched_by,
            "last_seen_at": self.last_seen_at,
        }

    @classmethod
    def from_identity(cls, identity: SpeakerIdentity) -> "PersistedSpeakerState":
        return cls(
            last_ip=identity.ip or "",
            last_mac=normalize_mac(identity.mac or identity.mac_display or ""),
            last_speaker_name=identity.speaker_name or "",
            last_speaker_model=identity.speaker_model or "",
            last_firmware_version=identity.firmware_version or "",
            backend=identity.backend or "w2",
            matched_by=identity.matched_by or "",
            last_seen_at=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        )


class SpeakerStateStore:
    def __init__(self, config: AppConfig, log):
        self.config = config
        self.log = log
        self.path = config.state_file
        self._last_saved_state: PersistedSpeakerState | None = None

    @staticmethod
    def _same_saved_identity(left: PersistedSpeakerState, right: PersistedSpeakerState) -> bool:
        return (
            left.last_ip == right.last_ip
            and left.last_mac == right.last_mac
            and left.last_speaker_name == right.last_speaker_name
            and left.last_speaker_model == right.last_speaker_model
            and left.last_firmware_version == right.last_firmware_version
            and left.backend == right.backend
            and left.matched_by == right.matched_by
        )

    def load(self) -> PersistedSpeakerState:
        if not self.config.persist_runtime_state:
            log_structured(
                self.log, "SKIP", action="PERSIST_RUNTIME_STATE", reason="startup", cause="persistence_disabled"
            )
            return PersistedSpeakerState()

        if not os.path.exists(self.path):
            log_structured(
                self.log,
                "STEP",
                action="PERSIST_RUNTIME_STATE",
                reason="startup",
                step="load",
                status="state_file_not_found",
                state_file=self.path,
            )
            return PersistedSpeakerState()

        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            state = PersistedSpeakerState.from_dict(data if isinstance(data, dict) else {})
            self._last_saved_state = state
            log_structured(
                self.log,
                "STEP",
                action="PERSIST_RUNTIME_STATE",
                reason="startup",
                step="load",
                status="loaded",
                state_file=self.path,
                actual_ip=state.last_ip or "<empty>",
                actual_mac=state.last_mac or "<empty>",
                actual_speaker_name=state.last_speaker_name or "<empty>",
                actual_speaker_model=state.last_speaker_model or "<empty>",
            )
            return state
        except Exception as exc:
            log_structured(
                self.log,
                "WARN",
                action="PERSIST_RUNTIME_STATE",
                reason="startup",
                cause="state_file_read_failed",
                state_file=self.path,
                error=repr(exc),
            )
            return PersistedSpeakerState()

    def save(self, identity: SpeakerIdentity, trigger: str) -> bool:
        if not self.config.persist_runtime_state:
            return False

        state = PersistedSpeakerState.from_identity(identity)
        previous = self._last_saved_state
        if previous is None and os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                previous = PersistedSpeakerState.from_dict(data if isinstance(data, dict) else {})
            except Exception:
                previous = None

        if previous is not None and self._same_saved_identity(previous, state):
            self._last_saved_state = previous
            log_structured(
                self.log,
                "SKIP",
                log_level="DEBUG",
                action="PERSIST_RUNTIME_STATE",
                trigger=trigger,
                cause="unchanged_identity",
                state_file=self.path,
                actual_ip=state.last_ip or "<empty>",
                actual_mac=state.last_mac or "<empty>",
            )
            return False

        try:
            write_json_atomic(self.path, state.to_dict(), prefix="speaker_state_")
            self._last_saved_state = state

            log_structured(
                self.log,
                "STEP",
                action="PERSIST_RUNTIME_STATE",
                trigger=trigger,
                step="save",
                status="saved",
                state_file=self.path,
                actual_ip=state.last_ip or "<empty>",
                actual_mac=state.last_mac or "<empty>",
                actual_speaker_name=state.last_speaker_name or "<empty>",
                actual_speaker_model=state.last_speaker_model or "<empty>",
            )
            return True
        except Exception as exc:
            log_structured(
                self.log,
                "WARN",
                action="PERSIST_RUNTIME_STATE",
                trigger=trigger,
                cause="state_file_save_failed",
                state_file=self.path,
                error=repr(exc),
            )
            return False
