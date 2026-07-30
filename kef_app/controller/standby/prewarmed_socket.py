from __future__ import annotations

import select
import socket
import threading
from dataclasses import dataclass

from ...devices.transport import build_standby_request_bytes, is_host_unreachable
from .cache import FastStandbyCacheSnapshot


_PREWARM_RETRY_DELAY_S = 2.0
_PREWARM_POWER_ACTION_DELAY_S = 0.5
_PREWARM_FAILURE_LOG_THRESHOLD = 3
_PREWARM_FAILURE_BACKOFF_THRESHOLD = 3
_PREWARM_FAILURE_BACKOFF_STEP_S = 30.0
_PREWARM_FAILURE_BACKOFF_MAX_S = 60.0
_PREWARM_PERSISTENT_SOCKET_POOL_SIZE = 2
_KEEPALIVE_REQUEST_PATH = "/api/getData?path=settings%3A%2Freleasetext&roles=value"
_KEEPALIVE_MAX_HEADER_BYTES = 16 * 1024
_KEEPALIVE_MAX_BODY_BYTES = 1024 * 1024


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
    host_unreachable: bool = False
    so_error: int | None = None


@dataclass(frozen=True, slots=True)
class CachedPrewarmedStandbySendResult:
    success: bool
    fast_path_used: bool
    fast_path_skip_reason: str = ""
    status: str = ""
    duration_ms: int = 0
    target_ip: str = ""
    target_mac: str = ""
    mode: str = "persistent_socket"
    error: str = ""
    host_unreachable: bool = False
    so_error: int | None = None
    cache_version: int | None = None
    cache_age_ms: int | None = None
    started_mono: float = 0.0
    finished_mono: float = 0.0
    snapshot: FastStandbyCacheSnapshot | None = None


@dataclass(frozen=True, slots=True)
class _PrewarmedSocketSendOutcome:
    success: bool
    status: str
    duration_ms: int
    finished_mono: float
    error: str = ""
    frozen_s: str = ""
    host_unreachable: bool = False
    so_error: int | None = None
    abort_reason: str = ""


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
        "Connection: keep-alive\r\n"
        "\r\n"
    ).encode("ascii")


def _read_limited_line(stream, *, remaining_header_bytes: list[int]) -> bytes:
    line = stream.readline(remaining_header_bytes[0] + 1)
    remaining_header_bytes[0] -= len(line)
    if remaining_header_bytes[0] < 0 or not line.endswith(b"\n"):
        raise ConnectionError("prewarmed keepalive response headers were incomplete or too large")
    return line


def _read_exact(stream, size: int) -> bytes:
    data = stream.read(size)
    if data is None or len(data) != size:
        raise ConnectionError("prewarmed keepalive response ended before its body was complete")
    return data


