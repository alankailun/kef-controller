from __future__ import annotations

from .raw_http import FireAndForgetHttpPostResult, build_http_post_request_bytes, fire_and_forget_http_post


_STANDBY_PAYLOAD = {
    "path": "settings:/kef/play/physicalSource",
    "roles": "value",
    "value": {"type": "kefPhysicalSource", "kefPhysicalSource": "standby"},
}

FireAndForgetShutdownResult = FireAndForgetHttpPostResult


def build_standby_request_bytes(host: str) -> bytes:
    return build_http_post_request_bytes(
        host,
        path="/api/setData",
        payload=_STANDBY_PAYLOAD,
        user_agent="kef-controller/fast-standby",
    )


def fire_and_forget_standby(
    ip: str,
    *,
    port: int = 80,
    attempts: int = 3,
    socket_timeout: float = 0.18,
    join_timeout: float = 0.25,
    inline_first_attempt: bool = True,
) -> FireAndForgetShutdownResult:
    return fire_and_forget_http_post(
        ip,
        request=build_standby_request_bytes(ip),
        port=port,
        attempts=attempts,
        socket_timeout=socket_timeout,
        join_timeout=join_timeout,
        inline_first_attempt=inline_first_attempt,
    )
