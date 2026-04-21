from __future__ import annotations

import logging

from pykefcontrol.kef_connector import KefConnector

from .models import SpeakerIdentity, normalize_mac, normalize_model_label


class W2Backend:
    backend_name = "w2"

    def __init__(self, log: logging.Logger):
        self.log = log

    def create_connector(self, ip: str) -> KefConnector:
        return KefConnector(ip)

    def capture_identity(self, connector: KefConnector, ip: str) -> SpeakerIdentity:
        def safe_read(attr: str) -> str:
            try:
                value = getattr(connector, attr)
            except Exception:
                return ""
            if value is None:
                return ""
            return str(value)

        model = normalize_model_label(safe_read('speaker_model'))
        mac_display = safe_read('mac_address')
        return SpeakerIdentity(
            ip=ip,
            mac=normalize_mac(mac_display),
            mac_display=mac_display,
            speaker_name=safe_read('speaker_name'),
            speaker_model=model,
            firmware_version=safe_read('firmware_version'),
            backend=self.backend_name,
        )
