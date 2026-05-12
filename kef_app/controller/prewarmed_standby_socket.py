from __future__ import annotations

import socket
import threading
from dataclasses import dataclass

from ..devices.transport import build_standby_request_bytes


_PREWARM_RETRY_DELAY_S = 2.0
_PREWARM_POWER_ACTION_DELAY_S = 0.5
_PREWARM_FAILURE_LOG_THRESHOLD = 3
_KEEPALIVE_REQUEST_PATH = "/api/getData?path=settings%3A%2Freleasetext&roles=value"


@dataclass(frozen=True, slots=True)
class PrewarmedStandbySendResult:
    attempted: bool
    success: bool
    status: str
    duration_ms: int = 0
    target_ip: str = ""
    mode: str = ""
    error: str = ""
    frozen_s: str = ""


class PrewarmedSocketHolder:
    def __init__(self, sock: socket.socket, ip: str, created_mono: float):
        self.sock = sock
        self.ip = ip
        self.created_mono = created_mono

    def take(self) -> socket.socket | None:
        sock = self.sock
        self.sock = None
        return sock

    def close(self) -> None:
        sock = self.take()
        if sock is None:
            return
        try:
            sock.close()
        except OSError:
            pass


def _build_keepalive_request_bytes(host: str) -> bytes:
    return (
        f"GET {_KEEPALIVE_REQUEST_PATH} HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        "User-Agent: kef-controller/prewarmed-standby\r\n"
        "Connection: close\r\n"
        "\r\n"
    ).encode("ascii")


def _open_tcp_socket(ip: str, *, port: int, timeout: float) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        sock.connect((ip, port))
        return sock
    except Exception:
        try:
            sock.close()
        except OSError:
            pass
        raise


def _close_socket(sock: socket.socket | None) -> None:
    if sock is None:
        return
    try:
        sock.close()
    except OSError:
        pass


