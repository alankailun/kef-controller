from __future__ import annotations

import contextlib
import threading

import requests.sessions


_requests_timeout_patch_lock = threading.Lock()
_requests_timeout_local = threading.local()
_REQUESTS_TIMEOUT_UNSET = object()
_requests_timeout_patch_installed = False


def _install_requests_default_timeout_patch() -> None:
    global _requests_timeout_patch_installed

    if _requests_timeout_patch_installed:
        return

    with _requests_timeout_patch_lock:
        if _requests_timeout_patch_installed:
            return

        original_request = requests.sessions.Session.request

        def request_with_scoped_timeout(self, method, url, **kwargs):
            # requests defaults timeout=None, which disables socket.setdefaulttimeout.
            scoped_timeout = getattr(_requests_timeout_local, "timeout", _REQUESTS_TIMEOUT_UNSET)
            if scoped_timeout is not _REQUESTS_TIMEOUT_UNSET and kwargs.get("timeout") is None:
                kwargs["timeout"] = scoped_timeout
            return original_request(self, method, url, **kwargs)

        requests.sessions.Session.request = request_with_scoped_timeout
        _requests_timeout_patch_installed = True


@contextlib.contextmanager
def temporary_socket_timeout(seconds: float):
    _install_requests_default_timeout_patch()
    previous_requests_timeout = getattr(_requests_timeout_local, "timeout", _REQUESTS_TIMEOUT_UNSET)
    _requests_timeout_local.timeout = seconds
    try:
        yield
    finally:
        if previous_requests_timeout is _REQUESTS_TIMEOUT_UNSET:
            try:
                delattr(_requests_timeout_local, "timeout")
            except AttributeError:
                pass
        else:
            _requests_timeout_local.timeout = previous_requests_timeout
