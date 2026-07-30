from __future__ import annotations

import logging
import threading
import time
from typing import Optional

_SLEEP_CROSSING_MIN_DURATION_S = 5.0
_NETWORK_INTERFACE_DEDUP_WINDOW_S = 0.2
_NETWORK_INTERFACE_DEDUP_FLUSH_GRACE_S = 0.05


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

    def _log_structured(self, tag: str, *, log_level: object = None, mono: object = None, **fields):
        self._write_structured_log(tag, log_level=log_level, mono=mono, **fields)

    def _write_structured_log(self, tag: str, *, log_level: object = None, mono: object = None, **fields):
        log_level_value = self._coerce_log_level(log_level) or self._get_structured_log_level(tag, fields)
        if not self.log.isEnabledFor(log_level_value):
            return

        if mono is not None:
            fields["mono"] = mono
        elif "mono" not in fields:
            fields["mono"] = f"{self.mono():.3f}"

        parts = []
        for key, value in fields.items():
            if value is None:
                continue
            parts.append(f"{key}={value}")
        if parts:
            self.log.log(log_level_value, f"{tag} " + " | ".join(parts))
        else:
            self.log.log(log_level_value, tag)

    def _log_action_begin(self, action: str, generation: int | None, reason: str) -> float:
        start_mono = self.mono()
        self._log_structured("BEGIN", action=action, gen=generation, reason=reason, mono=f"{start_mono:.3f}")
        return start_mono

    def _log_action_end(self, action: str, generation: int | None, reason: str, outcome: str, start_mono: float):
        end_mono = self.mono()
        self._log_action_sleep_crossing(action, generation, reason, start_mono, end_mono)
        self._log_structured(
            "END",
            action=action,
            gen=generation,
            reason=reason,
            outcome=outcome,
            duration_ms=int((end_mono - start_mono) * 1000),
            mono=f"{end_mono:.3f}",
        )

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
            f"offline_threshold={c.identity_probe_failure_threshold}"
        )
        self.log.info(
            "  Standby prewarm: "
            f"enabled={c.prewarmed_standby_enabled} | interval={c.prewarmed_keepalive_interval_s}s | "
            f"deadline={c.prewarmed_send_deadline_s}s | persist_socket={c.prewarmed_persist_socket}"
        )
        self.log.info(
            "  Files: "
            f"config={c.config_file} | state={c.state_file} | log={c.log_file}"
        )
        self.log.info("=" * 64)

    def log_power_event(self, name: str, wparam: int, lparam: int, *, event_mono: float | None = None):
        event_mono = self.mono() if event_mono is None else event_mono
        self._record_power_event_state(name, event_mono)

        self._log_structured(
            "EVENT",
            kind="POWER",
            name=name,
            wparam=f"0x{wparam:04X}",
            lparam=f"0x{lparam:016X}",
            mono=f"{event_mono:.3f}",
        )
        return event_mono

    def _record_power_event_state(self, name: str, event_mono: float) -> None:
        with self._state_lock:
            self._last_windows_event_name = name
            self._last_windows_event_mono = event_mono
            if name == "PBT_APMSUSPEND":
                self._system_sleep_pending = True
                self._last_system_suspend_mono = event_mono
            elif name in {"PBT_APMRESUMESUSPEND", "PBT_APMRESUMEAUTOMATIC"}:
                self._last_system_resume_mono = event_mono
                self._system_sleep_pending = False

    def log_power_setting_event(self, change, wparam: int, lparam: int, *, event_mono: float | None = None):
        event_mono = self.mono() if event_mono is None else event_mono
        self._record_power_setting_event_state(change, event_mono)
        self._log_power_setting_event_line(change, wparam, lparam, event_mono)
        return event_mono

    def _record_power_setting_event_state(self, change, event_mono: float) -> None:
        with self._state_lock:
            self._last_windows_event_name = change.name
            self._last_windows_event_mono = event_mono

    def _log_power_setting_event_line(self, change, wparam: int, lparam: int, event_mono: float) -> None:
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
            mono=f"{event_mono:.3f}",
        )

    def log_session_event(self, name: str, wparam: int, lparam: int):
        event_mono = self.mono()
        self._record_session_event_state(name, event_mono)
        self._log_session_event_line(name, wparam, lparam, event_mono)

    def _record_session_event_state(self, name: str, event_mono: float) -> None:
        with self._state_lock:
            self._last_windows_event_name = name
            self._last_windows_event_mono = event_mono
            if name == "WTS_SESSION_LOCK":
                self._session_locked = True
            elif name == "WTS_SESSION_UNLOCK":
                self._session_locked = False

    def _log_session_event_line(self, name: str, wparam: int, lparam: int, event_mono: float) -> None:
        self._log_structured(
            "EVENT",
            kind="SESSION",
            name=name,
            wparam=f"0x{wparam:04X}",
            session=lparam,
            mono=f"{event_mono:.3f}",
        )

    def log_network_interface_event(self, event):
        event_mono = self.mono()
        notification = getattr(event, "notification", "<unknown>")
        interface = getattr(event, "interface_alias", "<unknown>")
        interface_index = getattr(event, "interface_index", "<unknown>")
        connected = getattr(event, "connected", None)
        if_state = "up" if connected is True else "down" if connected is False else "<unknown>"
        family = getattr(event, "family", "<unknown>")
        metric = getattr(event, "metric", "<unknown>")
        nl_mtu = getattr(event, "nl_mtu", "<unknown>")
        target_ip = self.get_current_kef_ip() or "<empty>"
        if self._should_suppress_network_interface_event(
            notification=notification,
            interface=interface,
            interface_index=interface_index,
            if_state=if_state,
            family=family,
            metric=metric,
            nl_mtu=nl_mtu,
            target_ip=target_ip,
            event_mono=event_mono,
        ):
            return

        self._log_structured(
            "EVENT",
            log_level="info",
            kind="NETWORK",
            name="INTERFACE_CHANGE",
            notification=notification,
            interface=interface,
            interface_index=interface_index,
            if_state=if_state,
            family=family,
            metric=metric,
            nl_mtu=nl_mtu,
            target_ip=target_ip,
            mono=f"{event_mono:.3f}",
        )

    def _should_suppress_network_interface_event(
        self,
        *,
        notification: object,
        interface: object,
        interface_index: object,
        if_state: str,
        family: object,
        metric: object,
        nl_mtu: object,
        target_ip: str,
        event_mono: float,
    ) -> bool:
        if str(notification) != "ParameterNotification":
            return False

        key = (str(notification), str(interface_index), str(interface), if_state)
        summary: dict[str, object] = {
            "notification": notification,
            "interface": interface,
            "interface_index": interface_index,
            "if_state": if_state,
            "families": {str(family)},
            "metric": metric,
            "nl_mtu": nl_mtu,
            "target_ip": target_ip,
            "first_mono": event_mono,
            "last_mono": event_mono,
            "repeats": 0,
        }

        expired_summary = None
        with self._state_lock:
            existing = self._network_interface_dedup.get(key)
            if existing is not None and event_mono - float(existing["first_mono"]) <= _NETWORK_INTERFACE_DEDUP_WINDOW_S:
                existing["last_mono"] = event_mono
                existing["repeats"] = int(existing["repeats"]) + 1
                families = existing.get("families")
                if isinstance(families, set):
                    families.add(str(family))
                self._ensure_network_interface_dedup_timer_locked(event_mono)
                return True

            expired_summary = existing
            self._network_interface_dedup[key] = summary
            self._ensure_network_interface_dedup_timer_locked(event_mono)

        if expired_summary is not None:
            self._log_network_interface_dedup_summary(expired_summary)

        return False

    def _ensure_network_interface_dedup_timer_locked(self, event_mono: float) -> None:
        if self._network_interface_dedup_timer is not None:
            return
        due_mono = event_mono + _NETWORK_INTERFACE_DEDUP_WINDOW_S + _NETWORK_INTERFACE_DEDUP_FLUSH_GRACE_S
        delay_s = max(0.01, due_mono - self.mono())
        timer = threading.Timer(delay_s, self._flush_network_interface_dedup_due)
        timer.daemon = True
        self._network_interface_dedup_timer = timer
        timer.start()

    def _flush_network_interface_dedup_due(self, now_mono: float | None = None) -> None:
        now_mono = self.mono() if now_mono is None else now_mono
        due_summaries: list[dict[str, object]] = []
        with self._state_lock:
            self._network_interface_dedup_timer = None
            next_due_mono: float | None = None
            for key, summary in list(self._network_interface_dedup.items()):
                first_mono = float(summary.get("first_mono") or 0.0)
                due_mono = first_mono + _NETWORK_INTERFACE_DEDUP_WINDOW_S + _NETWORK_INTERFACE_DEDUP_FLUSH_GRACE_S
                if now_mono >= due_mono:
                    self._network_interface_dedup.pop(key, None)
                    due_summaries.append(summary)
                    continue
                if next_due_mono is None or due_mono < next_due_mono:
                    next_due_mono = due_mono

            if next_due_mono is not None:
                delay_s = max(0.01, next_due_mono - now_mono)
                timer = threading.Timer(delay_s, self._flush_network_interface_dedup_due)
                timer.daemon = True
                self._network_interface_dedup_timer = timer
                timer.start()

        for summary in due_summaries:
            self._log_network_interface_dedup_summary(summary)

    def _log_network_interface_dedup_summary(self, summary: dict[str, object]) -> None:
        repeats = int(summary.get("repeats") or 0)
        if repeats <= 0:
            return

        first_mono = float(summary.get("first_mono") or 0.0)
        last_mono = float(summary.get("last_mono") or first_mono)
        families = summary.get("families")
        if isinstance(families, set):
            family_text = ",".join(sorted(families))
        else:
            family_text = "<unknown>"

        self._log_structured(
            "EVENT",
            log_level="info",
            kind="NETWORK",
            name="INTERFACE_CHANGE_DEDUP",
            notification=summary.get("notification", "<unknown>"),
            interface=summary.get("interface", "<unknown>"),
            interface_index=summary.get("interface_index", "<unknown>"),
            if_state=summary.get("if_state", "<unknown>"),
            families=family_text,
            repeats=repeats,
            window_ms=int(max(0.0, last_mono - first_mono) * 1000),
            target_ip=summary.get("target_ip", "<empty>"),
            mono=f"{self.mono():.3f}",
        )
