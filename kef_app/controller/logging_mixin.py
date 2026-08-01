from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Optional

from ..structured_logging import coerce_log_level, format_structured_message, structured_log_level

_SLEEP_CROSSING_MIN_DURATION_S = 5.0
_NETWORK_INTERFACE_DEDUP_WINDOW_S = 0.2
_NETWORK_INTERFACE_DEDUP_FLUSH_GRACE_S = 0.05


@dataclass(frozen=True, slots=True)
class BoundStructuredLogger:
    """Immutable structured-log context for one action or operation."""

    owner: object
    fields: Mapping[str, object]

    def write(self, tag: str, *, log_level: object = None, mono: object = None, **fields: object) -> None:
        overlap = self.fields.keys() & fields.keys()
        if overlap:
            names = ", ".join(sorted(overlap))
            # Logging must never stop a power action. Keep the bound context
            # authoritative and surface the programmer error separately.
            self.owner._log_structured(
                "WARN",
                action="STRUCTURED_LOGGING",
                cause="bound_fields_conflict",
                conflicting_fields=names,
            )
        payload = dict(self.fields)
        payload.update((key, value) for key, value in fields.items() if key not in self.fields)
        self.owner._log_structured(tag, log_level=log_level, mono=mono, **payload)


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
    """Controller-side structured logging with a stable envelope.

    Core envelope fields are intentionally small and have fixed meanings:

    * ``action`` is the stable operation identifier (for example ``WAKE``).
    * ``gen`` is that power action's generation, when the operation has one.
    * ``reason`` is the external initiating event (``startup``, ``web_ui``,
      or a Windows event name).
    * ``trigger`` is the specific caller or observation path under that
      initiating event.  New code must use ``trigger`` rather than ``source``.

    Result fields retain their existing, non-interchangeable roles: ``status``
    describes a step's observed result, ``cause`` explains a skipped, retried,
    warned, or aborted branch, and ``outcome`` is reserved for an action's END
    record.  Operation-specific payload fields remain free-form by design.
    """
    def mono(self) -> float:
        return time.monotonic()

    @staticmethod
    def _coerce_log_level(level: object) -> Optional[int]:
        if level is None:
            return None
        return coerce_log_level(level)

    def _get_structured_log_level(self, tag: str, fields: dict[str, object]) -> int:
        return structured_log_level(tag)

    def _log_structured(self, tag: str, *, log_level: object = None, mono: object = None, **fields):
        self._write_structured_log(tag, log_level=log_level, mono=mono, **fields)

    def _bind_log(self, **fields: object) -> BoundStructuredLogger:
        return BoundStructuredLogger(self, MappingProxyType(dict(fields)))

    def _action_log(self, action: str, generation: int | None, reason: str) -> BoundStructuredLogger:
        return self._bind_log(action=action, gen=generation, reason=reason)

    def _write_structured_log(self, tag: str, *, log_level: object = None, mono: object = None, **fields):
        log_level_value = self._coerce_log_level(log_level) or self._get_structured_log_level(tag, fields)
        if not self.log.isEnabledFor(log_level_value):
            return

        if mono is not None:
            fields["mono"] = mono
        elif "mono" not in fields:
            fields["mono"] = f"{self.mono():.3f}"

        self.log.log(log_level_value, format_structured_message(tag, fields))

    def _log_action_begin(self, action: str, generation: int | None, reason: str) -> float:
        start_mono = self.mono()
        self._action_log(action, generation, reason).write("BEGIN", mono=f"{start_mono:.3f}")
        return start_mono

    def _log_action_end(self, action: str, generation: int | None, reason: str, outcome: str, start_mono: float):
        end_mono = self.mono()
        self._log_action_sleep_crossing(action, generation, reason, start_mono, end_mono)
        self._action_log(action, generation, reason).write(
            "END",
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
            sleep_pending = bool(self._windows_events.system_sleep_pending)
            suspend_mono = float(self._windows_events.last_system_suspend_mono or 0.0)
            resume_mono = float(self._windows_events.last_system_resume_mono or 0.0)

        suspend_inside_action = start_mono <= suspend_mono <= end_mono
        started_after_pending_suspend = sleep_pending and suspend_mono > 0 and suspend_mono <= start_mono
        resume_inside_action = start_mono <= resume_mono <= end_mono
        if not (suspend_inside_action or started_after_pending_suspend or resume_inside_action):
            return

        self._action_log(action, generation, reason).write(
            "STEP",
            step="system_sleep_crossed",
            action_crossed_system_sleep=True,
            resumed_while_action_pending=True,
            sleep_pending=sleep_pending,
            duration_ms=int(duration_s * 1000),
            suspend_mono=f"{suspend_mono:.3f}" if suspend_mono else "<unknown>",
            resume_mono=f"{resume_mono:.3f}" if resume_mono else "<pending>",
            mono=f"{end_mono:.3f}",
        )

    def _persist_runtime_state(self, trigger: str) -> bool:
        if self._state_store is None:
            return False
        identity = self.get_current_identity()
        identity.matched_by = self._identity.last_matched_by or identity.matched_by
        return self._state_store.save(identity, trigger=trigger)

    def log_banner(self):
        c = self.config
        pykefcontrol_version = _get_pykefcontrol_version()

        self._log_structured(
            "EVENT",
            action="PROCESS",
            reason="startup",
            name="CONTROLLER_BANNER",
            speaker_model=c.speaker_model_label,
            backend=c.backend_name,
            pykefcontrol=pykefcontrol_version,
            target_ip=self.get_current_kef_ip() or c.kef_ip or "<empty>",
            target_mac=c.kef_mac or self.get_target_kef_mac() or "<empty>",
            wake_on_startup=c.wake_on_startup,
            startup_delay_s=c.startup_delay,
            resume_delay_s=c.resume_wake_delay,
            unlock_delay_s=c.unlock_wake_delay,
            home_poll_s=c.home_external_poll_interval,
            tray_poll_s=c.tray_identity_poll_interval,
            home_event_poll=c.home_event_poll_enabled,
            event_timeout_s=c.home_event_poll_timeout,
            event_reconcile_s=c.home_event_reconcile_interval,
            event_recovery_failures=c.speaker_event_recovery_failure_threshold,
            offline_threshold=c.identity_probe_failure_threshold,
            prewarmed_enabled=c.prewarmed_standby_enabled,
            prewarmed_interval_s=c.prewarmed_keepalive_interval_s,
            prewarmed_deadline_s=c.prewarmed_send_deadline_s,
            prewarmed_persist_socket=c.prewarmed_persist_socket,
            config_file=c.config_file,
            state_file=c.state_file,
            log_file=c.log_file,
        )

    def log_power_event(self, name: str, wparam: int, lparam: int, *, event_mono: float | None = None):
        event_mono = self.mono() if event_mono is None else event_mono
        self._record_power_event_state(name, event_mono)

        self._log_structured(
            "EVENT",
            action="WINDOW_POWER_EVENT",
            kind="POWER",
            name=name,
            wparam=f"0x{wparam:04X}",
            lparam=f"0x{lparam:016X}",
            mono=f"{event_mono:.3f}",
        )
        return event_mono

    def _record_power_event_state(self, name: str, event_mono: float) -> None:
        with self._state_lock:
            self._windows_events.last_event_name = name
            self._windows_events.last_event_mono = event_mono
            if name == "PBT_APMSUSPEND":
                self._windows_events.system_sleep_pending = True
                self._windows_events.last_system_suspend_mono = event_mono
            elif name in {"PBT_APMRESUMESUSPEND", "PBT_APMRESUMEAUTOMATIC"}:
                self._windows_events.last_system_resume_mono = event_mono
                self._windows_events.system_sleep_pending = False

    def log_power_setting_event(self, change, wparam: int, lparam: int, *, event_mono: float | None = None):
        event_mono = self.mono() if event_mono is None else event_mono
        self._record_power_setting_event_state(change, event_mono)
        self._log_power_setting_event_line(change, wparam, lparam, event_mono)
        return event_mono

    def _record_power_setting_event_state(self, change, event_mono: float) -> None:
        with self._state_lock:
            self._windows_events.last_event_name = change.name
            self._windows_events.last_event_mono = event_mono

    def _log_power_setting_event_line(self, change, wparam: int, lparam: int, event_mono: float) -> None:
        self._log_structured(
            "EVENT",
            action="WINDOW_POWER_EVENT",
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
            self._windows_events.last_event_name = name
            self._windows_events.last_event_mono = event_mono
            if name == "WTS_SESSION_LOCK":
                self._power.session_locked = True
            elif name == "WTS_SESSION_UNLOCK":
                self._power.session_locked = False

    def _log_session_event_line(self, name: str, wparam: int, lparam: int, event_mono: float) -> None:
        self._log_structured(
            "EVENT",
            action="WINDOW_SESSION_EVENT",
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
            action="NETWORK_INTERFACE_EVENT",
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
            existing = self._windows_events.network_interface_dedup.get(key)
            if existing is not None and event_mono - float(existing["first_mono"]) <= _NETWORK_INTERFACE_DEDUP_WINDOW_S:
                existing["last_mono"] = event_mono
                existing["repeats"] = int(existing["repeats"]) + 1
                families = existing.get("families")
                if isinstance(families, set):
                    families.add(str(family))
                self._ensure_network_interface_dedup_timer_locked(event_mono)
                return True

            expired_summary = existing
            self._windows_events.network_interface_dedup[key] = summary
            self._ensure_network_interface_dedup_timer_locked(event_mono)

        if expired_summary is not None:
            self._log_network_interface_dedup_summary(expired_summary)

        return False

    def _ensure_network_interface_dedup_timer_locked(self, event_mono: float) -> None:
        if self._windows_events.network_interface_dedup_timer is not None:
            return
        due_mono = event_mono + _NETWORK_INTERFACE_DEDUP_WINDOW_S + _NETWORK_INTERFACE_DEDUP_FLUSH_GRACE_S
        delay_s = max(0.01, due_mono - self.mono())
        timer = threading.Timer(delay_s, self._flush_network_interface_dedup_due)
        timer.daemon = True
        self._windows_events.network_interface_dedup_timer = timer
        timer.start()

    def _flush_network_interface_dedup_due(self, now_mono: float | None = None) -> None:
        now_mono = self.mono() if now_mono is None else now_mono
        due_summaries: list[dict[str, object]] = []
        with self._state_lock:
            self._windows_events.network_interface_dedup_timer = None
            next_due_mono: float | None = None
            for key, summary in list(self._windows_events.network_interface_dedup.items()):
                first_mono = float(summary.get("first_mono") or 0.0)
                due_mono = first_mono + _NETWORK_INTERFACE_DEDUP_WINDOW_S + _NETWORK_INTERFACE_DEDUP_FLUSH_GRACE_S
                if now_mono >= due_mono:
                    self._windows_events.network_interface_dedup.pop(key, None)
                    due_summaries.append(summary)
                    continue
                if next_due_mono is None or due_mono < next_due_mono:
                    next_due_mono = due_mono

            if next_due_mono is not None:
                delay_s = max(0.01, next_due_mono - now_mono)
                timer = threading.Timer(delay_s, self._flush_network_interface_dedup_due)
                timer.daemon = True
                self._windows_events.network_interface_dedup_timer = timer
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
            action="NETWORK_INTERFACE_EVENT",
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
        )
