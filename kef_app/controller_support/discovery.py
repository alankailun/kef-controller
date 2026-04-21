from __future__ import annotations

from typing import Optional

from ..discovery import (
    discover_ip_by_mac,
    discover_kef_device_blind,
    identify_kef_device,
    is_routable_ipv4,
    normalize_mac,
)
from ..models import SpeakerIdentity, normalize_name
from .common import temporary_socket_timeout


class ControllerDiscoveryMixin:
    @staticmethod
    def _is_ui_poll_trigger(trigger: str) -> bool:
        return str(trigger).startswith(("ui_home_poll", "ui_tray_poll"))

    def get_current_kef_ip(self) -> str:
        with self._ip_lock:
            return self._current_kef_ip

    def get_effective_target_mac(self) -> str:
        expected_mac = normalize_mac(self.config.expected_speaker_mac)
        if expected_mac:
            return expected_mac
        with self._ip_lock:
            return self._target_kef_mac

    def get_expected_speaker_name(self) -> str:
        return self.config.expected_speaker_name.strip()

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
        expected_name = normalize_name(self.get_expected_speaker_name())
        expected_mac = self.get_effective_target_mac()
        if expected_mac and identity.mac and identity.mac != expected_mac:
            return False, "expected_mac_mismatch"
        if expected_name:
            if normalize_name(identity.speaker_name) != expected_name:
                return False, "expected_name_mismatch"
            return True, "expected_name"
        if expected_mac:
            return True, "expected_mac"
        return True, "no_expectation"

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

    def capture_identity_from_current_ip(self, reason: str, trigger: str) -> bool:
        current_ip = self.get_current_kef_ip()
        if not current_ip:
            return False

        info: Optional[SpeakerIdentity] = None
        try:
            with temporary_socket_timeout(self.config.socket_timeout):
                speaker = self.get_speaker(fresh=False)
                info = self._backend.capture_identity(speaker, current_ip)
        except Exception:
            info = None

        if not info or not info.speaker_model:
            info = identify_kef_device(current_ip, self.config, self.log)

        if not info:
            self._log_structured(
                "SKIP",
                action="DISCOVER_IP",
                reason=reason,
                cause="identity_probe_failed",
                trigger=trigger,
                current_ip=current_ip,
                mono=f"{self.mono():.3f}",
            )
            return False

        changed = self.update_identity_from_device_info(info, source=trigger)
        matched, match_reason = self._identity_matches_expectation(info)
        should_log_success = changed or not matched or not self._is_ui_poll_trigger(trigger)
        if should_log_success:
            self._log_structured(
                "STEP",
                action="DISCOVER_IP",
                reason=reason,
                step="capture_identity_from_current_ip",
                trigger=trigger,
                current_ip=current_ip,
                changed=changed,
                speaker_model=info.speaker_model or "",
                speaker_name=info.speaker_name or "",
                matched=matched,
                match_reason=match_reason,
                mono=f"{self.mono():.3f}",
            )
        if not matched:
            self._log_structured(
                "WARN",
                action="DISCOVER_IP",
                reason=reason,
                cause=match_reason,
                trigger=trigger,
                current_ip=current_ip,
                expected_name=self.get_expected_speaker_name() or "<empty>",
                expected_mac=self.get_effective_target_mac() or "<empty>",
                actual_name=info.speaker_name or "<empty>",
                actual_mac=info.mac_display or info.mac or "<empty>",
                mono=f"{self.mono():.3f}",
            )
        return True

    def probe_external_identity(self, reason: str, trigger: str) -> tuple[bool, bool]:
        identity_seen = self.capture_identity_from_current_ip(reason=reason, trigger=f"{trigger}_identity")
        ip_refreshed = False
        if not identity_seen:
            ip_refreshed = self.maybe_refresh_kef_ip(reason=reason, trigger=f"{trigger}_refresh")
        reachable = identity_seen or ip_refreshed
        if not reachable:
            self.record_identity_probe_failure(source=reason, trigger=trigger, cause="identity_refresh_failed")

        if not (self._is_ui_poll_trigger(trigger) and identity_seen and not ip_refreshed):
            self._log_structured(
                "STEP",
                action="IDENTITY_PROBE",
                reason=reason,
                trigger=trigger,
                fallback_identity=identity_seen,
                fallback_ip_refresh=ip_refreshed,
                reachable=reachable,
                mono=f"{self.mono():.3f}",
            )
        return identity_seen, ip_refreshed

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
            action="DISCOVER_IP",
            step="update_current_ip",
            source=source,
            old_ip=old_ip,
            new_ip=new_ip,
            mono=f"{self.mono():.3f}",
        )
        self._emit_identity_changed()
        return True

    def maybe_refresh_kef_ip_by_mac(self, reason: str, trigger: str, force: bool = False) -> bool:
        c = self.config
        if not c.auto_discover_kef_ip_by_mac:
            return False
        effective_mac = self.get_effective_target_mac()
        if not effective_mac:
            self._log_structured(
                "SKIP",
                action="DISCOVER_IP",
                reason=reason,
                cause="empty_kef_mac",
                trigger=trigger,
                mono=f"{self.mono():.3f}",
            )
            return False
        if not self._discovery_lock.acquire(blocking=False):
            self._log_structured(
                "SKIP",
                action="DISCOVER_IP",
                reason=reason,
                cause="discovery_already_running",
                trigger=trigger,
                mono=f"{self.mono():.3f}",
            )
            return False

        try:
            now = self.mono()
            elapsed = now - self._last_mac_discovery_mono
            if not force and elapsed < c.mac_discovery_cooldown:
                self._log_structured(
                    "SKIP",
                    action="DISCOVER_IP",
                    reason=reason,
                    cause="cooldown",
                    trigger=trigger,
                    cooldown_s=f"{c.mac_discovery_cooldown:.1f}",
                    elapsed_s=f"{elapsed:.1f}",
                    mono=f"{now:.3f}",
                )
                return False

            self._last_mac_discovery_mono = now
            seed_ip = self.get_current_kef_ip()
            target_mac = self.get_effective_target_mac()
            self._log_structured(
                "BEGIN",
                action="DISCOVER_IP",
                reason=reason,
                trigger=trigger,
                seed_ip=seed_ip,
                target_mac=target_mac or "<empty>",
                current_ip=self.get_current_kef_ip(),
                mono=f"{now:.3f}",
            )
            discovered_ip = discover_ip_by_mac(target_mac, seed_ip, c, self.log)
            if not discovered_ip:
                self._log_structured(
                    "END",
                    action="DISCOVER_IP",
                    reason=reason,
                    trigger=trigger,
                    outcome="not_found",
                    mono=f"{self.mono():.3f}",
                )
                return False

            changed = self.update_kef_ip(discovered_ip, source=trigger)
            self._log_structured(
                "END",
                action="DISCOVER_IP",
                reason=reason,
                trigger=trigger,
                outcome="ip_updated" if changed else "ip_confirmed",
                ip=discovered_ip,
                mono=f"{self.mono():.3f}",
            )
            return True
        finally:
            self._discovery_lock.release()

    def maybe_refresh_kef_ip_by_blind(self, reason: str, trigger: str, force: bool = False) -> bool:
        c = self.config
        if not c.auto_discover_kef_ip_blind:
            return False

        if not self._blind_discovery_lock.acquire(blocking=False):
            self._log_structured(
                "SKIP",
                action="DISCOVER_IP",
                reason=reason,
                cause="blind_discovery_already_running",
                trigger=trigger,
                mono=f"{self.mono():.3f}",
            )
            return False

        try:
            now = self.mono()
            elapsed = now - self._last_blind_discovery_mono
            if not force and elapsed < c.blind_discovery_cooldown:
                self._log_structured(
                    "SKIP",
                    action="DISCOVER_IP",
                    reason=reason,
                    cause="blind_discovery_cooldown",
                    trigger=trigger,
                    cooldown_s=f"{c.blind_discovery_cooldown:.1f}",
                    elapsed_s=f"{elapsed:.1f}",
                    mono=f"{now:.3f}",
                )
                return False

            self._last_blind_discovery_mono = now
            seed_ip = self.get_current_kef_ip()
            known_mac = self.get_effective_target_mac()
            self._log_structured(
                "BEGIN",
                action="BLIND_DISCOVER_IP",
                reason=reason,
                trigger=trigger,
                seed_ip=seed_ip or "<empty>",
                known_mac=known_mac or "<empty>",
                mono=f"{now:.3f}",
            )

            device_info = discover_kef_device_blind(known_mac, seed_ip, c, self.log)
            if not device_info:
                self._log_structured(
                    "END",
                    action="BLIND_DISCOVER_IP",
                    reason=reason,
                    trigger=trigger,
                    outcome="not_found",
                    mono=f"{self.mono():.3f}",
                )
                return False

            ip_changed = self.update_kef_ip(device_info.ip, source=trigger)
            self.update_identity_from_device_info(device_info, source=trigger)
            self._log_structured(
                "END",
                action="BLIND_DISCOVER_IP",
                reason=reason,
                trigger=trigger,
                outcome="ip_updated" if ip_changed else "ip_confirmed",
                ip=device_info.ip,
                mac=device_info.mac_display or device_info.mac or "",
                speaker_model=device_info.speaker_model or "",
                speaker_name=device_info.speaker_name or "",
                mono=f"{self.mono():.3f}",
            )
            return True
        finally:
            self._blind_discovery_lock.release()

    def maybe_refresh_kef_ip(self, reason: str, trigger: str, force: bool = False) -> bool:
        refreshed = self.maybe_refresh_kef_ip_by_mac(reason=reason, trigger=trigger, force=force)
        if refreshed:
            return True
        return self.maybe_refresh_kef_ip_by_blind(reason=reason, trigger=trigger, force=force)
