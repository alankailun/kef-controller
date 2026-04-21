from __future__ import annotations

import logging
import time


class ControllerLoggingMixin:
    _INFO_STEP_ACTIONS = frozenset({"CHANGE_INPUT"})
    _INFO_STEP_PAIRS = frozenset(
        {
            ("DISCOVER_IP", "update_current_ip"),
            ("DISCOVER_IP", "update_target_mac"),
            ("DISCOVER_IP", "update_speaker_name"),
            ("DISCOVER_IP", "update_speaker_model"),
            ("DISCOVER_IP", "update_firmware_version"),
            ("IDENTITY_PROBE", "mark_available"),
        }
    )

    def mono(self) -> float:
        return time.monotonic()

    def _is_diagnostic_logging_enabled(self) -> bool:
        return bool(getattr(self.config, "diagnostic_logging", False))

    def _get_structured_log_level(self, tag: str, fields: dict[str, object]) -> int:
        if tag in {"WARN", "RETRY", "ABORT"}:
            return logging.WARNING
        if tag in {"BEGIN", "END", "EVENT", "STATE"}:
            return logging.INFO
        if self._is_diagnostic_logging_enabled():
            return logging.INFO
        if tag == "SKIP":
            return logging.DEBUG
        if tag == "STEP":
            action = str(fields.get("action") or "")
            step = str(fields.get("step") or "")
            if action in self._INFO_STEP_ACTIONS or (action, step) in self._INFO_STEP_PAIRS:
                return logging.INFO
            return logging.DEBUG
        return logging.INFO

    def _log_structured(self, tag: str, **fields):
        parts = []
        for key, value in fields.items():
            if value is None:
                continue
            parts.append(f"{key}={value}")
        level = self._get_structured_log_level(tag, fields)
        if parts:
            self.log.log(level, f"{tag} " + " | ".join(parts))
        else:
            self.log.log(level, tag)

    def _log_separator(self):
        if self._is_diagnostic_logging_enabled():
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
        self.log.info(f"  {c.speaker_model_label} power controller | backend={c.backend_name} / pykefcontrol")
        self.log.info(
            "  Speaker target: "
            f"ip={self.get_current_kef_ip() or c.kef_ip or '<empty>'} | "
            f"expected_name={c.expected_speaker_name or '<empty>'} | "
            f"expected_mac={configured_expected_mac or '<empty>'}"
        )
        self.log.info(
            "  Startup / wake: "
            f"wake_on_startup={c.wake_on_startup} | startup_delay={c.startup_delay}s | "
            f"resume_delay={c.resume_wake_delay}s | unlock_delay={c.unlock_wake_delay}s"
        )
        self.log.info(
            "  Polling / logging: "
            f"home_poll={c.home_external_poll_interval}s | tray_poll={c.tray_identity_poll_interval}s | "
            f"offline_threshold={c.identity_probe_failure_threshold} | diagnostic_logging={c.diagnostic_logging}"
        )
        self.log.info(
            "  Files: "
            f"config={c.config_file} | state={c.state_file} | log={c.log_file}"
        )
        if self._is_diagnostic_logging_enabled():
            self.log.info(f"  Last remembered IP: {self._loaded_state.last_ip or '<empty>'}")
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
            self.log.info(f"  Log retention days: {c.log_backup_days}")
            self.log.info(f"  Runtime state persistence: {c.persist_runtime_state}")
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
