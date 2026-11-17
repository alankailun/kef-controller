from __future__ import annotations

import json
import ssl
import threading

import urllib3
from pykefcontrol import Credentials, _auth, create_ssl_context

# pykefcontrol 1.0.1 exposes credentials but not its signing helper publicly.
_contexts: dict[str, ssl.SSLContext] = {}
_lock = threading.Lock()


def secure_context(ip: str) -> ssl.SSLContext | None:
    return _contexts.get(ip)


def prepare_secure_transport(ip: str, client) -> None:
    if client.is_secure is not True:
        return
    context = _contexts.get(ip) or create_ssl_context()
    with _lock:
        _contexts[ip] = context
    credentials = Credentials.default()
    pool = urllib3.PoolManager(ssl_context=context)
    try:
        response = pool.request("POST", f"https://{ip}:4430/api/setData",
                                body=json.dumps(_auth.add_user_payload(credentials)).encode(),
                                headers={"Content-Type": "application/json"},
                                timeout=urllib3.Timeout(total=3), retries=False, redirect=False)
        if not _auth.is_already_provisioned(response.status, response.data):
            raise ConnectionError(f"HTTPS standby registration failed: HTTP {response.status}")
    finally:
        pool.clear()


def sign_standby(ip: str, body: bytes) -> str:
    return _auth.sign(Credentials.default(), f"https://{ip}:4430/api/setData", body)
