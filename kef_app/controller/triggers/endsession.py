from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...platform.windows import ENDSESSION_CLOSEAPP, ENDSESSION_CRITICAL, ENDSESSION_LOGOFF, decode_query_end_session_flags


@dataclass(frozen=True, slots=True)
class QueryEndSessionTrigger:
    name: str = "query_end_session"
    default_reason: str = "WM_QUERYENDSESSION"

    def fire(self, controller: Any, wparam: int, lparam: int) -> bool:
        controller._set_session_ending(True)
        controller._new_generation("sleep", self.default_reason)
        flags = decode_query_end_session_flags(lparam)
        controller._log_structured(
            "EVENT",
            kind="WINDOW",
            name=self.default_reason,
            wparam=wparam,
            lparam=f"0x{lparam:08X}",
            flags=flags,
        )
        controller._log_structured(
            "STEP",
            action="STANDBY",
            reason=self.default_reason,
            step="generation_refresh",
            status="wake_threads_interrupted_wait_endsession",
        )

        is_rm_closeapp = bool(lparam & ENDSESSION_CLOSEAPP) and not bool(lparam & (ENDSESSION_LOGOFF | ENDSESSION_CRITICAL))
        if controller.config.fast_exit_on_endsession and is_rm_closeapp:
            controller._log_structured(
                "STEP",
                action="PROCESS_EXIT",
                reason=self.default_reason,
                step="request_self_close",
                status="rm_closeapp_fast_exit",
            )
            return True

        standby_sent = controller.standby_kef_end_session(self.default_reason, flags)
        controller._log_structured(
            "STEP",
            action="PROCESS_EXIT",
            reason=self.default_reason,
            step="request_self_close",
            status="wait_for_wm_endsession_or_system_teardown",
            endsession_standby_sent=standby_sent,
        )
        return False


@dataclass(frozen=True, slots=True)
class EndSessionTrigger:
    name: str = "end_session"
    default_reason: str = "WM_ENDSESSION"

    def fire(self, controller: Any, ending: bool, lparam: int) -> None:
        flags = decode_query_end_session_flags(lparam)
        controller._set_session_ending(ending)
        controller._log_structured(
            "EVENT",
            kind="WINDOW",
            name=self.default_reason,
            ending=ending,
            lparam=f"0x{lparam:08X}",
            flags=flags,
        )
        if ending:
            controller._new_generation("sleep", self.default_reason)
            controller._log_structured(
                "STEP",
                action="PROCESS_EXIT",
                reason=self.default_reason,
                step="fast_exit",
                status=("skip_standby_to_avoid_app_hang" if controller.config.fast_exit_on_endsession else "no_fast_exit"),
            )


QUERY_END_SESSION_TRIGGER = QueryEndSessionTrigger()
END_SESSION_TRIGGER = EndSessionTrigger()
