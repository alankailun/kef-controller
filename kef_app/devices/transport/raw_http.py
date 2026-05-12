from __future__ import annotations

import json
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any

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


def _send_one_http_request(ip: str, request: bytes, *, port: int, timeout: float) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect((ip, port))
        sock.settimeout(timeout)
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
) -> FireAndForgetHttpPostResult:
    started = time.monotonic()
    attempts = max(1, int(attempts))
    errors: list[str] = []
    host_unreachable_errors = 0
    completed = 0
    success = False
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

    if inline_first_attempt:
        try:
            _send_one_http_request(ip, request, port=port, timeout=socket_timeout)
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

    def worker() -> None:
        try:
            _send_one_http_request(ip, request, port=port, timeout=socket_timeout)
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
    for thread in threads:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        thread.join(remaining)
        if success:
            break

    with lock:
        completed_count = completed
        success_seen = success
        error_snapshot = tuple(errors[:3])
        all_host_unreachable = (
            not success_seen
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
    )
