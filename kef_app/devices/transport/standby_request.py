from __future__ import annotations

from collections.abc import Callable

from .raw_http import FireAndForgetHttpPostResult, build_http_post_request_bytes, fire_and_forget_http_post
from .secure import secure_context, sign_standby

_STANDBY_PAYLOAD = {
    "path": "settings:/kef/play/physicalSource",
    "role": "value",
    "value": {"type": "kefPhysicalSource", "kefPhysicalSource": "standby"},
}

FireAndForgetShutdownResult = FireAndForgetHttpPostResult


def build_standby_request_bytes(host: str) -> bytes:
    request = build_http_post_request_bytes(
        host,
        path="/api/setData",
        payload=_STANDBY_PAYLOAD,
        user_agent="kef-controller/fast-standby",
    )
    if secure_context(host) is not None:
        headers, body = request.split(b"\r\n\r\n", 1)
        authorization = sign_standby(host, body)
        headers = headers.replace(f"Host: {host}\r\n".encode(), f"Host: {host}:4430\r\n".encode())
        request = headers + f"\r\nAuthorization: {authorization}\r\n\r\n".encode() + body
    return request


def fire_and_forget_standby(
    ip: str,
    *,
    port: int = 80,
    attempts: int = 3,
    socket_timeout: float = 0.18,
    join_timeout: float = 0.25,
    inline_first_attempt: bool = True,
    deadline_mono: float | None = None,
    should_send: Callable[[], bool] | None = None,
) -> FireAndForgetShutdownResult:
    context = secure_context(ip)
    extra = {"ssl_context": context} if context is not None else {}
    return fire_and_forget_http_post(
        ip,
        request=build_standby_request_bytes(ip),
        port=4430 if context is not None else port,
        **extra,
        attempts=attempts,
        socket_timeout=socket_timeout,
        join_timeout=join_timeout,
        inline_first_attempt=inline_first_attempt,
        deadline_mono=deadline_mono,
        should_send=should_send,
    )
