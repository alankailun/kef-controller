from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from ..controller import KefPowerController


class ControllerEventBridge(QObject):
    identity_changed = Signal(object)
    power_action_started = Signal(str, str)
    power_action_finished = Signal(str, str, bool, str)

    def __init__(self, controller: KefPowerController) -> None:
        super().__init__()
        self._controller = controller
        self._controller.add_event_listener(self._handle_event)

    def _handle_event(self, event_name: str, payload: dict[str, object]) -> None:
        if event_name == "identity_changed":
            self.identity_changed.emit(payload.get("identity"))
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
