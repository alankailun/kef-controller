from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from ..controller import KefPowerController


class ControllerEventBridge(QObject):
    identity_changed = Signal(object)
    speaker_state_changed = Signal(object, object, object)
    power_action_started = Signal(str, str)
    power_action_finished = Signal(str, str, bool, str)

    def __init__(self, controller: KefPowerController) -> None:
        super().__init__()
        self._controller = controller
        self._disposed = False
        self._controller.add_event_listener(self._handle_event)

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._controller.remove_event_listener(self._handle_event)

    def _handle_event(self, event_name: str, payload: dict[str, object]) -> None:
        if event_name == "identity_changed":
            self.identity_changed.emit(payload.get("identity"))
            return

        if event_name == "speaker_state_changed":
            self.speaker_state_changed.emit(
                payload.get("input_source"),
                payload.get("volume"),
                payload.get("speaker_on"),
            )
            return

        if event_name == "power_action_started":
            self.power_action_started.emit(
                str(payload.get("action", "")),
                str(payload.get("reason", "")),
            )
            return

        if event_name == "power_action_finished":
            self.power_action_finished.emit(
                str(payload.get("action", "")),
                str(payload.get("reason", "")),
                bool(payload.get("success", False)),
                str(payload.get("outcome", "")),
            )
