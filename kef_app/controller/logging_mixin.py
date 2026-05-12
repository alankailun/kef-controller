from __future__ import annotations

import logging
import time
from typing import Optional

_SLEEP_CROSSING_MIN_DURATION_S = 5.0


def _get_pykefcontrol_version() -> str:
    try:
        import pykefcontrol

        version = getattr(pykefcontrol, "__version__", "")
        if version:
            return str(version)
    except Exception:
        pass

    try:
        from importlib.metadata import version

        return version("pykefcontrol")
    except Exception:
        return "<unknown>"


class ControllerLoggingMixin:
    def mono(self) -> float:
        return time.monotonic()

    def _is_diagnostic_logging_enabled(self) -> bool:
        return bool(getattr(self.config, "diagnostic_logging", False))

    @staticmethod
    def _coerce_log_level(level: object) -> Optional[int]:
        if level is None:
            return None
        if isinstance(level, int):
            return level
        normalized = str(level).strip().upper()
        return getattr(logging, normalized, None)

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
            if (
                action in {"EARLY_STANDBY", "STANDBY", "ENDSESSION_STANDBY"}
                and step == "shutdown_request"
                and str(fields.get("status") or "") == "sent"
            ):
                return logging.INFO
            return logging.DEBUG
        return logging.INFO

    def _log_structured(self, tag: str, *, log_level: object = None, **fields):
        log_level_value = self._coerce_log_level(log_level) or self._get_structured_log_level(tag, fields)
        if not self.log.isEnabledFor(log_level_value):
            return

        parts = []
        for key, value in fields.items():
            if value is None:
                continue
            parts.append(f"{key}={value}")
        if parts:
            self.log.log(log_level_value, f"{tag} " + " | ".join(parts))
        else:
            self.log.log(log_level_value, tag)

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
        self._log_action_sleep_crossing(action, generation, reason, start_mono, end_mono)
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

    def _log_action_sleep_crossing(
        self,
        action: str,
        generation: int | None,
        reason: str,
        start_mono: float,
        end_mono: float,
    ) -> None:
        duration_s = end_mono - start_mono
        if duration_s < _SLEEP_CROSSING_MIN_DURATION_S:
            return

        with self._state_lock:
            sleep_pending = bool(self._system_sleep_pending)
            suspend_mono = float(self._last_system_suspend_mono or 0.0)
            resume_mono = float(self._last_system_resume_mono or 0.0)

        suspend_inside_action = start_mono <= suspend_mono <= end_mono
        started_after_pending_suspend = sleep_pending and suspend_mono > 0 and suspend_mono <= start_mono
        resume_inside_action = start_mono <= resume_mono <= end_mono
        if not (suspend_inside_action or started_after_pending_suspend or resume_inside_action):
            return

        self._log_structured(
            "STEP",
            log_level="info",
            action=action,
            gen=generation,
            reason=reason,
            step="system_sleep_crossed",
            action_crossed_system_sleep=True,
            resumed_while_action_pending=True,
            sleep_pending=sleep_pending,
            duration_ms=int(duration_s * 1000),
            suspend_mono=f"{suspend_mono:.3f}" if suspend_mono else "<unknown>",
            resume_mono=f"{resume_mono:.3f}" if resume_mono else "<pending>",
            mono=f"{end_mono:.3f}",
        )

    def _persist_runtime_state(self, source: str) -> bool:
        if self._state_store is None:
            return False
        identity = self.get_current_identity()
        identity.matched_by = self._last_matched_by or identity.matched_by
        return self._state_store.save(identity, source=source)

    def log_banner(self):
        c = self.config
        pykefcontrol_version = _get_pykefcontrol_version()

        self.log.info("=" * 64)
        self.log.info(
            f"  {c.speaker_model_label} power controller | "
            f"backend={c.backend_name} / pykefcontrol={pykefcontrol_version}"
        )
        self.log.info(
            "  Speaker target: "
            f"ip={self.get_current_kef_ip() or c.kef_ip or '<empty>'} | "
            f"target_mac={c.kef_mac or self.get_target_kef_mac() or '<empty>'}"
        )
        self.log.info(
            "  Startup / wake: "
            f"wake_on_startup={c.wake_on_startup} | startup_delay={c.startup_delay}s | "
            f"resume_delay={c.resume_wake_delay}s | unlock_delay={c.unlock_wake_delay}s"
        )
        self.log.info(
            "  Polling / logging: "
            f"home_poll={c.home_external_poll_interval}s | tray_poll={c.tray_identity_poll_interval}s | "
            f"home_event_poll={c.home_event_poll_enabled} | event_timeout={c.home_event_poll_timeout}s | "
            f"event_reconcile={c.home_event_reconcile_interval}s | "
            f"event_recovery_failures={c.speaker_event_recovery_failure_threshold} | "
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
                f"enabled | configured_mac={c.kef_mac or '<empty>'} | "
                f"current_mac={self.get_target_kef_mac() or '<empty>'} | "
                f"subnet_prefix=/{c.mac_discovery_subnet_prefix} | extra_cidrs={c.mac_discovery_extra_cidrs}"
            )
            self.log.info(
                "  Full network scan: "
                f"target-mac-only | http_timeout={c.blind_discovery_http_timeout:.2f}s | "
                f"cooldown={c.blind_discovery_cooldown:.1f}s | workers={c.blind_discovery_max_workers}"
            )
            self.log.info(f"  Wake only after unlock: {c.wake_on_unlock_only}")
            self.log.info(f"  Reachability wait timeout: {c.reachability_wait_timeout}s")
            self.log.info(f"  Socket timeout: {c.socket_timeout}s")
            self.log.info(f"  Wake retry delays: {c.wake_attempt_delays}")
            self.log.info(f"  Standby action-lock timeout: {c.suspend_action_lock_timeout}s")
            self.log.info(f"  Wake action-lock timeout: {c.wake_action_lock_timeout}s")
            self.log.info(f"  Standby when Windows sleeps: {c.standby_on_sleep}")
            self.log.info(
                "  Fast standby on sleep broadcast: "
                f"{c.suspend_fast_standby_enabled} | "
                f"lock_timeout={c.suspend_fast_standby_action_lock_timeout}s | "
                f"socket_timeout={c.suspend_fast_standby_socket_timeout}s"
            )
            self.log.info(
                "  Standby on screen lock: "
                f"{c.standby_on_lock} | lock_timeout={c.lock_standby_action_lock_timeout}s | "
                f"dedupe_window={c.lock_standby_dedup_window}s"
            )
            self.log.info(
                "  Early standby triggers: "
                f"user_inactive={c.standby_on_user_inactive} | "
                f"display_off={c.standby_on_display_off} | "
                f"lid_close={c.standby_on_lid_close}"
            )
            self.log.info(f"  Standby during shutdown/sign-out: {c.endsession_standby_on_shutdown}")
            self.log.info(f"  Log retention days: {c.log_backup_days}")
            self.log.info(f"  Runtime state persistence: {c.persist_runtime_state}")
            self.log.info(f"  Fast exit during end-session: {c.fast_exit_on_endsession}")
        self.log.info("=" * 64)

    def log_power_event(self, name: str, wparam: int, lparam: int):
        event_mono = self.mono()
        with self._state_lock:
            if name == "PBT_APMSUSPEND":
                self._system_sleep_pending = True
                self._last_system_suspend_mono = event_mono
            elif name in {"PBT_APMRESUMESUSPEND", "PBT_APMRESUMEAUTOMATIC"}:
                self._last_system_resume_mono = event_mono
                self._system_sleep_pending = False

        self._log_structured(
            "EVENT",
            kind="POWER",
            name=name,
            wparam=f"0x{wparam:04X}",
            lparam=f"0x{lparam:016X}",
            mono=f"{event_mono:.3f}",
        )

    def log_power_setting_event(self, change, wparam: int, lparam: int):
        self._log_structured(
            "EVENT",
            kind="POWER_SETTING",
            name=change.name,
            guid=change.guid,
            value=change.value if change.value is not None else "<raw>",
            label=change.label,
            data_hex=change.data_hex,
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
