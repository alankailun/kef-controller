from __future__ import annotations

import logging
import weakref

from pykefcontrol import KefClient, paths

from .speaker_http import current_socket_timeout
from .speaker_models import SpeakerIdentity, normalize_mac, normalize_model_label
from .transport.secure import prepare_secure_transport


class SpeakerConnector:
    """Keep the controller's device contract at the pykefcontrol boundary."""

    def __init__(self, ip: str, timeout: float):
        self.request_timeout = timeout
        self.client = KefClient(ip, timeout=timeout)
        # Cached connectors can still be in use when a target/reset replaces them.
        self._cleanup = weakref.finalize(self, self.client.close)
        self._events = self.client.event_queue()
        self.client.connect()
        prepare_secure_transport(ip, self.client)

    status = property(lambda self: self.client.get_status())
    source = property(lambda self: self.client.get_source(),
                      lambda self, value: self.client.set_source(value))
    volume = property(lambda self: self.client.get_volume(),
                      lambda self, value: self.client.set_volume(value))

    def shutdown(self) -> None:
        self.client.power_off()

    def reset_event_subscription(self) -> None:
        self._events.reset()

    def poll_speaker(self, timeout: int) -> dict[str, object]:
        fields = {paths.PHYSICAL_SOURCE: "source", paths.VOLUME: "volume",
                  paths.SPEAKER_STATUS: "speaker_status"}
        if self._events.queue_id is None:
            self._events.subscribe(fields)
            # A new subscription does not replay the speaker's current values.
            return {"source": self.source, "volume": self.volume, "speaker_status": self.status}
        return {fields[event.path]: event.value for event in self._events.poll(timeout)
                if event.path in fields and event.has_value}

    def get_wifi_information(self) -> dict[str, object]:
        wireless = self.client.get_network_info().wireless
        if wireless is None:
            return {}
        return {"signalLevel": wireless.signal_level, "ssid": wireless.ssid,
                "frequency": wireless.frequency, "bssid": wireless.bssid}


class W2Backend:
    backend_name = "w2"

    def __init__(self, log: logging.Logger):
        self.log = log

    def create_connector(self, ip: str) -> SpeakerConnector:
        return SpeakerConnector(ip, current_socket_timeout(5.0))

    def capture_identity(self, connector: SpeakerConnector, ip: str) -> SpeakerIdentity:
        info = connector.client.get_device_info()
        mac_display = info.mac_address or ""
        return SpeakerIdentity(
            ip=ip,
            mac=normalize_mac(mac_display),
            mac_display=mac_display,
            speaker_name=info.name or "",
            speaker_model=normalize_model_label(str(info.model)),
            firmware_version=str(info.firmware) if info.firmware is not None else (info.release_text or ""),
            backend=self.backend_name,
        )
