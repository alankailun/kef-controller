from __future__ import annotations

import json
import socket
import threading
import time
from dataclasses import dataclass


_STANDBY_PAYLOAD = {
    "path": "settings:/kef/play/physicalSource",
    "roles": "value",
    "value": {"type": "kefPhysicalSource", "kefPhysicalSource": "standby"},
}


@dataclass(frozen=True, slots=True)
class FireAndForgetShutdownResult:
    success: bool
    attempts: int
    completed: int
    pending: int
    duration_ms: int
    errors: tuple[str, ...] = ()


def build_standby_request_bytes(host: str) -> bytes:
    body = json.dumps(_STANDBY_PAYLOAD, separators=(",", ":")).encode("ascii")
    headers = (
        f"POST /api/setData HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        "User-Agent: kef-controller/fast-standby\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n"
        "\r\n"
    ).encode("ascii")
    return headers + body


def _send_one_standby_request(ip: str, request: bytes, *, port: int, timeout: float) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect((ip, port))
        sock.settimeout(timeout)
        sock.sendall(request)
        try:
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def fire_and_forget_standby(
    ip: str,
    *,
    port: int = 80,
    attempts: int = 3,
    socket_timeout: float = 0.18,
    join_timeout: float = 0.25,
) -> FireAndForgetShutdownResult:
    started = time.monotonic()
    request = build_standby_request_bytes(ip)
    attempts = max(1, int(attempts))
    errors: list[str] = []
    completed = 0
    success = False
    lock = threading.Lock()

    def worker() -> None:
        nonlocal completed, success
        try:
            _send_one_standby_request(ip, request, port=port, timeout=socket_timeout)
        except OSError as exc:
            with lock:
                errors.append(repr(exc))
                completed += 1
            return

        with lock:
            success = True
            completed += 1

    threads = [threading.Thread(target=worker, daemon=True, name="FastStandbySend") for _ in range(attempts)]
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

    return FireAndForgetShutdownResult(
        success=success_seen,
        attempts=attempts,
        completed=completed_count,
        pending=max(0, attempts - completed_count),
        duration_ms=int((time.monotonic() - started) * 1000),
        errors=error_snapshot,
    )