def _drain_keepalive_http_response(sock: socket.socket, timeout: float) -> None:
    """Read one complete HTTP response so the connection is safe to reuse."""
    sock.settimeout(timeout)
    stream = sock.makefile("rb")
    try:
        remaining_header_bytes = [_KEEPALIVE_MAX_HEADER_BYTES]
        status_line = _read_limited_line(stream, remaining_header_bytes=remaining_header_bytes).strip()
        parts = status_line.split(None, 2)
        if len(parts) < 2 or not parts[0].startswith(b"HTTP/"):
            raise ConnectionError(f"invalid prewarmed keepalive HTTP status: {status_line!r}")
        try:
            status_code = int(parts[1])
        except ValueError as exc:
            raise ConnectionError(f"invalid prewarmed keepalive status code: {status_line!r}") from exc

        headers: dict[bytes, bytes] = {}
        while True:
            line = _read_limited_line(stream, remaining_header_bytes=remaining_header_bytes)
            if line in {b"\r\n", b"\n"}:
                break
            if b":" not in line:
                raise ConnectionError(f"invalid prewarmed keepalive header: {line!r}")
            name, value = line.split(b":", 1)
            headers[name.strip().lower()] = value.strip().lower()

        if not 200 <= status_code < 300:
            raise ConnectionError(f"prewarmed keepalive returned HTTP {status_code}")

        transfer_encoding = headers.get(b"transfer-encoding", b"")
        if b"chunked" in transfer_encoding:
            body_bytes = 0
            while True:
                size_line = _read_limited_line(stream, remaining_header_bytes=remaining_header_bytes)
                size_token = size_line.split(b";", 1)[0].strip()
                try:
                    chunk_size = int(size_token, 16)
                except ValueError as exc:
                    raise ConnectionError(f"invalid prewarmed keepalive chunk size: {size_line!r}") from exc
                if chunk_size < 0 or body_bytes + chunk_size > _KEEPALIVE_MAX_BODY_BYTES:
                    raise ConnectionError("prewarmed keepalive response body was too large")
                if chunk_size == 0:
                    while True:
                        trailer = _read_limited_line(stream, remaining_header_bytes=remaining_header_bytes)
                        if trailer in {b"\r\n", b"\n"}:
                            break
                    break
                _read_exact(stream, chunk_size)
                if _read_exact(stream, 2) != b"\r\n":
                    raise ConnectionError("invalid prewarmed keepalive chunk terminator")
                body_bytes += chunk_size
        elif b"content-length" in headers:
            try:
                content_length = int(headers[b"content-length"])
            except ValueError as exc:
                raise ConnectionError("invalid prewarmed keepalive Content-Length") from exc
            if not 0 <= content_length <= _KEEPALIVE_MAX_BODY_BYTES:
                raise ConnectionError("prewarmed keepalive response body was too large")
            _read_exact(stream, content_length)
        else:
            raise ConnectionError("prewarmed keepalive response had no reusable body framing")

        if b"close" in headers.get(b"connection", b""):
            raise ConnectionError("prewarmed keepalive server requested Connection: close")
    finally:
        stream.close()


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
            self._close_prewarmed_socket_holders()
            return False

        stale_holders: list[PrewarmedSocketHolder] = []
        with self._prewarmed_standby_lock:
            if self._prewarmed_standby_running:
                if self._prewarmed_standby_stop.is_set():
                    self._prewarmed_standby_restart_reason = reason
                return False
            stale_holders = self._prewarmed_standby_holders
            self._prewarmed_standby_holders = []
            self._prewarmed_standby_running = True
            self._prewarmed_standby_restart_reason = None
            self._prewarmed_standby_failures = 0
            self._prewarmed_standby_last_error = ""
            self._prewarmed_standby_stop.clear()

        for holder in stale_holders:
            holder.close()

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

    def _finish_prewarmed_standby_socket_monitor(self) -> None:
        with self._prewarmed_standby_lock:
            self._prewarmed_standby_running = False
            self._prewarmed_standby_thread = None
            restart_reason = self._prewarmed_standby_restart_reason
            self._prewarmed_standby_restart_reason = None

        if restart_reason and self.config.prewarmed_standby_enabled:
            self.start_prewarmed_standby_socket_monitor(restart_reason)

    def _close_prewarmed_socket_holders(self) -> None:
        with self._prewarmed_standby_lock:
            holders = self._prewarmed_standby_holders
            self._prewarmed_standby_holders = []
        for holder in holders:
            holder.close()

    def _take_prewarmed_holder(self, target_ip: str) -> PrewarmedSocketHolder | None:
        selected = None
        stale = []
        with self._prewarmed_standby_lock:
            remaining = []
            for holder in self._prewarmed_standby_holders:
                if holder.ip != target_ip:
                    stale.append(holder)
                elif selected is None:
                    selected = holder
                else:
                    remaining.append(holder)
            self._prewarmed_standby_holders = remaining

        for holder in stale:
            holder.close()
        return selected

    def _take_prewarmed_socket_holder(self, target_ip: str) -> socket.socket | None:
        holder = self._take_prewarmed_holder(target_ip)
        return None if holder is None else holder.take()

    def _return_prewarmed_holder(self, holder: PrewarmedSocketHolder) -> bool:
        should_keep = False
        with self._prewarmed_standby_lock:
            matching_count = sum(1 for current in self._prewarmed_standby_holders if current.ip == holder.ip)
            if holder.sock is not None and matching_count < _PREWARM_PERSISTENT_SOCKET_POOL_SIZE:
                self._prewarmed_standby_holders.append(holder)
                should_keep = True
        if not should_keep:
            holder.close()
        return should_keep

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
            # A suspend worker may have just been dispatched off-pump and still need
            # the remaining holder. Fresh monitor starts clear stale holders before
            # rebuilding the pool, so stopping only halts keepalive work here.
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
            self._close_prewarmed_socket_holders()
            return _PREWARM_RETRY_DELAY_S
        if self._is_controller_power_action_active():
            return _PREWARM_POWER_ACTION_DELAY_S

        target_ip = self.get_current_kef_ip()
        if not target_ip:
            self._close_prewarmed_socket_holders()
            return _PREWARM_RETRY_DELAY_S

        last_ip = self._prewarmed_standby_last_ip
        if last_ip and last_ip != target_ip:
            self._close_prewarmed_socket_holders()
            with self._prewarmed_standby_lock:
                self._prewarmed_standby_failures = 0
                self._prewarmed_standby_last_error = ""
                self._prewarmed_standby_ready_logged = False
                self._prewarmed_standby_last_ok_mono = 0.0

        started = self.mono()
        try:
            if self.config.prewarmed_persist_socket:
                self._ensure_persistent_prewarmed_socket(target_ip)
            self._probe_prewarmed_keepalive(target_ip)
            if self.config.prewarmed_persist_socket:
                self._ensure_persistent_prewarmed_socket(target_ip)
        except OSError as exc:
            if self.config.prewarmed_persist_socket:
                try:
                    self._ensure_persistent_prewarmed_socket(target_ip)
                except OSError as replenish_exc:
                    return self._record_prewarmed_keepalive_failure(reason, target_ip, replenish_exc)
                self._log_structured(
                    "STEP",
                    log_level="info",
                    action="PREWARMED_STANDBY_SOCKET",
                    reason=reason,
                    step="keepalive",
                    status="replaced_failed_connection",
                    target_ip=target_ip,
                    error=repr(exc),
                    mono=f"{self.mono():.3f}",
                )
            else:
                return self._record_prewarmed_keepalive_failure(reason, target_ip, exc)

        finished = self.mono()
        duration_ms = int(max(0.0, finished - started) * 1000)
        self._record_prewarmed_keepalive_success(reason, target_ip, duration_ms)
        return max(1.0, float(self.config.prewarmed_keepalive_interval_s))

    def _ensure_persistent_prewarmed_socket(self, target_ip: str) -> None:
        stale = []
        with self._prewarmed_standby_lock:
            holders = []
            for holder in self._prewarmed_standby_holders:
                if holder.ip == target_ip:
                    holders.append(holder)
                else:
                    stale.append(holder)
            stale.extend(holders[_PREWARM_PERSISTENT_SOCKET_POOL_SIZE:])
            holders = holders[:_PREWARM_PERSISTENT_SOCKET_POOL_SIZE]
            self._prewarmed_standby_holders = holders
            missing = max(0, _PREWARM_PERSISTENT_SOCKET_POOL_SIZE - len(holders))

        for holder in stale:
            holder.close()
        if missing <= 0:
            return

        new_holders = []
        try:
            for _ in range(missing):
                sock = _open_tcp_socket(
                    target_ip,
                    port=int(self.config.mac_discovery_tcp_port),
                    timeout=float(self.config.prewarmed_socket_timeout_s),
                )
                new_holders.append(PrewarmedSocketHolder(sock, target_ip, self.mono()))
        except OSError:
            for holder in new_holders:
                holder.close()
            raise

        with self._prewarmed_standby_lock:
            self._prewarmed_standby_holders.extend(new_holders)

    def _probe_prewarmed_keepalive(self, target_ip: str) -> None:
        request = _build_keepalive_request_bytes(target_ip)
        holder = self._take_prewarmed_holder(target_ip) if self.config.prewarmed_persist_socket else None
        if holder is None:
            sock = _open_tcp_socket(
                target_ip,
                port=int(self.config.mac_discovery_tcp_port),
                timeout=float(self.config.prewarmed_socket_timeout_s),
            )
            holder = PrewarmedSocketHolder(sock, target_ip, self.mono())
        sock = holder.sock
        if sock is None:
            raise ConnectionError("prewarmed keepalive selected an empty socket holder")
        try:
            sock.sendall(request)
            _drain_keepalive_http_response(sock, float(self.config.prewarmed_socket_timeout_s))
        except OSError:
            holder.close()
            raise

        if not self.config.prewarmed_persist_socket:
            holder.close()
            return
        self._return_prewarmed_holder(holder)

    def _record_prewarmed_keepalive_success(self, reason: str, target_ip: str, duration_ms: int) -> None:
        with self._prewarmed_standby_lock:
            previous_failures = self._prewarmed_standby_failures
            ready_logged = self._prewarmed_standby_ready_logged and self._prewarmed_standby_last_ip == target_ip
            self._prewarmed_standby_failures = 0
            self._prewarmed_standby_last_error = ""
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

    def _prewarmed_keepalive_failure_delay(self, failures: int) -> float:
        base_interval = max(1.0, float(self.config.prewarmed_keepalive_interval_s))
        if failures < _PREWARM_FAILURE_BACKOFF_THRESHOLD:
            return base_interval
        if failures < _PREWARM_FAILURE_BACKOFF_THRESHOLD * 2:
            return max(base_interval, _PREWARM_FAILURE_BACKOFF_STEP_S)
        return max(base_interval, _PREWARM_FAILURE_BACKOFF_MAX_S)

    def _record_prewarmed_keepalive_failure(self, reason: str, target_ip: str, exc: OSError) -> float:
        with self._prewarmed_standby_lock:
            self._prewarmed_standby_failures += 1
            failures = self._prewarmed_standby_failures
            self._prewarmed_standby_last_error = repr(exc)
            self._prewarmed_standby_ready_logged = False
        next_delay_s = self._prewarmed_keepalive_failure_delay(failures)

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
            next_delay_s=f"{next_delay_s:.1f}",
            error=repr(exc),
            mono=f"{self.mono():.3f}",
        )
        return next_delay_s

    def _has_recent_prewarmed_keepalive(self) -> bool:
        with self._prewarmed_standby_lock:
            last_ok_mono = float(self._prewarmed_standby_last_ok_mono or 0.0)
        if last_ok_mono <= 0:
            return False
        max_age_s = max(5.0, float(self.config.prewarmed_keepalive_interval_s) * 2.5)
        return (self.mono() - last_ok_mono) <= max_age_s

    def get_prewarmed_standby_health(self) -> dict[str, object]:
        """Return a small, lock-safe snapshot for the optional UI diagnostics."""
        with self._prewarmed_standby_lock:
            last_ok_mono = float(self._prewarmed_standby_last_ok_mono or 0.0)
            failures = int(self._prewarmed_standby_failures)
            last_error = str(self._prewarmed_standby_last_error or "")
        return {
            "last_heartbeat_age_s": round(max(0.0, self.mono() - last_ok_mono), 1) if last_ok_mono else None,
            "failures": failures,
            "last_error": last_error,
        }

    def _send_standby_over_socket(
        self,
        sock: socket.socket,
        request: bytes,
        *,
        target_ip: str,
        started_mono: float,
        deadline_s: float,
        frozen_limit_s: float,
        deadline_mono: float | None = None,
        generation: int | None = None,
        reject_readable_before_send: bool = False,
    ) -> _PrewarmedSocketSendOutcome:
        so_error: int | None = None
        try:
            sock.settimeout(deadline_s)
            abort_reason = self._bounded_standby_abort_reason(
                deadline_mono=deadline_mono,
                generation=generation,
            )
            if abort_reason:
                finished = self.mono()
                return _PrewarmedSocketSendOutcome(
                    False,
                    f"skipped_{abort_reason}",
                    int(max(0.0, finished - started_mono) * 1000),
                    finished,
                    abort_reason=abort_reason,
                )

            if reject_readable_before_send:
                # Keepalive drains its complete response before returning a
                # holder to the pool.  A readable persistent socket therefore
                # signals peer FIN/RST (or otherwise unexpected bytes), not a
                # response we may safely ignore.  Do not let sendall merely
                # queue bytes locally and turn that stale connection into a
                # false standby success.
                readable, _, _ = select.select([sock], [], [], 0)
                if readable:
                    finished = self.mono()
                    return _PrewarmedSocketSendOutcome(
                        False,
                        "stale_socket_readable",
                        int(max(0.0, finished - started_mono) * 1000),
                        finished,
                    )

            sock.sendall(request)
            so_error = int(sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR))
            if so_error != 0:
                raise OSError(so_error, "socket SO_ERROR was non-zero after standby send")
            try:
                sock.shutdown(socket.SHUT_WR)
            except OSError:
                pass
        except OSError as exc:
            finished = self.mono()
            status = (
                f"so_error_after_send:{so_error}"
                if so_error not in {None, 0}
                else f"send_failed:{type(exc).__name__}"
            )
            return _PrewarmedSocketSendOutcome(
                False,
                status,
                int(max(0.0, finished - started_mono) * 1000),
                finished,
                error=repr(exc),
                host_unreachable=is_host_unreachable(exc),
                so_error=so_error,
            )
        finally:
            _close_socket(sock)

        finished = self.mono()
        elapsed_s = finished - started_mono
        duration_ms = int(max(0.0, elapsed_s) * 1000)
        if elapsed_s > frozen_limit_s:
            return _PrewarmedSocketSendOutcome(
                False,
                "frozen_during_send",
                duration_ms,
                finished,
                frozen_s=f"{elapsed_s:.3f}",
            )

        return _PrewarmedSocketSendOutcome(
            True,
            "sent",
            duration_ms,
            finished,
            so_error=0,
        )

    def _send_standby_over_prewarmed_pool(
        self,
        target_ip: str,
        request: bytes,
        *,
        started_mono: float,
        deadline_s: float,
        frozen_limit_s: float,
        deadline_mono: float | None = None,
        generation: int | None = None,
    ) -> _PrewarmedSocketSendOutcome | None:
        """Try each persistent socket once before falling back to cold TCP.

        A send consumes its socket because the standby request closes the write
        side.  Retrying the other prewarmed holder is therefore safe and avoids
        treating one stale pooled connection as a reason to reconnect.
        """
        last_outcome: _PrewarmedSocketSendOutcome | None = None
        for _ in range(_PREWARM_PERSISTENT_SOCKET_POOL_SIZE):
            sock = self._take_prewarmed_socket_holder(target_ip)
            if sock is None:
                break
            attempt_started_mono = self.mono()
            outcome = self._send_standby_over_socket(
                sock,
                request,
                target_ip=target_ip,
                started_mono=attempt_started_mono,
                deadline_s=deadline_s,
                frozen_limit_s=frozen_limit_s,
                deadline_mono=deadline_mono,
                generation=generation,
                reject_readable_before_send=True,
            )
            last_outcome = outcome
            if outcome.success or outcome.abort_reason:
                return outcome
        return last_outcome

    def try_send_prewarmed_standby(
        self,
        current_ip: str,
        *,
        deadline_mono: float | None = None,
        generation: int | None = None,
        reason: str = "fast_standby",
    ) -> PrewarmedStandbySendResult:
        if not self.config.prewarmed_standby_enabled:
            return PrewarmedStandbySendResult(False, False, "disabled", target_ip=current_ip)
        if not self._has_recent_prewarmed_keepalive():
            return PrewarmedStandbySendResult(False, False, "no_recent_keepalive", target_ip=current_ip)

        abort_reason = self._bounded_standby_abort_reason(
            deadline_mono=deadline_mono,
            generation=generation,
        )
        if abort_reason:
            return PrewarmedStandbySendResult(False, False, f"skipped_{abort_reason}", target_ip=current_ip)

        deadline_s = float(self.config.prewarmed_send_deadline_s)
        frozen_limit_s = (
            float("inf")
            if deadline_mono is None and generation is not None
            else deadline_s * float(self.config.prewarmed_frozen_send_multiplier)
        )
        request = build_standby_request_bytes(current_ip)
        started = self.mono()
        mode = "persistent_socket" if self.config.prewarmed_persist_socket else "short_connection"
        with self._state_lock:
            event_mono = float(self._last_windows_event_mono or 0.0)
        fields: dict[str, object] = {
            "action": "PREWARMED_STANDBY_SOCKET",
            "reason": reason,
            "step": "send_enter",
            "target_ip": current_ip,
            "mode": mode,
            "deadline_s": f"{deadline_s:.2f}",
            "mono": f"{started:.3f}",
        }
        if event_mono > 0:
            fields["since_windows_event_ms"] = int(max(0.0, started - event_mono) * 1000)
        self._log_structured("STEP", log_level="info", **fields)

        if self.config.prewarmed_persist_socket:
            outcome = self._send_standby_over_prewarmed_pool(
                current_ip,
                request,
                started_mono=started,
                deadline_s=deadline_s,
                frozen_limit_s=frozen_limit_s,
                deadline_mono=deadline_mono,
                generation=generation,
            )
            if outcome is None:
                return PrewarmedStandbySendResult(True, False, "no_socket", target_ip=current_ip, mode=mode)
        else:
            abort_reason = self._bounded_standby_abort_reason(
                deadline_mono=deadline_mono,
                generation=generation,
            )
            if abort_reason:
                return PrewarmedStandbySendResult(
                    False,
                    False,
                    f"skipped_{abort_reason}",
                    target_ip=current_ip,
                    mode=mode,
                )
            try:
                sock = _open_tcp_socket(
                    current_ip,
                    port=int(self.config.mac_discovery_tcp_port),
                    timeout=deadline_s,
                )
            except OSError as exc:
                finished = self.mono()
                return PrewarmedStandbySendResult(
                    True,
                    False,
                    f"send_failed:{type(exc).__name__}",
                    duration_ms=int(max(0.0, finished - started) * 1000),
                    target_ip=current_ip,
                    mode=mode,
                    error=repr(exc),
                    host_unreachable=is_host_unreachable(exc),
                )

            outcome = self._send_standby_over_socket(
                sock,
                request,
                target_ip=current_ip,
                started_mono=started,
                deadline_s=deadline_s,
                frozen_limit_s=frozen_limit_s,
                deadline_mono=deadline_mono,
                generation=generation,
            )
        return PrewarmedStandbySendResult(
            True,
            outcome.success,
            outcome.status,
            duration_ms=outcome.duration_ms,
            target_ip=current_ip,
            mode=mode,
            error=outcome.error,
            frozen_s=outcome.frozen_s,
            host_unreachable=outcome.host_unreachable,
            so_error=outcome.so_error,
        )

    def try_send_cached_prewarmed_standby(
        self,
        *,
        deadline_mono: float | None = None,
        generation: int | None = None,
    ) -> CachedPrewarmedStandbySendResult:
        started = self.mono()
        mode = "persistent_socket"
        if not self.config.prewarmed_standby_enabled:
            return CachedPrewarmedStandbySendResult(
                False,
                False,
                fast_path_skip_reason="prewarmed_disabled",
                status="disabled",
                started_mono=started,
                finished_mono=started,
            )
        if not self.config.prewarmed_persist_socket:
            return CachedPrewarmedStandbySendResult(
                False,
                False,
                fast_path_skip_reason="persistent_socket_disabled",
                status="disabled",
                started_mono=started,
                finished_mono=started,
            )

        snapshot = self._fast_standby_send_cache.read()
        if snapshot is None:
            return CachedPrewarmedStandbySendResult(
                False,
                False,
                fast_path_skip_reason="no_cache",
                status="no_cache",
                started_mono=started,
                finished_mono=started,
            )

        abort_reason = self._bounded_standby_abort_reason(
            deadline_mono=deadline_mono,
            generation=generation,
        )
        if abort_reason:
            return CachedPrewarmedStandbySendResult(
                False,
                False,
                fast_path_skip_reason=abort_reason,
                status=f"skipped_{abort_reason}",
                target_ip=snapshot.target_ip,
                target_mac=snapshot.target_mac,
                cache_version=snapshot.version,
                cache_age_ms=int(max(0.0, started - snapshot.updated_mono) * 1000),
                started_mono=started,
                finished_mono=started,
                snapshot=snapshot,
            )

        cache_age_ms = int(max(0.0, started - snapshot.updated_mono) * 1000)
        deadline_s = float(self.config.prewarmed_send_deadline_s)
        frozen_limit_s = (
            float("inf")
            if deadline_mono is None and generation is not None
            else deadline_s * float(self.config.prewarmed_frozen_send_multiplier)
        )
        outcome = self._send_standby_over_prewarmed_pool(
            snapshot.target_ip,
            snapshot.standby_request_bytes,
            started_mono=started,
            deadline_s=deadline_s,
            frozen_limit_s=frozen_limit_s,
            deadline_mono=deadline_mono,
            generation=generation,
        )
        if outcome is None:
            return CachedPrewarmedStandbySendResult(
                False,
                False,
                fast_path_skip_reason="no_socket_for_cached_ip",
                status="no_socket",
                target_ip=snapshot.target_ip,
                target_mac=snapshot.target_mac,
                mode=mode,
                cache_version=snapshot.version,
                cache_age_ms=cache_age_ms,
                started_mono=started,
                finished_mono=self.mono(),
                snapshot=snapshot,
            )

        if not outcome.success:
            if outcome.abort_reason:
                fast_path_skip_reason = outcome.abort_reason
            elif outcome.frozen_s:
                fast_path_skip_reason = "frozen_during_send"
            else:
                fast_path_skip_reason = "send_oserror"

            return CachedPrewarmedStandbySendResult(
                False,
                False,
                fast_path_skip_reason=fast_path_skip_reason,
                status=outcome.status,
                duration_ms=outcome.duration_ms,
                target_ip=snapshot.target_ip,
                target_mac=snapshot.target_mac,
                mode=mode,
                error=outcome.error,
                host_unreachable=outcome.host_unreachable,
                so_error=outcome.so_error,
                cache_version=snapshot.version,
                cache_age_ms=cache_age_ms,
                started_mono=started,
                finished_mono=outcome.finished_mono,
                snapshot=snapshot,
            )

        return CachedPrewarmedStandbySendResult(
            True,
            True,
            status=outcome.status,
            duration_ms=outcome.duration_ms,
            target_ip=snapshot.target_ip,
            target_mac=snapshot.target_mac,
            mode=mode,
            so_error=outcome.so_error,
            cache_version=snapshot.version,
            cache_age_ms=cache_age_ms,
            started_mono=started,
            finished_mono=outcome.finished_mono,
            snapshot=snapshot,
        )
