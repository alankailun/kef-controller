from __future__ import annotations

from ...devices.scan.identity import identify_kef_device
from ...devices.scan.network import is_routable_ipv4
from ...devices.speaker_models import SpeakerIdentity
from ..network_timeout import temporary_socket_timeout
from .identity_helpers import (
    can_use_cached_current_target,
    can_use_cached_target_without_probe,
    merge_identity_details,
    missing_identity_fields,
)


class ControllerIdentityProbeMixin:
    def _cached_target_probe_context(self) -> tuple[str, str, str, str, str]:
        target_mac = self.get_effective_target_mac()
        current_ip = self.get_current_kef_ip()
        configured_ip = str(self.config.kef_ip or "").strip()
        with self._ip_lock:
            cached_mac = self._identity.target_mac
            cached_model = self._identity.speaker_model
        return target_mac, current_ip, configured_ip, cached_mac, cached_model

    def _log_backend_identity_partial(
        self,
        *,
        reason: str,
        trigger: str,
        ip: str,
        identity: SpeakerIdentity | None,
        error: str = "",
    ) -> None:
        self._log_structured(
            "STEP",
            action="DISCOVER_IP",
            reason=reason,
            step="backend_identity_partial",
            trigger=trigger,
            current_ip=ip,
            missing_fields=missing_identity_fields(identity),
            actual_mac=(identity.mac_display or identity.mac) if identity else "<empty>",
            speaker_model=identity.speaker_model if identity else "<empty>",
            error=error or None,
        )

    def _log_http_identity_result(
        self,
        *,
        reason: str,
        trigger: str,
        ip: str,
        identity: SpeakerIdentity | None,
    ) -> None:
        self._log_structured(
            "STEP",
            action="DISCOVER_IP",
            reason=reason,
            step="http_identity_succeeded" if identity else "http_identity_failed",
            trigger=trigger,
            current_ip=ip,
            missing_fields=missing_identity_fields(identity),
            actual_mac=(identity.mac_display or identity.mac) if identity else "<empty>",
            speaker_model=identity.speaker_model if identity else "<empty>",
            speaker_name=identity.speaker_name if identity else "<empty>",
        )

    def _can_use_cached_current_target(self, identity: SpeakerIdentity, trigger: str) -> bool:
        target_mac, current_ip, configured_ip, cached_mac, cached_model = self._cached_target_probe_context()

        return can_use_cached_current_target(
            identity=identity,
            trigger=trigger,
            target_mac=target_mac,
            current_ip=current_ip,
            configured_ip=configured_ip,
            cached_mac=cached_mac,
            cached_model=cached_model,
        )

    def _can_use_cached_target_without_probe(self, trigger: str) -> bool:
        target_mac, current_ip, configured_ip, cached_mac, cached_model = self._cached_target_probe_context()

        return can_use_cached_target_without_probe(
            trigger=trigger,
            target_mac=target_mac,
            current_ip=current_ip,
            configured_ip=configured_ip,
            cached_mac=cached_mac,
            cached_model=cached_model,
        )

    def verify_current_target(self, reason: str, trigger: str) -> bool:
        if not self.get_current_kef_ip():
            self._log_structured(
                "SKIP",
                action="VERIFY_TARGET",
                reason=reason,
                trigger=trigger,
                cause="empty_current_ip",
                target_mac=self.get_effective_target_mac() or "<empty>",
            )
            return False

        return self.capture_identity_from_current_ip(reason=reason, trigger=trigger)

    def inspect_kef_identity_at_ip(self, ip: str, reason: str, trigger: str) -> SpeakerIdentity | None:
        ip = str(ip or "").strip()
        if not is_routable_ipv4(ip):
            return None

        info: SpeakerIdentity | None = None
        backend_error = ""
        try:
            with temporary_socket_timeout(self.config.socket_timeout):
                speaker = self._backend.create_connector(ip)
                info = self._backend.capture_identity(speaker, ip)
        except Exception as exc:
            backend_error = repr(exc)
            info = None

        if not info or not info.speaker_model or not info.mac:
            self._log_backend_identity_partial(
                reason=reason,
                trigger=trigger,
                ip=ip,
                identity=info,
                error=backend_error,
            )
            http_info = identify_kef_device(ip, self.config)
            self._log_http_identity_result(reason=reason, trigger=trigger, ip=ip, identity=http_info)
            info = merge_identity_details(info, http_info)

        self._log_structured(
            "STEP" if info else "SKIP",
            action="VALIDATE_TARGET",
            reason=reason,
            trigger=trigger,
            actual_ip=ip,
            status="identity_found" if info else "identity_not_found",
            actual_mac=(info.mac_display or info.mac) if info else "<empty>",
            actual_speaker_model=info.speaker_model if info else "<empty>",
            actual_speaker_name=info.speaker_name if info else "<empty>",
        )
        return info

    def capture_identity_from_current_ip(self, reason: str, trigger: str) -> bool:
        current_ip = self.get_current_kef_ip()
        if not current_ip:
            return False

        if self._can_use_cached_target_without_probe(trigger):
            self._log_structured(
                "STEP",
                action="DISCOVER_IP",
                reason=reason,
                step="use_cached_target_identity",
                trigger=trigger,
                cause="urgent_standby_fast_path",
                current_ip=current_ip,
                target_mac=self.get_effective_target_mac() or "<empty>",
            )
            return True

        info: SpeakerIdentity | None = None
        backend_error = ""
        try:
            with temporary_socket_timeout(self.config.socket_timeout):
                speaker = self.get_speaker(fresh=False)
                info = self._backend.capture_identity(speaker, current_ip)
        except Exception as exc:
            backend_error = repr(exc)
            info = None

        can_use_cached_current_target = bool(info and self._can_use_cached_current_target(info, trigger))
        if can_use_cached_current_target:
            self._log_backend_identity_partial(
                reason=reason,
                trigger=trigger,
                ip=current_ip,
                identity=info,
                error=backend_error,
            )
            self._log_structured(
                "STEP",
                action="DISCOVER_IP",
                reason=reason,
                step="use_cached_target_identity",
                trigger=trigger,
                cause="target_mac_unverified",
                current_ip=current_ip,
                target_mac=self.get_effective_target_mac() or "<empty>",
                speaker_model=info.speaker_model or "<empty>",
            )
        elif not info or not info.speaker_model or not info.mac:
            self._log_backend_identity_partial(
                reason=reason,
                trigger=trigger,
                ip=current_ip,
                identity=info,
                error=backend_error,
            )
            http_info = identify_kef_device(current_ip, self.config)
            self._log_http_identity_result(reason=reason, trigger=trigger, ip=current_ip, identity=http_info)
            info = merge_identity_details(info, http_info)

        if not info:
            self._log_structured(
                "SKIP",
                action="DISCOVER_IP",
                reason=reason,
                cause="identity_probe_failed",
                trigger=trigger,
                current_ip=current_ip,
            )
            return False

        matched, match_reason = self._identity_matches_expectation(info)
        if not matched and match_reason == "target_mac_unverified" and can_use_cached_current_target:
            matched = True
            match_reason = "cached_target_mac_current_ip"
        changed = False
        if matched:
            changed = self.update_identity_from_device_info(info, trigger=trigger)
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
            )
            self.reset_speaker()
            return False
        return True

    def log_current_http_identity_snapshot(self, reason: str, trigger: str) -> bool:
        current_ip = self.get_current_kef_ip()
        started = self.mono()
        if not current_ip:
            self._log_structured(
                "STEP",
                action="DISCOVER_IP",
                reason=reason,
                step="startup_http_identity",
                trigger=trigger,
                status="skipped_empty_current_ip",
                current_ip="<empty>",
                duration_ms=0,
                mono=f"{started:.3f}",
            )
            return False

        info = identify_kef_device(current_ip, self.config)
        matched = False
        match_reason = "identity_not_found"
        if info:
            matched, match_reason = self._identity_matches_expectation(info)

        ended = self.mono()
        self._log_structured(
            "STEP",
            action="DISCOVER_IP",
            reason=reason,
            step="startup_http_identity",
            trigger=trigger,
            status="identity_found" if info else "identity_not_found",
            current_ip=current_ip,
            target_mac=self.get_effective_target_mac() or "<empty>",
            actual_mac=(info.mac_display or info.mac) if info else "<empty>",
            speaker_model=info.speaker_model if info else "<empty>",
            speaker_name=info.speaker_name if info else "<empty>",
            matched=matched,
            match_reason=match_reason,
            duration_ms=int((ended - started) * 1000),
            mono=f"{ended:.3f}",
        )
        return bool(info and matched)

    def probe_external_identity(self, reason: str, trigger: str) -> tuple[bool, bool]:
        identity_seen = self.capture_identity_from_current_ip(reason=reason, trigger=f"{trigger}_identity")
        ip_refreshed = False
        if not identity_seen:
            ip_refreshed = self.maybe_refresh_kef_ip(reason=reason, trigger=f"{trigger}_refresh")
        reachable = identity_seen or ip_refreshed
        if not reachable:
            self.record_identity_probe_failure(reason=reason, trigger=trigger, cause="identity_refresh_failed")

        self._log_structured(
            "STEP",
            action="IDENTITY_PROBE",
            reason=reason,
            trigger=trigger,
            fallback_identity=identity_seen,
            fallback_ip_refresh=ip_refreshed,
            reachable=reachable,
        )
        return identity_seen, ip_refreshed
