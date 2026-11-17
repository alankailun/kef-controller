from __future__ import annotations

import contextlib
import threading

_request_local = threading.local()
_TIMEOUT_UNSET = object()


def current_socket_timeout(default: float) -> float:
    return getattr(_request_local, "timeout", default)


@contextlib.contextmanager
def temporary_socket_timeout(seconds: float):
    previous = getattr(_request_local, "timeout", _TIMEOUT_UNSET)
    _request_local.timeout = seconds
    try:
        yield
    finally:
        if previous is _TIMEOUT_UNSET:
            delattr(_request_local, "timeout")
        else:
            _request_local.timeout = previous
