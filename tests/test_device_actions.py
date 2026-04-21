from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from kef_app.controller_support.device_actions import ControllerDeviceActionsMixin


class _FakeSpeaker:
    def __init__(self, initial_source: str) -> None:
        self.current_source = initial_source
        self.source_commands: list[str] = []

    @property
    def source(self) -> str:
        return self.current_source

    @source.setter
    def source(self, value: str) -> None:
        self.current_source = value
        self.source_commands.append(value)


class _FakeController(ControllerDeviceActionsMixin):
    def __init__(
        self,
        initial_source: str,
        input_reads: list[str],
        player_reads: list[str | None],
    ) -> None:
        self.config = SimpleNamespace(socket_timeout=0.01)
        self._speaker_lock = threading.Lock()
        self._action_lock = threading.Lock()
        self._speaker = _FakeSpeaker(initial_source)
        self._current_kef_ip = "192.168.1.50"
        self._input_reads = list(input_reads)
        self._player_reads = list(player_reads)
        self.logged: list[tuple[str, dict[str, object]]] = []
        self.now = 0.0

    def mono(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def _log_structured(self, tag: str, **fields):
        self.logged.append((tag, fields))

    def get_current_kef_ip(self) -> str:
        return self._current_kef_ip

    def get_speaker(self, fresh: bool = False) -> _FakeSpeaker:
        return self._speaker

    def get_input_source(self, fresh: bool = False) -> str:
        if self._input_reads:
            return self._input_reads.pop(0)
        return self._speaker.current_source

    def get_player_source_hint(self, fresh: bool = False) -> str | None:
        if self._player_reads:
            return self._player_reads.pop(0)
        return None

    def reset_speaker(self):
        return None


class ChangeInputLiveTests(unittest.TestCase):
    def _run_with_fake_sleep(self, controller: _FakeController, new_input: str) -> bool:
        sleep_calls: list[float] = []

        def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)
            controller.advance(seconds)

        with patch("kef_app.controller_support.device_actions.time.sleep", side_effect=fake_sleep):
            result = controller.change_input_live(new_input)
        self.sleep_calls = sleep_calls
        return result

    def test_switching_to_bluetooth_still_uses_immediate_player_source_verification(self):
        controller = _FakeController(
            initial_source="optical",
            input_reads=["optical", "bluetooth", "bluetooth"],
            player_reads=["optical", "optical", "optical"],
        )

        ok = self._run_with_fake_sleep(controller, "bluetooth")

        self.assertFalse(ok)
        self.assertEqual(controller._speaker.source_commands, ["bluetooth", "bluetooth"])
        self.assertTrue(
            any(
                tag == "WARN" and fields.get("cause") == "player_source_not_verified"
                for tag, fields in controller.logged
            )
        )
        self.assertFalse(
            any(
                tag == "STEP" and fields.get("step") == "bluetooth_settle_check"
                for tag, fields in controller.logged
            )
        )

    def test_leaving_bluetooth_uses_same_immediate_flow_as_other_inputs(self):
        controller = _FakeController(
            initial_source="bluetooth",
            input_reads=["bluetooth", "coaxial"],
            player_reads=["bluetooth", "coaxial"],
        )

        ok = self._run_with_fake_sleep(controller, "coaxial")

        self.assertTrue(ok)
        self.assertEqual(controller._speaker.source_commands, ["coaxial"])
        self.assertFalse(
            any(
                tag == "STEP"
                and fields.get("step") in {"bluetooth_cycle_phase", "delay_input_after_bluetooth_cycle"}
                for tag, fields in controller.logged
            )
        )


if __name__ == "__main__":
    unittest.main()
