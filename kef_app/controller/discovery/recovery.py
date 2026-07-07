from __future__ import annotations

from typing import Callable

from ...devices.scan import discover_ip_by_mac, discover_kef_device_blind, discover_kef_devices
from ...devices.speaker_models import SpeakerIdentity
from ...platform.windows import has_best_route_to_ipv4


class ControllerDiscoveryRecoveryMixin:
    def scan_kef_devices(
        self,
        on_candidate: Callable[[SpeakerIdentity], None] | None = None,
        should_continue: Callable[[], bool] | None = None,
    ) -> list[SpeakerIdentity]:
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
            devices = discover_kef_devices(
                seed_ip,
                self.config,
                self.log,
                on_candidate=on_candidate,
                should_continue=should_continue,
            )
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
                mode="arp_cache",
            )
            discovered_ip = discover_ip_by_mac(target_mac, seed_ip, c, self.log)
            if not discovered_ip:
                end_mono = self.mono()
                self._log_action_sleep_crossing("DISCOVER_IP", None, reason, now, end_mono)
                self._log_structured(
                    "END",
                    action="DISCOVER_IP",
                    reason=reason,
                    trigger=trigger,
                    outcome="not_found",
                    duration_ms=int((end_mono - now) * 1000),
                    mono=f"{end_mono:.3f}",
                )
                return False

            changed = self.update_kef_ip(discovered_ip, source=trigger)
            end_mono = self.mono()
            self._log_action_sleep_crossing("DISCOVER_IP", None, reason, now, end_mono)
            self._log_structured(
                "END",
                action="DISCOVER_IP",
                reason=reason,
                trigger=trigger,
                outcome="ip_updated" if changed else "ip_confirmed",
                ip=discovered_ip,
                duration_ms=int((end_mono - now) * 1000),
                mono=f"{end_mono:.3f}",
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

            device_info = discover_kef_device_blind(
                known_mac,
                seed_ip,
                c,
                self.log,
                # Recovery sweeps are pointless once the session is tearing
                # down; let them unwind instead of finishing the whole subnet.
                should_continue=lambda: not self._is_session_ending(),
            )
            if not device_info:
                end_mono = self.mono()
                self._log_action_sleep_crossing("BLIND_DISCOVER_IP", None, reason, now, end_mono)
                self._log_structured(
                    "END",
                    action="BLIND_DISCOVER_IP",
                    reason=reason,
                    trigger=trigger,
                    outcome="not_found",
                    duration_ms=int((end_mono - now) * 1000),
                    mono=f"{end_mono:.3f}",
                )
                return False

            ip_changed = self.update_kef_ip(device_info.ip, source=trigger)
            self.update_identity_from_device_info(device_info, source=trigger)
            end_mono = self.mono()
            self._log_action_sleep_crossing("BLIND_DISCOVER_IP", None, reason, now, end_mono)
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
                duration_ms=int((end_mono - now) * 1000),
                mono=f"{end_mono:.3f}",
            )
            return True
        finally:
            self._blind_discovery_lock.release()

    def maybe_refresh_kef_ip(self, reason: str, trigger: str, force: bool = False) -> bool:
        target_ip = self.get_current_kef_ip()
        if target_ip and has_best_route_to_ipv4(target_ip) is False:
            self._log_structured(
                "SKIP",
                action="DISCOVER_IP",
                reason=reason,
                trigger=trigger,
                cause="no_local_route",
                target_ip=target_ip,
                mono=f"{self.mono():.3f}",
            )
            return False

        refreshed = self.maybe_refresh_kef_ip_by_mac(reason=reason, trigger=trigger, force=force)
        if refreshed:
            return True
        return self.maybe_refresh_kef_ip_by_blind(reason=reason, trigger=trigger, force=force)
