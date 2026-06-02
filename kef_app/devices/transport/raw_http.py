from __future__ import annotations

import errno
import json
import select
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from .errors import is_host_unreachable


@dataclass(frozen=True, slots=True)
class FireAndForgetHttpPostResult:
    success: bool
    attempts: int
    completed: int
    pending: int
    duration_ms: int
    errors: tuple[str, ...] = ()
    all_host_unreachable: bool = False
    abort_reason: str = ""


class SendAbortedError(OSError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


_CONNECT_IN_PROGRESS_ERRORS = {
    errno.EINPROGRESS,
    errno.EWOULDBLOCK,
    errno.EALREADY,
    getattr(errno, "WSAEWOULDBLOCK", 10035),
    getattr(errno, "WSAEINPROGRESS", 10036),
    getattr(errno, "WSAEALREADY", 10037),
}
_CONNECT_SUCCESS_ERRORS = {0, errno.EISCONN, getattr(errno, "WSAEISCONN", 10056)}


def _json_body(payload: Any) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("ascii")


def build_http_post_request_bytes(
    host: str,
    *,
    path: str,
    payload: Any,
    user_agent: str = "kef-controller/raw-http",
) -> bytes:
    body = _json_body(payload)
    headers = (
        f"POST {path} HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        f"User-Agent: {user_agent}\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n"
        "\r\n"
    ).encode("ascii")
    return headers + body


def _send_abort_reason(deadline_mono: float | None, should_send: Callable[[], bool] | None) -> str:
    if deadline_mono is not None and time.monotonic() >= deadline_mono:
        return "deadline_exceeded"
    if should_send is not None and not should_send():
        return "send_gate_closed"
    return ""


def _ensure_send_allowed(deadline_mono: float | None, should_send: Callable[[], bool] | None) -> None:
    reason = _send_abort_reason(deadline_mono, should_send)
    if reason:
        raise SendAbortedError(reason)


def _bounded_socket_timeout(
    timeout: float,
    deadline_mono: float | None,
    should_send: Callable[[], bool] | None,
) -> float:
    _ensure_send_allowed(deadline_mono, should_send)
    bounded_timeout = max(0.0, float(timeout))
    if deadline_mono is None:
        return bounded_timeout

    remaining = deadline_mono - time.monotonic()
    if remaining <= 0:
        raise SendAbortedError("deadline_exceeded")
    return min(bounded_timeout, remaining)


def _raise_connect_error(error_code: int) -> None:
    raise OSError(error_code, "connect_ex failed")


def _connect_with_deadline(
    sock: socket.socket,
    address: tuple[str, int],
    *,
    timeout: float,
    deadline_mono: float | None,
    should_send: Callable[[], bool] | None,
) -> None:
    sock.setblocking(False)
    error_code = int(sock.connect_ex(address))
    if error_code in _CONNECT_SUCCESS_ERRORS:
        return
    if error_code not in _CONNECT_IN_PROGRESS_ERRORS:
        _raise_connect_error(error_code)

    wait_timeout = _bounded_socket_timeout(timeout, deadline_mono, should_send)
    _readable, writable, exceptional = select.select((), (sock,), (sock,), wait_timeout)
    _ensure_send_allowed(deadline_mono, should_send)
    if not writable and not exceptional:
        raise TimeoutError("timed out")

    so_error = int(sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR))
    if so_error != 0:
        _raise_connect_error(so_error)


def _send_one_http_request(
    ip: str,
    request: bytes,
    *,
    port: int,
    timeout: float,
    deadline_mono: float | None = None,
    should_send: Callable[[], bool] | None = None,
) -> None:
    _ensure_send_allowed(deadline_mono, should_send)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        _connect_with_deadline(
            sock,
            (ip, port),
            timeout=timeout,
            deadline_mono=deadline_mono,
            should_send=should_send,
        )
        sock.settimeout(_bounded_socket_timeout(timeout, deadline_mono, should_send))
        _ensure_send_allowed(deadline_mono, should_send)
        sock.sendall(request)
        try:
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def fire_and_forget_http_post(
    ip: str,
    *,
    request: bytes,
    port: int = 80,
    attempts: int = 3,
    socket_timeout: float = 0.18,
    join_timeout: float = 0.25,
    inline_first_attempt: bool = True,
    deadline_mono: float | None = None,
    should_send: Callable[[], bool] | None = None,
) -> FireAndForgetHttpPostResult:
    started = time.monotonic()
    attempts = max(1, int(attempts))
    errors: list[str] = []
    host_unreachable_errors = 0
    completed = 0
    success = False
    abort_reason = ""
    lock = threading.Lock()

    def record_error(exc: OSError) -> None:
        nonlocal completed, host_unreachable_errors
        errors.append(repr(exc))
        if is_host_unreachable(exc):
            host_unreachable_errors += 1
        completed += 1

    def record_success() -> None:
        nonlocal completed, success
        success = True
        completed += 1

    def record_abort(exc: SendAbortedError) -> None:
        nonlocal completed, abort_reason
        abort_reason = exc.reason
        completed += 1

    def build_result() -> FireAndForgetHttpPostResult:
        with lock:
            completed_count = completed
            success_seen = success
            error_snapshot = tuple(errors[:3])
            abort_reason_snapshot = abort_reason
            all_host_unreachable = (
                not success_seen
                and not abort_reason_snapshot
                and completed_count == attempts
                and host_unreachable_errors == completed_count
            )

        return FireAndForgetHttpPostResult(
            success=success_seen,
            attempts=attempts,
            completed=completed_count,
            pending=max(0, attempts - completed_count),
            duration_ms=int((time.monotonic() - started) * 1000),
            errors=error_snapshot,
            all_host_unreachable=all_host_unreachable,
            abort_reason=abort_reason_snapshot,
        )

    initial_abort_reason = _send_abort_reason(deadline_mono, should_send)
    if initial_abort_reason:
        abort_reason = initial_abort_reason
        return build_result()

    if inline_first_attempt:
        try:
            _send_one_http_request(
                ip,
                request,
                port=port,
                timeout=socket_timeout,
                deadline_mono=deadline_mono,
                should_send=should_send,
            )
        except SendAbortedError as exc:
            record_abort(exc)
            return build_result()
        except OSError as exc:
            record_error(exc)
        else:
            record_success()
            return FireAndForgetHttpPostResult(
                success=True,
                attempts=attempts,
                completed=completed,
                pending=0,
                duration_ms=int((time.monotonic() - started) * 1000),
            )

    next_abort_reason = _send_abort_reason(deadline_mono, should_send)
    if next_abort_reason:
        abort_reason = next_abort_reason
        return build_result()

    def worker() -> None:
        try:
            _send_one_http_request(
                ip,
                request,
                port=port,
                timeout=socket_timeout,
                deadline_mono=deadline_mono,
                should_send=should_send,
            )
        except SendAbortedError as exc:
            with lock:
                record_abort(exc)
            return
        except OSError as exc:
            with lock:
                record_error(exc)
            return

        with lock:
            record_success()

    remaining_attempts = max(0, attempts - completed)
    threads = [threading.Thread(target=worker, daemon=True, name="RawHttpPostSend") for _ in range(remaining_attempts)]
    for thread in threads:
        thread.start()

    deadline = time.monotonic() + max(0.0, join_timeout)
    if deadline_mono is not None:
        deadline = min(deadline, deadline_mono)
    for thread in threads:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        thread.join(remaining)
        if success:
            break

    return build_result()
