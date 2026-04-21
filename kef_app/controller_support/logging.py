from __future__ import annotations

import time


class ControllerLoggingMixin:
    def mono(self) -> float:
        return time.monotonic()

    def _log_structured(self, tag: str, **fields):
        parts = []
        for key, value in fields.items():
            if value is None:
                continue
            parts.append(f"{key}={value}")
        if parts:
            self.log.info(f"{tag} " + " | ".join(parts))
        else:
            self.log.info(tag)

    def _log_separator(self):
        self.log.info("-" * 100)

    def _log_action_begin(self, action: str, generation: int | None, reason: str) -> float:
        start_mono = self.mono()
        self._log_separator()
        self._log_structured("BEGIN", action=action, gen=generation, reason=reason, mono=f"{start_mono:.3f}")
        self._log_separator()
        return start_mono

    def _log_action_end(self, action: str, generation: int | None, reason: str, outcome: str, start_mono: float):
        end_mono = self.mono()
        self._log_separator()
        self._log_structured(
            "END",
            action=action,
            gen=generation,
            reason=reason,
            outcome=outcome,
            duration_ms=int((end_mono - start_mono) * 1000),
            mono=f"{end_mono:.3f}",
        )
        self._log_separator()

    def _persist_runtime_state(self, source: str) -> bool:
        if self._state_store is None:
            return False
        identity = self.get_current_identity()
        identity.matched_by = self._last_matched_by or identity.matched_by
        return self._state_store.save(identity, source=source)

    def log_banner(self):
        c = self.config
        configured_expected_mac = c.expected_speaker_mac or c.kef_mac

        self.log.info("=" * 64)
        self.log.info(f"  {c.speaker_model_label} power controller")
        self.log.info(f"  Backend: {c.backend_name} / pykefcontrol")
        self.log.info(f"  Config IP: {c.kef_ip or '<empty>'}")
        self.log.info(f"  Last remembered IP: {self._loaded_state.last_ip or '<empty>'}")
        self.log.info(f"  Current speaker IP: {self.get_current_kef_ip() or '<empty>'}")
        self.log.info(f"  Expected name: {c.expected_speaker_name or '<empty>'}")
        self.log.info(f"  Expected MAC: {configured_expected_mac or '<empty>'}")
        self.log.info(f"  Last remembered MAC: {self._loaded_state.last_mac or '<empty>'}")
        self.log.info(f"  Current target MAC: {self.get_target_kef_mac() or '<empty>'}")
        self.log.info(f"  Current speaker name: {self._speaker_name or '<empty>'}")
        self.log.info(f"  Current speaker model: {self._speaker_model or '<empty>'}")
        self.log.info(f"  Default input: {c.kef_input}")
        self.log.info(
            "  MAC recovery discovery: "
            f"{c.auto_discover_kef_ip_by_mac} | configured_mac={c.kef_mac or '<empty>'} | "
            f"current_mac={self.get_target_kef_mac() or '<empty>'} | "
            f"subnet_prefix=/{c.mac_discovery_subnet_prefix} | extra_cidrs={c.mac_discovery_extra_cidrs}"
        )
        self.log.info(
            "  Full network scan: "
            f"{c.auto_discover_kef_ip_blind} | http_timeout={c.blind_discovery_http_timeout:.2f}s | "
            f"cooldown={c.blind_discovery_cooldown:.1f}s | workers={c.blind_discovery_max_workers}"
        )
        self.log.info(f"  Wake on app start: {c.wake_on_startup}")
        self.log.info(f"  Startup delay: {c.startup_delay}s")
        self.log.info(f"  Resume wake delay: {c.resume_wake_delay}s")
        self.log.info(f"  Unlock wake delay: {c.unlock_wake_delay}s")
        self.log.info(f"  Wake only after unlock: {c.wake_on_unlock_only}")
        self.log.info(f"  Reachability wait timeout: {c.reachability_wait_timeout}s")
        self.log.info(f"  Socket timeout: {c.socket_timeout}s")
        self.log.info(f"  Standby retry delays: {c.standby_attempt_delays}")
        self.log.info(f"  Wake retry delays: {c.wake_attempt_delays}")
        self.log.info(f"  Standby action-lock timeout: {c.suspend_action_lock_timeout}s")
        self.log.info(f"  Wake action-lock timeout: {c.wake_action_lock_timeout}s")
        self.log.info(f"  Standby when Windows sleeps: {c.standby_on_sleep}")
        self.log.info(
            "  Standby on screen lock: "
            f"{c.standby_on_lock} | lock_timeout={c.lock_standby_action_lock_timeout}s | "
            f"dedupe_window={c.lock_standby_dedup_window}s"
        )
        self.log.info(f"  Standby during shutdown/sign-out: {c.endsession_standby_on_shutdown}")
        self.log.info(f"  User config file: {c.config_file}")
        self.log.info(f"  Log file: {c.log_file} | retention_days={c.log_backup_days}")
        self.log.info(f"  Runtime state persistence: {c.persist_runtime_state} | state_file={c.state_file}")
        self.log.info(f"  Fast exit during end-session: {c.fast_exit_on_endsession}")
        self.log.info(
            f"  Application auto-restart: {c.enable_application_restart} | flags=0x{c.application_restart_flags:02X}"
        )
        self.log.info("=" * 64)

    def log_power_event(self, name: str, wparam: int, lparam: int):
        self._log_structured(
            "EVENT",
            kind="POWER",
            name=name,
            wparam=f"0x{wparam:04X}",
            lparam=f"0x{lparam:016X}",
            mono=f"{self.mono():.3f}",
        )

    def log_session_event(self, name: str, wparam: int, lparam: int):
        self._log_structured(
            "EVENT",
            kind="SESSION",
            name=name,
            wparam=f"0x{wparam:04X}",
            session=lparam,
            mono=f"{self.mono():.3f}",
        )
