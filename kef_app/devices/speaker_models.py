from __future__ import annotations

from dataclasses import dataclass, replace
import re


def normalize_mac(mac: str) -> str:
    return re.sub(r"[^0-9A-Fa-f]", "", mac or "").upper()


INPUT_SOURCE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("Optical", "optical"),
    ("Coaxial", "coaxial"),
    ("Bluetooth", "bluetooth"),
    ("Wi-Fi", "wifi"),
    ("Analog (Aux)", "analog"),
    ("TV (eARC)", "tv"),
    ("USB", "usb"),
)

_INPUT_SOURCE_ALIASES = {
    "": "",
    "optical": "optical",
    "optic": "optical",
    "coaxial": "coaxial",
    "bluetooth": "bluetooth",
    "bt": "bluetooth",
    "wifi": "wifi",
    "wi-fi": "wifi",
    "wireless": "wifi",
    "analog": "analog",
    "aux": "analog",
    "tv": "tv",
    "earc": "tv",
    "e-arc": "tv",
    "hdmi": "tv",
    "usb": "usb",
    "standby": "standby",
    "poweron": "powerOn",
    "poweron_": "powerOn",
    "poweron.": "powerOn",
    "coax": "coaxial",
}


def normalize_input_source(source: str) -> str:
    raw = (source or "").strip()
    if not raw:
        return ""
    compact = re.sub(r"[\s_-]+", "", raw).casefold()
    return _INPUT_SOURCE_ALIASES.get(compact, raw.casefold())


_MODEL_ALIASES = {
    "LS50WII": "LS50 Wireless II",
    "LS50WIRELESSII": "LS50 Wireless II",
    "LSXII": "LSX II",
    "LS60": "LS60 Wireless",
    "LS60WIRELESS": "LS60 Wireless",
}


def normalize_model_label(model: str) -> str:
    raw = (model or "").strip()
    compact = re.sub(r"[^A-Za-z0-9]", "", raw).upper()
    return _MODEL_ALIASES.get(compact, raw)


@dataclass(slots=True)
class SpeakerIdentity:
    ip: str = ""
    mac: str = ""
    mac_display: str = ""
    speaker_name: str = ""
    speaker_model: str = ""
    firmware_version: str = ""
    available: bool = False
    backend: str = "w2"
    matched_by: str = ""

    def with_match(self, matched_by: str) -> "SpeakerIdentity":
        return replace(self, matched_by=matched_by)