class PrewarmedStandbySocketMonitorMixin:
    def start_prewarmed_standby_socket_monitor(self, reason: str = "runtime") -> bool:
        if not self.config.prewarmed_standby_enabled:
            return False

        with self._prewarmed_standby_lock:
            if self._prewarmed_standby_running:
                if self._prewarmed_standby_stop.is_set():
                    self._prewarmed_standby_restart_reason = reason
                return False
            self._prewarmed_standby_running = True
            self._prewarmed_standby_restart_reason = None
            self._prewarmed_standby_stop.clear()

        thread = threading.Thread(
            target=lambda: self._run_prewarmed_standby_socket_monitor(reason),
            daemon=True,
            name="PrewarmedStandbySocket",
        )
        with self._prewarmed_standby_lock:
            self._prewarmed_standby_thread = thread
        thread.start()
        return True

    def stop_prewarmed_standby_socket_monitor(self) -> None:
        self._prewarmed_standby_stop.set()
        self._close_prewarmed_socket_holder()

    def _finish_prewarmed_standby_socket_monitor(self) -> None:
        with self._prewarmed_standby_lock:
            self._prewarmed_standby_running = False
            self._prewarmed_standby_thread = None
            restart_reason = self._prewarmed_standby_restart_reason
            self._prewarmed_standby_restart_reason = None

        if restart_reason and self.config.prewarmed_standby_enabled:
            self.start_prewarmed_standby_socket_monitor(restart_reason)

    def _close_prewarmed_socket_holder(self) -> None:
        with self._prewarmed_standby_lock:
            holder = self._prewarmed_standby_holder
            self._prewarmed_standby_holder = None
        if holder is not None:
            holder.close()

    def _take_prewarmed_socket_holder(self, target_ip: str) -> socket.socket | None:
        holder = None
        with self._prewarmed_standby_lock:
            holder = self._prewarmed_standby_holder
            if holder is None or holder.ip != target_ip:
                self._prewarmed_standby_holder = None
            else:
                self._prewarmed_standby_holder = None
                return holder.take()

        if holder is not None:
            holder.close()
        return None

    def _run_prewarmed_standby_socket_monitor(self, reason: str) -> None:
        self._log_structured(
            "STEP",
            log_level="info",
            action="PREWARMED_STANDBY_SOCKET",
            reason=reason,
            step="monitor",
            status="started",
            interval_s=f"{self.config.prewarmed_keepalive_interval_s:.1f}",
            timeout_s=f"{self.config.prewarmed_socket_timeout_s:.2f}",
            persist_socket=self.config.prewarmed_persist_socket,
            mono=f"{self.mono():.3f}",
        )
        try:
            while not self._prewarmed_standby_stop.is_set():
                delay = self._prewarmed_standby_tick(reason)
                if self._prewarmed_standby_stop.wait(delay):
                    return
        finally:
            self._close_prewarmed_socket_holder()
            self._finish_prewarmed_standby_socket_monitor()
            self._log_structured(
                "STEP",
                log_level="info",
                action="PREWARMED_STANDBY_SOCKET",
                reason=reason,
                step="monitor",
                status="stopped",
                mono=f"{self.mono():.3f}",
            )

    def _prewarmed_standby_tick(self, reason: str) -> float:
        if not self.config.prewarmed_standby_enabled:
            self._close_prewarmed_socket_holder()
            return _PREWARM_RETRY_DELAY_S
        if self._is_controller_power_action_active():
            return _PREWARM_POWER_ACTION_DELAY_S

        target_ip = self.get_current_kef_ip()
        if not target_ip:
            self._close_prewarmed_socket_holder()
            return _PREWARM_RETRY_DELAY_S

        last_ip = self._prewarmed_standby_last_ip
        if last_ip and last_ip != target_ip:
            self._close_prewarmed_socket_holder()
            with self._prewarmed_standby_lock:
                self._prewarmed_standby_ready_logged = False
                self._prewarmed_standby_last_ok_mono = 0.0

        started = self.mono()
        try:
            if self.config.prewarmed_persist_socket:
                self._ensure_persistent_prewarmed_socket(target_ip)
            self._probe_prewarmed_keepalive(target_ip)
        except OSError as exc:
            self._record_prewarmed_keepalive_failure(reason, target_ip, exc)
            return _PREWARM_RETRY_DELAY_S

        finished = self.mono()
        duration_ms = int(max(0.0, finished - started) * 1000)
        self._record_prewarmed_keepalive_success(reason, target_ip, duration_ms)
        return max(1.0, float(self.config.prewarmed_keepalive_interval_s))

    def _ensure_persistent_prewarmed_socket(self, target_ip: str) -> None:
        holder = None
        with self._prewarmed_standby_lock:
            holder = self._prewarmed_standby_holder
            if holder is not None and holder.ip == target_ip:
                return
            self._prewarmed_standby_holder = None

        if holder is not None:
            holder.close()

        sock = _open_tcp_socket(
            target_ip,
            port=int(self.config.mac_discovery_tcp_port),
            timeout=float(self.config.prewarmed_socket_timeout_s),
        )
        with self._prewarmed_standby_lock:
            self._prewarmed_standby_holder = PrewarmedSocketHolder(sock, target_ip, self.mono())

    def _probe_prewarmed_keepalive(self, target_ip: str) -> None:
        request = _build_keepalive_request_bytes(target_ip)
        sock = _open_tcp_socket(
            target_ip,
            port=int(self.config.mac_discovery_tcp_port),
            timeout=float(self.config.prewarmed_socket_timeout_s),
        )
        try:
            sock.settimeout(float(self.config.prewarmed_socket_timeout_s))
            sock.sendall(request)
            try:
                sock.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            response_prefix = sock.recv(4)
            if not response_prefix:
                raise ConnectionResetError("prewarmed keepalive got an empty response")
        finally:
            _close_socket(sock)

    def _record_prewarmed_keepalive_success(self, reason: str, target_ip: str, duration_ms: int) -> None:
        with self._prewarmed_standby_lock:
            previous_failures = self._prewarmed_standby_failures
            ready_logged = self._prewarmed_standby_ready_logged and self._prewarmed_standby_last_ip == target_ip
            self._prewarmed_standby_failures = 0
            self._prewarmed_standby_last_ip = target_ip
            self._prewarmed_standby_last_ok_mono = self.mono()
            self._prewarmed_standby_ready_logged = True

        if ready_logged and previous_failures == 0:
            return

        self._log_structured(
            "STEP",
            log_level="info",
            action="PREWARMED_STANDBY_SOCKET",
            reason=reason,
            step="keepalive",
            status="ready",
            target_ip=target_ip,
            duration_ms=duration_ms,
            mode=("persistent_socket" if self.config.prewarmed_persist_socket else "short_connection"),
            mono=f"{self.mono():.3f}",
        )

    def _record_prewarmed_keepalive_failure(self, reason: str, target_ip: str, exc: OSError) -> None:
        self._close_prewarmed_socket_holder()
        with self._prewarmed_standby_lock:
            self._prewarmed_standby_failures += 1
            failures = self._prewarmed_standby_failures
            self._prewarmed_standby_ready_logged = False

        log_level = (
            "info"
            if failures in {1, _PREWARM_FAILURE_LOG_THRESHOLD} or failures % _PREWARM_FAILURE_LOG_THRESHOLD == 0
            else None
        )
        self._log_structured(
            "STEP",
            log_level=log_level,
            action="PREWARMED_STANDBY_SOCKET",
            reason=reason,
            step="keepalive",
            status="failed",
            failures=failures,
            target_ip=target_ip,
            error=repr(exc),
            mono=f"{self.mono():.3f}",
        )

    def _has_recent_prewarmed_keepalive(self) -> bool:
        with self._prewarmed_standby_lock:
            last_ok_mono = float(self._prewarmed_standby_last_ok_mono or 0.0)
        if last_ok_mono <= 0:
            return False
        max_age_s = max(5.0, float(self.config.prewarmed_keepalive_interval_s) * 2.5)
        return (self.mono() - last_ok_mono) <= max_age_s

    def try_send_prewarmed_standby(self, current_ip: str) -> PrewarmedStandbySendResult:
        if not self.config.prewarmed_standby_enabled:
            return PrewarmedStandbySendResult(False, False, "disabled", target_ip=current_ip)
        if not self._has_recent_prewarmed_keepalive():
            return PrewarmedStandbySendResult(False, False, "no_recent_keepalive", target_ip=current_ip)

        deadline_s = float(self.config.prewarmed_send_deadline_s)
        frozen_limit_s = deadline_s * float(self.config.prewarmed_frozen_send_multiplier)
        request = build_standby_request_bytes(current_ip)
        started = self.mono()
        sock = None
        mode = "persistent_socket" if self.config.prewarmed_persist_socket else "short_connection"
        try:
            if self.config.prewarmed_persist_socket:
                sock = self._take_prewarmed_socket_holder(current_ip)
                if sock is None:
                    return PrewarmedStandbySendResult(True, False, "no_socket", target_ip=current_ip, mode=mode)
                sock.settimeout(deadline_s)
            else:
                sock = _open_tcp_socket(
                    current_ip,
                    port=int(self.config.mac_discovery_tcp_port),
                    timeout=deadline_s,
                )

            sock.settimeout(deadline_s)
            sock.sendall(request)
            try:
                sock.shutdown(socket.SHUT_WR)
            except OSError:
                pass
        except OSError as exc:
            duration_ms = int(max(0.0, self.mono() - started) * 1000)
            return PrewarmedStandbySendResult(
                True,
                False,
                f"send_failed:{type(exc).__name__}",
                duration_ms=duration_ms,
                target_ip=current_ip,
                mode=mode,
                error=repr(exc),
            )
        finally:
            _close_socket(sock)

        elapsed_s = self.mono() - started
        duration_ms = int(max(0.0, elapsed_s) * 1000)
        if elapsed_s > frozen_limit_s:
            return PrewarmedStandbySendResult(
                True,
                False,
                "frozen_during_send",
                duration_ms=duration_ms,
                target_ip=current_ip,
                mode=mode,
                frozen_s=f"{elapsed_s:.3f}",
            )

        return PrewarmedStandbySendResult(
            True,
            True,
            "sent",
            duration_ms=duration_ms,
            target_ip=current_ip,
            mode=mode,
        )
