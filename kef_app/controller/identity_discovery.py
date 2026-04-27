from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..devices.speaker_discovery import (
    discover_kef_devices,
    discover_ip_by_mac,
    discover_kef_device_blind,
    identify_kef_device,
    is_routable_ipv4,
    normalize_mac,
    probe_ip_port,
)
from ..devices.speaker_models import SpeakerIdentity
from .network_timeout import temporary_socket_timeout


@dataclass(slots=True)
class TargetValidationResult:
    status: str
    requested_ip: str = ""
    requested_mac: str = ""
    identity: SpeakerIdentity = field(default_factory=SpeakerIdentity)


class ControllerDiscoveryMixin:
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

    def scan_kef_devices(self) -> list[SpeakerIdentity]:
        if not self._blind_discovery_lock.acquire(blocking=False):
            self._log_structured(
                "SKIP",
                action="MANUAL_SCAN",
                cause="blind_discovery_already_running",
                mono=f"{self.mono():.3f}",
            )
            return []

        try:
            seed_ip = self.get_current_kef_ip()
            self._log_structured(
                "BEGIN",
                action="MANUAL_SCAN",
                seed_ip=seed_ip or "<empty>",
                mono=f"{self.mono():.3f}",
            )
            devices = discover_kef_devices(seed_ip, self.config, self.log)
            self._log_structured(
                "END",
                action="MANUAL_SCAN",
                outcome="found" if devices else "not_found",
                count=len(devices),
                mono=f"{self.mono():.3f}",
            )
            return devices
        finally:
            self._blind_discovery_lock.release()

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
            if identity.mac != target_mac:
                return False, "target_mac_mismatch"
            return True, "target_mac"
        return True, "no_target_mac"

    def get_target_kef_mac(self) -> str:
        with self._ip_lock:
            return self._target_kef_mac

    def verify_current_target(self, reason: str, trigger: str) -> bool:
        if not self.get_current_kef_ip():
            self._log_structured(
                "SKIP",
                action="VERIFY_TARGET",
                reason=reason,
                trigger=trigger,
                cause="empty_current_ip",
                target_mac=self.get_effective_target_mac() or "<empty>",
                mono=f"{self.mono():.3f}",
            )
            return False

        return self.capture_identity_from_current_ip(reason=reason, trigger=trigger)

    def inspect_kef_identity_at_ip(self, ip: str, reason: str, trigger: str) -> Optional[SpeakerIdentity]:
        ip = str(ip or "").strip()
        if not is_routable_ipv4(ip):
            return None

        info: Optional[SpeakerIdentity] = None
        try:
            with temporary_socket_timeout(self.config.socket_timeout):
                speaker = self._backend.create_connector(ip)
                info = self._backend.capture_identity(speaker, ip)
        except Exception:
            info = None

        if not info or not info.speaker_model:
            info = identify_kef_device(ip, self.config)

        self._log_structured(
            "STEP" if info else "SKIP",
            action="VALIDATE_TARGET",
            reason=reason,
            trigger=trigger,
            ip=ip,
            outcome="identity_found" if info else "identity_not_found",
            mac=(info.mac_display or info.mac) if info else "<empty>",
            speaker_model=info.speaker_model if info else "<empty>",
            speaker_name=info.speaker_name if info else "<empty>",
            mono=f"{self.mono():.3f}",
        )
        return info

    def validate_manual_target(self, ip: str, target_mac: str, reason: str, trigger: str) -> TargetValidationResult:
        requested_ip = str(ip or "").strip()
        requested_mac = normalize_mac(target_mac)

        if requested_ip and not is_routable_ipv4(requested_ip):
            return TargetValidationResult("invalid_ip", requested_ip=requested_ip, requested_mac=requested_mac)
        if target_mac and len(requested_mac) != 12:
            return TargetValidationResult("invalid_mac", requested_ip=requested_ip, requested_mac=requested_mac)
        if not requested_ip and not requested_mac:
            return TargetValidationResult("empty", requested_ip="", requested_mac="")

        if requested_ip:
            info = self.inspect_kef_identity_at_ip(requested_ip, reason=reason, trigger=f"{trigger}_ip")
            if info:
                if requested_mac:
                    if info.mac and info.mac != requested_mac:
                        return TargetValidationResult(
                            "mac_mismatch",
                            requested_ip=requested_ip,
                            requested_mac=requested_mac,
                            identity=info,
                        )
                    if not info.mac:
                        return TargetValidationResult(
                            "mac_unverified",
                            requested_ip=requested_ip,
                            requested_mac=requested_mac,
                            identity=info,
                        )
                return TargetValidationResult(
                    "verified",
                    requested_ip=requested_ip,
                    requested_mac=requested_mac,
                    identity=info,
                )

            if probe_ip_port(requested_ip, self.config.mac_discovery_tcp_port, self.config.mac_discovery_probe_timeout):
                return TargetValidationResult("not_kef", requested_ip=requested_ip, requested_mac=requested_mac)

        if requested_mac:
            recovered = self._recover_identity_for_manual_target(
                requested_mac,
                seed_ip=requested_ip,
                reason=reason,
                trigger=f"{trigger}_recover",
            )
            if recovered:
                return TargetValidationResult(
                    "recovered",
                    requested_ip=requested_ip,
                    requested_mac=requested_mac,
                    identity=recovered,
                )
            return TargetValidationResult("mac_not_found", requested_ip=requested_ip, requested_mac=requested_mac)

        return TargetValidationResult("unreachable", requested_ip=requested_ip, requested_mac=requested_mac)

    def _recover_identity_for_manual_target(
        self,
        target_mac: str,
        seed_ip: str,
        reason: str,
        trigger: str,
    ) -> Optional[SpeakerIdentity]:
        discovered_ip = discover_ip_by_mac(target_mac, seed_ip, self.config, self.log)
        if discovered_ip:
            info = self.inspect_kef_identity_at_ip(discovered_ip, reason=reason, trigger=f"{trigger}_mac_ip")
            if info and info.mac == target_mac:
                return info.with_match("target_mac")

        identity = discover_kef_device_blind(target_mac, seed_ip, self.config, self.log)
        if identity and identity.mac == target_mac:
            return identity

        return None

    def recover_target_ip(self, reason: str, trigger: str, force: bool = False) -> bool:
        recovered = self.maybe_refresh_kef_ip(reason=reason, trigger=trigger, force=force)
        if recovered:
            return self.verify_current_target(reason=reason, trigger=f"{trigger}_verify")
        return False

    def resolve_target(
        self,
        reason: str,
        trigger: str,
        force_recovery: bool = False,
    ) -> bool:
        if self.verify_current_target(reason=reason, trigger=f"{trigger}_verify"):
            return True
        if not self.get_current_kef_ip() and not self.get_effective_target_mac():
            self._log_structured(
                "SKIP",
                action="RESOLVE_TARGET",
                reason=reason,
                trigger=trigger,
                cause="empty_target_requires_manual_selection",
                mono=f"{self.mono():.3f}",
            )
            return False
        return self.recover_target_ip(reason=reason, trigger=f"{trigger}_recover", force=force_recovery)

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
            info = identify_kef_device(current_ip, self.config)

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

        matched, match_reason = self._identity_matches_expectation(info)
        changed = False
        if matched:
            changed = self.update_identity_from_device_info(info, source=trigger)
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
                target_mac=self.get_effective_target_mac() or "<empty>",
                actual_name=info.speaker_name or "<empty>",
                actual_mac=info.mac_display or info.mac or "<empty>",
                mono=f"{self.mono():.3f}",
            )
            self.reset_speaker()
            return False
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

    def maybe_refresh_kef_ip_by_mac(self, reason: str, trigger: str, force: bool = False) -> bool:
        c = self.config
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
            seed_ip = self.get_current_kef_ip()
            known_mac = self.get_effective_target_mac()
            if not known_mac:
                self._log_structured(
                    "SKIP",
                    action="BLIND_DISCOVER_IP",
                    reason=reason,
                    trigger=trigger,
                    cause="empty_target_mac_requires_manual_selection",
                    seed_ip=seed_ip or "<empty>",
                    mono=f"{now:.3f}",
                )
                return False

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
