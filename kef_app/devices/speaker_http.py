from __future__ import annotations

import contextlib
import threading
from typing import Any
from urllib.parse import urlsplit

import requests
import requests.adapters

_request_local = threading.local()
_TIMEOUT_UNSET = object()


def _speaker_session(url: str) -> requests.Session:
    sessions = getattr(_request_local, "sessions", None)
    if sessions is None:
        sessions = {}
        _request_local.sessions = sessions
    host = urlsplit(url).netloc
    session = sessions.get(host)
    if session is None:
        session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=16)
        session.mount("http://", adapter)
        sessions[host] = session
    return session


class _SpeakerRequests:
    """Requests adapter used only by pykefcontrol's speaker connector."""

    @staticmethod
    def _kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
        timeout = getattr(_request_local, "timeout", _TIMEOUT_UNSET)
        if timeout is not _TIMEOUT_UNSET and kwargs.get("timeout") is None:
            kwargs["timeout"] = timeout
        return kwargs

    def get(self, url, params=None, **kwargs):
        return _speaker_session(url).get(url, params=params, **self._kwargs(kwargs))

    def post(self, url, data=None, json=None, **kwargs):
        return _speaker_session(url).post(url, data=data, json=json, **self._kwargs(kwargs))


speaker_requests = _SpeakerRequests()


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
