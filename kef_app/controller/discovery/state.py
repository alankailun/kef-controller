from __future__ import annotations

from ...devices.scan.network import is_routable_ipv4
from ...devices.speaker_models import SpeakerIdentity, normalize_mac


class ControllerIdentityStateMixin:
    def get_current_kef_ip(self) -> str:
        with self._ip_lock:
            return self._identity.current_ip

    def get_effective_target_mac(self) -> str:
        configured_mac = normalize_mac(self.config.kef_mac)
        if configured_mac:
            return configured_mac
        with self._ip_lock:
            return self._identity.target_mac

    def get_current_kef_target(self) -> tuple[str, str]:
        """Snapshot the current IP and effective target MAC under one lock."""
        configured_mac = normalize_mac(self.config.kef_mac)
        with self._ip_lock:
            return self._identity.current_ip, configured_mac or self._identity.target_mac

    def get_current_identity(self) -> SpeakerIdentity:
        with self._ip_lock:
            return SpeakerIdentity(
                ip=self._identity.current_ip,
                mac=self._identity.target_mac,
                mac_display=self._identity.target_mac,
                speaker_name=self._identity.speaker_name,
                speaker_model=self._identity.speaker_model,
                firmware_version=self._identity.speaker_firmware,
                available=bool(self._identity.current_ip) and self._identity.available,
                backend=self.config.backend_name,
                matched_by=self._identity.last_matched_by,
            )

    def _mark_identity_probe_success(self, trigger: str) -> bool:
        with self._ip_lock:
            current_ip = self._identity.current_ip
            previous_failures = self._identity.probe_failures
            availability_changed = bool(current_ip) and not self._identity.available
            self._identity.probe_failures = 0
            self._identity.available = bool(current_ip)

        if previous_failures or availability_changed:
            self._log_structured(
                "STEP",
                action="IDENTITY_PROBE",
                step="mark_available",
                trigger=trigger,
                current_ip=current_ip or "<empty>",
                previous_failures=previous_failures,
            )
        return availability_changed

    def record_identity_probe_failure(self, reason: str, trigger: str, cause: str) -> bool:
        threshold = max(1, int(self.config.identity_probe_failure_threshold))
        with self._ip_lock:
            current_ip = self._identity.current_ip
            if not current_ip:
                return False

            self._identity.probe_failures += 1
            failures = self._identity.probe_failures
            offline = failures >= threshold
            threshold_crossed = failures == threshold
            availability_changed = offline and self._identity.available
            if offline:
                self._identity.available = False

        # UI polls repeat every few seconds.  The first failure and the point
        # at which it becomes offline are useful state transitions; subsequent
        # identical failures only displace the original diagnosis.
        if failures == 1 or threshold_crossed:
            self._log_structured(
                "WARN",
                action="IDENTITY_PROBE",
                step="mark_failure",
                reason=reason,
                trigger=trigger,
                cause=cause,
                current_ip=current_ip,
                failures=failures,
                threshold=threshold,
                offline=offline,
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
            return self._identity.target_mac

    def update_identity_from_device_info(self, info: SpeakerIdentity | None, trigger: str) -> bool:
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
            old_mac = self._identity.target_mac
            old_name = self._identity.speaker_name
            old_model = self._identity.speaker_model
            old_firmware = self._identity.speaker_firmware
            if mac_norm and self._identity.target_mac != mac_norm:
                self._identity.target_mac = mac_norm
                changed = True
            if speaker_name and self._identity.speaker_name != speaker_name:
                self._identity.speaker_name = speaker_name
                changed = True
            if speaker_model and self._identity.speaker_model != speaker_model:
                self._identity.speaker_model = speaker_model
                changed = True
            if firmware_version and self._identity.speaker_firmware != firmware_version:
                self._identity.speaker_firmware = firmware_version
                changed = True
            if info.matched_by and self._identity.last_matched_by != info.matched_by:
                self._identity.last_matched_by = info.matched_by
                changed = True

        for step, old_val, new_val, kw_old, kw_new, display_val in [
            ("update_target_mac", old_mac, mac_norm, "previous_mac", "actual_mac", mac_display or mac_norm),
            ("update_speaker_name", old_name, speaker_name, "previous_speaker_name", "actual_speaker_name", speaker_name),
            ("update_speaker_model", old_model, speaker_model, "previous_speaker_model", "actual_speaker_model", speaker_model),
            ("update_firmware_version", old_firmware, firmware_version, "previous_firmware_version", "actual_firmware_version", firmware_version),
        ]:
            if new_val and old_val != new_val:
                self._log_structured(
                    "STEP",
                    action="DISCOVER_IP",
                    step=step,
                    trigger=trigger,
                    **{kw_old: old_val or "<empty>", kw_new: display_val},
                )
        availability_changed = self._mark_identity_probe_success(trigger=f"identity:{trigger}")
        if changed:
            self._refresh_fast_standby_send_cache()
            self._persist_runtime_state(trigger=f"identity:{trigger}")
        if changed or availability_changed:
            self._emit_identity_changed()
        return changed

    def update_kef_ip(self, new_ip: str, trigger: str) -> bool:
        if not is_routable_ipv4(new_ip):
            return False
        with self._ip_lock:
            old_ip = self._identity.current_ip
            if old_ip != new_ip:
                self._identity.current_ip = new_ip

        availability_changed = self._mark_identity_probe_success(trigger=f"ip:{trigger}")
        if old_ip == new_ip:
            self._log_structured(
                "STEP",
                action="DISCOVER_IP",
                step="confirm_current_ip",
                trigger=trigger,
                actual_ip=new_ip,
            )
            if availability_changed:
                self._emit_identity_changed()
            return False

        self._refresh_fast_standby_send_cache()
        self.reset_speaker()
        self._persist_runtime_state(trigger=f"ip:{trigger}")
        self._log_structured(
            "STEP",
            action="DISCOVER_IP",
            step="update_current_ip",
            trigger=trigger,
            previous_ip=old_ip,
            actual_ip=new_ip,
        )
        self._emit_identity_changed()
        return True

    def apply_configured_device_target(self, trigger: str) -> bool:
        configured_ip = str(self.config.kef_ip or "").strip()
        configured_mac = normalize_mac(self.config.kef_mac)

        ignored_ip = ""
        ip_changed = False
        mac_changed = False
        old_ip = ""
        old_mac = ""

        with self._ip_lock:
            old_ip = self._identity.current_ip
            old_mac = self._identity.target_mac

            if configured_ip:
                if is_routable_ipv4(configured_ip):
                    if old_ip != configured_ip:
                        self._identity.current_ip = configured_ip
                        self._identity.available = True
                        self._identity.probe_failures = 0
                        ip_changed = True
                else:
                    ignored_ip = configured_ip
            elif old_ip:
                self._identity.current_ip = ""
                self._identity.available = False
                self._identity.probe_failures = 0
                ip_changed = True

            if old_mac != configured_mac:
                self._identity.target_mac = configured_mac
                mac_changed = True

        if ignored_ip:
            self._log_structured(
                "WARN",
                action="CONFIG_SYNC",
                step="configured_device_target",
                trigger=trigger,
                cause="invalid_configured_ip",
                configured_ip=ignored_ip,
            )

        if not (ip_changed or mac_changed):
            return False

        self._refresh_fast_standby_send_cache()
        if ip_changed:
            self.reset_speaker()

        self._persist_runtime_state(trigger=f"config:{trigger}")
        self._log_structured(
            "STEP",
            action="CONFIG_SYNC",
            step="configured_device_target",
            trigger=trigger,
            previous_ip=old_ip or "<empty>",
            actual_ip=self.get_current_kef_ip() or "<empty>",
            previous_mac=old_mac or "<empty>",
            target_mac=self.get_effective_target_mac() or "<empty>",
            ip_changed=ip_changed,
            mac_changed=mac_changed,
        )
        self._emit_identity_changed()
        return True
