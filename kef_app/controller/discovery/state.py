from __future__ import annotations

from typing import Optional

from ...devices.discovery import is_routable_ipv4
from ...devices.speaker_models import SpeakerIdentity, normalize_mac


class ControllerIdentityStateMixin:
    @staticmethod
    def _is_ui_poll_trigger(trigger: str) -> bool:
        return str(trigger).startswith(("ui_home_poll", "ui_tray_poll"))

    def get_current_kef_ip(self) -> str:
        with self._ip_lock:
            return self._current_kef_ip

    def get_effective_target_mac(self) -> str:
        configured_mac = normalize_mac(self.config.kef_mac)
        if configured_mac:
            return configured_mac
        with self._ip_lock:
            return self._target_kef_mac

    def get_current_identity(self) -> SpeakerIdentity:
        with self._ip_lock:
            return SpeakerIdentity(
                ip=self._current_kef_ip,
                mac=self._target_kef_mac,
                mac_display=self._target_kef_mac,
                speaker_name=self._speaker_name,
                speaker_model=self._speaker_model,
                firmware_version=self._speaker_firmware,
                available=bool(self._current_kef_ip) and self._identity_available,
                backend=self.config.backend_name,
                matched_by=self._last_matched_by,
            )

    def select_kef_device(self, identity: SpeakerIdentity, source: str) -> bool:
        ip = str(identity.ip or "").strip()
        if not is_routable_ipv4(ip):
            self._log_structured(
                "WARN",
                action="SELECT_DEVICE",
                source=source,
                cause="invalid_ip",
                ip=ip or "<empty>",
                mono=f"{self.mono():.3f}",
            )
            return False

        mac_norm = normalize_mac(identity.mac or identity.mac_display or "")
        speaker_name = identity.speaker_name or ""
        speaker_model = identity.speaker_model or ""
        firmware_version = identity.firmware_version or ""

        with self._ip_lock:
            old_ip = self._current_kef_ip
            old_mac = self._target_kef_mac
            old_name = self._speaker_name
            old_model = self._speaker_model
            old_firmware = self._speaker_firmware
            old_matched_by = self._last_matched_by

            self._current_kef_ip = ip
            self._target_kef_mac = mac_norm
            self._speaker_name = speaker_name
            self._speaker_model = speaker_model
            self._speaker_firmware = firmware_version
            self._last_matched_by = identity.matched_by or "manual"
            self._identity_available = True
            self._identity_probe_failures = 0

            changed = (
                old_ip != self._current_kef_ip
                or old_mac != self._target_kef_mac
                or old_name != self._speaker_name
                or old_model != self._speaker_model
                or old_firmware != self._speaker_firmware
                or old_matched_by != self._last_matched_by
            )

        if not changed:
            self._log_structured(
                "STEP",
                action="SELECT_DEVICE",
                source=source,
                outcome="unchanged",
                ip=ip,
                mac=mac_norm or "<empty>",
                mono=f"{self.mono():.3f}",
            )
            return False

        self.reset_speaker()
        self._persist_runtime_state(source=f"select:{source}")
        self._log_structured(
            "STEP",
            action="SELECT_DEVICE",
            source=source,
            outcome="selected",
            old_ip=old_ip or "<empty>",
            new_ip=ip,
            old_mac=old_mac or "<empty>",
            new_mac=mac_norm or "<empty>",
            speaker_name=speaker_name or "<empty>",
            speaker_model=speaker_model or "<empty>",
            mono=f"{self.mono():.3f}",
        )
        self._emit_identity_changed()
        return True

    def _mark_identity_probe_success(self, source: str) -> bool:
        with self._ip_lock:
            current_ip = self._current_kef_ip
            previous_failures = self._identity_probe_failures
            availability_changed = bool(current_ip) and not self._identity_available
            self._identity_probe_failures = 0
            self._identity_available = bool(current_ip)

        if previous_failures or availability_changed:
            self._log_structured(
                "STEP",
                log_level="info",
                action="IDENTITY_PROBE",
                step="mark_available",
                source=source,
                current_ip=current_ip or "<empty>",
                previous_failures=previous_failures,
                mono=f"{self.mono():.3f}",
            )
        return availability_changed

    def record_identity_probe_failure(self, source: str, trigger: str, cause: str) -> bool:
        threshold = max(1, int(self.config.identity_probe_failure_threshold))
        with self._ip_lock:
            current_ip = self._current_kef_ip
            if not current_ip:
                return False

            self._identity_probe_failures += 1
            failures = self._identity_probe_failures
            offline = failures >= threshold
            availability_changed = offline and self._identity_available
            if offline:
                self._identity_available = False

        self._log_structured(
            "WARN",
            action="IDENTITY_PROBE",
            step="mark_failure",
            source=source,
            trigger=trigger,
            cause=cause,
            current_ip=current_ip,
            failures=failures,
            threshold=threshold,
            offline=offline,
            mono=f"{self.mono():.3f}",
        )
        if availability_changed:
            self._emit_identity_changed()
        return availability_changed

    def _identity_matches_expectation(self, identity: SpeakerIdentity) -> tuple[bool, str]:
        target_mac = self.get_effective_target_mac()
        if target_mac:
            if not identity.mac:
                return False, "target_mac_unverified"
            if identity.mac != target_mac:
                return False, "target_mac_mismatch"
            return True, "target_mac"
        return True, "no_target_mac"

    def get_target_kef_mac(self) -> str:
        with self._ip_lock:
            return self._target_kef_mac

    def update_identity_from_device_info(self, info: Optional[SpeakerIdentity], source: str) -> bool:
        if not info:
            return False

        mac_norm = normalize_mac(info.mac or info.mac_display or "")
        mac_display = info.mac_display or info.mac or ""
        speaker_name = info.speaker_name or ""
        speaker_model = info.speaker_model or ""
        firmware_version = info.firmware_version or ""

        old_mac = ""
        old_name = ""
        old_model = ""
        old_firmware = ""
        changed = False

        with self._ip_lock:
            old_mac = self._target_kef_mac
            old_name = self._speaker_name
            old_model = self._speaker_model
            old_firmware = self._speaker_firmware
            if mac_norm and self._target_kef_mac != mac_norm:
                self._target_kef_mac = mac_norm
                changed = True
            if speaker_name and self._speaker_name != speaker_name:
                self._speaker_name = speaker_name
                changed = True
            if speaker_model and self._speaker_model != speaker_model:
                self._speaker_model = speaker_model
                changed = True
            if firmware_version and self._speaker_firmware != firmware_version:
                self._speaker_firmware = firmware_version
                changed = True
            if info.matched_by and self._last_matched_by != info.matched_by:
                self._last_matched_by = info.matched_by
                changed = True

        for step, old_val, new_val, kw_old, kw_new, display_val in [
            ("update_target_mac", old_mac, mac_norm, "old_mac", "new_mac", mac_display or mac_norm),
            ("update_speaker_name", old_name, speaker_name, "old_name", "new_name", speaker_name),
            ("update_speaker_model", old_model, speaker_model, "old_model", "new_model", speaker_model),
            ("update_firmware_version", old_firmware, firmware_version, "old_firmware", "new_firmware", firmware_version),
        ]:
            if new_val and old_val != new_val:
                self._log_structured(
                    "STEP",
                    log_level="info",
                    action="DISCOVER_IP",
                    step=step,
                    source=source,
                    **{kw_old: old_val or "<empty>", kw_new: display_val},
                    mono=f"{self.mono():.3f}",
                )
        availability_changed = self._mark_identity_probe_success(source=f"identity:{source}")
        if changed:
            self._persist_runtime_state(source=f"identity:{source}")
        if changed or availability_changed:
            self._emit_identity_changed()
        return changed

    def update_kef_ip(self, new_ip: str, source: str) -> bool:
        if not is_routable_ipv4(new_ip):
            return False
        with self._ip_lock:
            old_ip = self._current_kef_ip
            if old_ip != new_ip:
                self._current_kef_ip = new_ip

        availability_changed = self._mark_identity_probe_success(source=f"ip:{source}")
        if old_ip == new_ip:
            self._log_structured(
                "STEP",
                action="DISCOVER_IP",
                step="confirm_current_ip",
                source=source,
                ip=new_ip,
                mono=f"{self.mono():.3f}",
            )
            if availability_changed:
                self._emit_identity_changed()
            return False

        self.reset_speaker()
        self._persist_runtime_state(source=f"ip:{source}")
        self._log_structured(
            "STEP",
            log_level="info",
            action="DISCOVER_IP",
            step="update_current_ip",
            source=source,
            old_ip=old_ip,
            new_ip=new_ip,
            mono=f"{self.mono():.3f}",
        )
        self._emit_identity_changed()
        return True

    def apply_configured_device_target(self, source: str) -> bool:
        configured_ip = str(self.config.kef_ip or "").strip()
        configured_mac = normalize_mac(self.config.kef_mac)

        ignored_ip = ""
        ip_changed = False
        mac_changed = False
        old_ip = ""
        old_mac = ""

        with self._ip_lock:
            old_ip = self._current_kef_ip
            old_mac = self._target_kef_mac

            if configured_ip:
                if is_routable_ipv4(configured_ip):
                    if old_ip != configured_ip:
                        self._current_kef_ip = configured_ip
                        self._identity_available = True
                        self._identity_probe_failures = 0
                        ip_changed = True
                else:
                    ignored_ip = configured_ip
            elif old_ip:
                self._current_kef_ip = ""
                self._identity_available = False
                self._identity_probe_failures = 0
                ip_changed = True

            if old_mac != configured_mac:
                self._target_kef_mac = configured_mac
                mac_changed = True

        if ignored_ip:
            self._log_structured(
                "WARN",
                action="CONFIG_SYNC",
                step="configured_device_target",
                source=source,
                cause="invalid_configured_ip",
                ip=ignored_ip,
                mono=f"{self.mono():.3f}",
            )

        if not (ip_changed or mac_changed):
            return False

        if ip_changed:
            self.reset_speaker()

        self._persist_runtime_state(source=f"config:{source}")
        self._log_structured(
            "STEP",
            log_level="info",
            action="CONFIG_SYNC",
            step="configured_device_target",
            source=source,
            old_ip=old_ip or "<empty>",
            new_ip=self.get_current_kef_ip() or "<empty>",
            old_mac=old_mac or "<empty>",
            new_mac=self.get_effective_target_mac() or "<empty>",
            ip_changed=ip_changed,
            mac_changed=mac_changed,
            mono=f"{self.mono():.3f}",
        )
        self._emit_identity_changed()
        return True
