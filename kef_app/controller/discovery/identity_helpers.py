from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ...devices.speaker_models import SpeakerIdentity, normalize_model_label


@dataclass(slots=True)
class TargetValidationResult:
    status: str
    requested_ip: str = ""
    requested_mac: str = ""
    identity: SpeakerIdentity = field(default_factory=SpeakerIdentity)


def merge_identity_details(
    primary: Optional[SpeakerIdentity],
    supplement: Optional[SpeakerIdentity],
) -> Optional[SpeakerIdentity]:
    if not primary:
        return supplement
    if not supplement:
        return primary
    return SpeakerIdentity(
        ip=primary.ip or supplement.ip,
        mac=primary.mac or supplement.mac,
        mac_display=primary.mac_display or supplement.mac_display,
        speaker_name=primary.speaker_name or supplement.speaker_name,
        speaker_model=primary.speaker_model or supplement.speaker_model,
        firmware_version=primary.firmware_version or supplement.firmware_version,
        available=primary.available or supplement.available,
        backend=primary.backend or supplement.backend,
        matched_by=primary.matched_by or supplement.matched_by,
    )


def missing_identity_fields(identity: Optional[SpeakerIdentity]) -> str:
    if not identity:
        return "identity"
    missing = []
    if not identity.speaker_model:
        missing.append("speaker_model")
    if not identity.mac:
        missing.append("mac")
    return ",".join(missing) or "<none>"


def is_urgent_standby_identity_trigger(trigger: str) -> bool:
    text = str(trigger or "")
    return text.startswith(
        (
            "lock_pre_standby_",
            "standby_before_request_",
            "endsession_before_request_",
        )
    )


def can_use_cached_current_target(
    *,
    identity: SpeakerIdentity,
    trigger: str,
    target_mac: str,
    current_ip: str,
    configured_ip: str,
    cached_mac: str,
    cached_model: str,
) -> bool:
    if not is_urgent_standby_identity_trigger(trigger):
        return False
    if not target_mac or identity.mac:
        return False
    if not current_ip or identity.ip != current_ip:
        return False
    if configured_ip and configured_ip != current_ip:
        return False
    if cached_mac != target_mac or not cached_model:
        return False
    live_model = normalize_model_label(identity.speaker_model)
    if live_model and live_model != normalize_model_label(cached_model):
        return False
    return True
