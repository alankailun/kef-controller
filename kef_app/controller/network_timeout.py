from __future__ import annotations

import contextlib
import threading
from typing import Any

import requests
import requests.adapters
import requests.sessions

_requests_timeout_patch_lock = threading.Lock()
_requests_timeout_local = threading.local()
_REQUESTS_TIMEOUT_UNSET = object()
_requests_timeout_patch_installed = False
_pooled_requests_session: requests.Session | None = None


def _apply_scoped_timeout(kwargs: dict[str, Any]) -> None:
    # requests defaults timeout=None, which disables socket.setdefaulttimeout.
    scoped_timeout = getattr(_requests_timeout_local, "timeout", _REQUESTS_TIMEOUT_UNSET)
    if scoped_timeout is not _REQUESTS_TIMEOUT_UNSET and kwargs.get("timeout") is None:
        kwargs["timeout"] = scoped_timeout


def _get_pooled_requests_session() -> requests.Session:
    global _pooled_requests_session

    if _pooled_requests_session is not None:
        return _pooled_requests_session

    with _requests_timeout_patch_lock:
        if _pooled_requests_session is None:
            session = requests.Session()
            adapter = requests.adapters.HTTPAdapter(pool_connections=16, pool_maxsize=32)
            session.mount("http://", adapter)
            session.mount("https://", adapter)
            _pooled_requests_session = session
        return _pooled_requests_session


def _pooled_requests_get(url, params=None, **kwargs):
    _apply_scoped_timeout(kwargs)
    return _get_pooled_requests_session().get(url, params=params, **kwargs)


def _pooled_requests_post(url, data=None, json=None, **kwargs):
    _apply_scoped_timeout(kwargs)
    return _get_pooled_requests_session().post(url, data=data, json=json, **kwargs)


def _install_requests_default_timeout_patch() -> None:
    global _requests_timeout_patch_installed

    if _requests_timeout_patch_installed:
        return

    with _requests_timeout_patch_lock:
        if _requests_timeout_patch_installed:
            return

        original_request = requests.sessions.Session.request

        def request_with_scoped_timeout(self, method, url, **kwargs):
            _apply_scoped_timeout(kwargs)
            return original_request(self, method, url, **kwargs)

        requests.sessions.Session.request = request_with_scoped_timeout
        requests.get = _pooled_requests_get
        requests.post = _pooled_requests_post
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
