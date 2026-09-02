from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from ...config import AppConfig
from ..speaker_models import SpeakerIdentity, normalize_mac, normalize_model_label


def http_get_kef_data(ip: str, path: str, timeout: float, roles: str = "value"):
    try:
        query = urllib.parse.urlencode({"path": path, "roles": roles})
        url = f"http://{ip}/api/getData?{query}"
        request = urllib.request.Request(url, headers={"User-Agent": "kef-controller/1.0"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
        data = json.loads(payload.decode("utf-8", errors="ignore"))
        return data if isinstance(data, list) and data else None
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError):
        return None


def parse_kef_release_text(release_text: str) -> str:
    if not release_text:
        return ""
    return normalize_model_label(str(release_text).split("_", 1)[0].strip())


def looks_like_supported_kef_model(model: str, config: AppConfig) -> bool:
    normalized = normalize_model_label(model)
    if not normalized:
        return False
    allowed = {normalize_model_label(item) for item in config.supported_w2_models}
    return normalized in allowed


def identify_kef_device(ip: str, config: AppConfig, *, timeout: float | None = None) -> SpeakerIdentity | None:
    http_timeout = config.blind_discovery_http_timeout if timeout is None else timeout
    release_data = http_get_kef_data(ip, "settings:/releasetext", timeout=http_timeout)
    if not release_data:
        return None

    if not isinstance(release_data[0], dict):
        return None
    release_text = release_data[0].get("string_", "")
    speaker_model = parse_kef_release_text(release_text)
    if not looks_like_supported_kef_model(speaker_model, config):
        return None

    mac_address = ""
    mac_data = http_get_kef_data(ip, "settings:/system/primaryMacAddress", timeout=http_timeout)
    if mac_data and isinstance(mac_data[0], dict):
        mac_address = mac_data[0].get("string_", "")

    speaker_name = ""
    name_data = http_get_kef_data(ip, "settings:/deviceName", timeout=http_timeout)
    if name_data and isinstance(name_data[0], dict):
        speaker_name = name_data[0].get("string_", "")

    return SpeakerIdentity(
        ip=ip,
        mac=normalize_mac(mac_address),
        mac_display=mac_address,
        speaker_name=speaker_name,
        speaker_model=speaker_model,
        firmware_version=release_text,
        backend=config.backend_name,
    )
