from __future__ import annotations

import socket
import time
from typing import Optional

from pykefcontrol.kef_connector import KefConnector

from .common import temporary_socket_timeout
from ..models import normalize_input_source


class ControllerDeviceActionsMixin:
    def _verify_player_source(self, expected_input: str) -> tuple[Optional[str], bool]:
        actual_player_source = self.get_player_source_hint(fresh=True)
        return actual_player_source, (not actual_player_source or actual_player_source == expected_input)

    def get_player_source_hint(self, fresh: bool = False) -> Optional[str]:
        if not self.get_current_kef_ip():
            return None
        try:
            with temporary_socket_timeout(self.config.socket_timeout):
                speaker = self.get_speaker(fresh=fresh)
                player_data = speaker._get_player_data()
        except Exception as exc:
            self.reset_speaker()
            self._log_structured("WARN", action="GET_PLAYER_SOURCE", error=repr(exc), mono=f"{self.mono():.3f}")
            return None

        candidates = [
            player_data.get("mediaRoles", {}).get("mediaData", {}).get("metaData", {}).get("serviceID"),
            player_data.get("trackRoles", {}).get("mediaData", {}).get("metaData", {}).get("serviceID"),
            player_data.get("mediaRoles", {}).get("title"),
            player_data.get("trackRoles", {}).get("title"),
            player_data.get("mediaRoles", {}).get("path"),
            player_data.get("trackRoles", {}).get("path"),
        ]
        for candidate in candidates:
            normalized = normalize_input_source(str(candidate or ""))
            if normalized:
                return normalized
        return None

    def get_input_source(self, fresh: bool = False) -> Optional[str]:
        if not self.get_current_kef_ip():
            return None
        try:
            with temporary_socket_timeout(self.config.socket_timeout):
                speaker = self.get_speaker(fresh=fresh)
                return normalize_input_source(speaker.source)
        except Exception as exc:
            self.reset_speaker()
            self._log_structured("WARN", action="GET_INPUT_SOURCE", error=repr(exc), mono=f"{self.mono():.3f}")
            return None

    def _wait_for_input_source(self, expected_input: str, timeout: float = 2.5) -> Optional[str]:
        expected = normalize_input_source(expected_input)
        deadline = self.mono() + timeout
        observed = None
        while self.mono() < deadline:
            observed = self.get_input_source(fresh=True)
            if observed == expected:
                return observed
            time.sleep(0.2)
        return observed

    def reset_speaker(self):
        with self._speaker_lock:
            self._speaker = None

    def change_input_live(self, new_input: str) -> bool:
        requested_input = new_input
        new_input = normalize_input_source(new_input)
        previous_input = self.get_input_source()
        previous_player_source = self.get_player_source_hint()
        if not self.get_current_kef_ip():
            return False
        if not self._action_lock.acquire(timeout=2.0):
            self._log_structured("SKIP", action="CHANGE_INPUT", cause="action_lock_busy", mono=f"{self.mono():.3f}")
            return False
        try:
            for attempt in range(1, 3):
                if attempt > 1:
                    time.sleep(0.6)
                try:
                    with temporary_socket_timeout(self.config.socket_timeout):
                        speaker = self.get_speaker(fresh=True)
                        speaker.source = new_input
                    actual_input = self._wait_for_input_source(new_input)
                    if actual_input != new_input:
                        self._log_structured(
                            "WARN",
                            action="CHANGE_INPUT",
                            requested_input=requested_input,
                            normalized_input=new_input,
                            attempt=attempt,
                            cause="source_not_verified",
                            actual_input=actual_input or "<unknown>",
                            mono=f"{self.mono():.3f}",
                        )
                        self.reset_speaker()
                        continue
                    actual_player_source, player_ok = self._verify_player_source(new_input)
                    if not player_ok:
                        self._log_structured(
                            "WARN",
                            action="CHANGE_INPUT",
                            requested_input=requested_input,
                            normalized_input=new_input,
                            attempt=attempt,
                            cause="player_source_not_verified",
                            actual_input=actual_input or "<unknown>",
                            actual_player_source=actual_player_source,
                            mono=f"{self.mono():.3f}",
                        )
                        self.reset_speaker()
                        continue
                    self._log_structured(
                        "STEP",
                        action="CHANGE_INPUT",
                        requested_input=requested_input,
                        new_input=new_input,
                        previous_input=previous_input or "<unknown>",
                        previous_player_source=previous_player_source or "<unknown>",
                        attempt=attempt,
                        status="success",
                        actual_input=actual_input,
                        actual_player_source=actual_player_source or "<unknown>",
                        mono=f"{self.mono():.3f}",
                    )
                    return True
                except Exception as exc:
                    self.reset_speaker()
                    self._log_structured(
                        "WARN",
                        action="CHANGE_INPUT",
                        requested_input=requested_input,
                        new_input=new_input,
                        attempt=attempt,
                        error=repr(exc),
                        mono=f"{self.mono():.3f}",
                    )
            return False
        finally:
            self._action_lock.release()

    def get_volume(self) -> Optional[int]:
        if not self.get_current_kef_ip():
            return None
        try:
            with temporary_socket_timeout(self.config.socket_timeout):
                speaker = self.get_speaker(fresh=False)
                return speaker.volume
        except Exception as exc:
            self.reset_speaker()
            self._log_structured("WARN", action="GET_VOLUME", error=repr(exc), mono=f"{self.mono():.3f}")
            return None

    def set_volume(self, level: int) -> bool:
        if not self.get_current_kef_ip():
            return False
        level = max(0, min(100, level))
        if not self._action_lock.acquire(timeout=2.0):
            self._log_structured("SKIP", action="SET_VOLUME", cause="action_lock_busy", mono=f"{self.mono():.3f}")
            return False
        try:
            with temporary_socket_timeout(self.config.socket_timeout):
                speaker = self.get_speaker(fresh=False)
                speaker.volume = level
            self._log_structured("STEP", action="SET_VOLUME", level=level, status="success", mono=f"{self.mono():.3f}")
            return True
        except Exception as exc:
            self.reset_speaker()
            self._log_structured("WARN", action="SET_VOLUME", level=level, error=repr(exc), mono=f"{self.mono():.3f}")
            return False
        finally:
            self._action_lock.release()

    def get_speaker(self, fresh: bool = False) -> KefConnector:
        with self._speaker_lock:
            if fresh or self._speaker is None:
                self._speaker = self._backend.create_connector(self.get_current_kef_ip())
            return self._speaker

    def wait_until_reachable(self, timeout: float, generation: int, reason: str) -> bool:
        start_mono = self.mono()
        deadline = start_mono + timeout
        waiting_logged = False
        last_error = None
        self._log_structured(
            "STEP",
            action="WAKE",
            gen=generation,
            reason=reason,
            step="wait_reachable",
            status="begin",
            timeout_s=f"{timeout:.2f}",
            target_ip=self.get_current_kef_ip(),
            mono=f"{start_mono:.3f}",
        )

        while True:
            if self._should_abort_generation(generation):
                self._log_structured(
                    "ABORT",
                    action="WAKE",
                    gen=generation,
                    reason=reason,
                    step="wait_reachable",
                    current_gen=self._current_generation(),
                    cause="generation_changed",
                    mono=f"{self.mono():.3f}",
                )
                return False

            remaining = deadline - self.mono()
            if remaining <= 0:
                refreshed = self.maybe_refresh_kef_ip(reason=reason, trigger="wait_reachable_timeout")
                self._log_structured(
                    "STEP",
                    action="WAKE",
                    gen=generation,
                    reason=reason,
                    step="wait_reachable",
                    status="timeout_continue",
                    elapsed_ms=int((self.mono() - start_mono) * 1000),
                    last_error=last_error,
                    ip_refresh_attempted=refreshed,
                    target_ip=self.get_current_kef_ip(),
                    mono=f"{self.mono():.3f}",
                )
                return True

            try:
                target_ip = self.get_current_kef_ip()
                with socket.create_connection((target_ip, 80), timeout=min(0.5, remaining)):
                    self._log_structured(
                        "STEP",
                        action="WAKE",
                        gen=generation,
                        reason=reason,
                        step="wait_reachable",
                        status="reachable",
                        elapsed_ms=int((self.mono() - start_mono) * 1000),
                        target_ip=target_ip,
                        mono=f"{self.mono():.3f}",
                    )
                    return True
            except OSError as exc:
                last_error = str(exc)
                if not waiting_logged:
                    waiting_logged = True
                    self._log_structured(
                        "STEP",
                        action="WAKE",
                        gen=generation,
                        reason=reason,
                        step="wait_reachable",
                        status="waiting",
                        poll_s=f"{self.config.reachability_poll_interval:.2f}",
                        last_error=last_error,
                        target_ip=self.get_current_kef_ip(),
                        mono=f"{self.mono():.3f}",
                    )
                if not self._interruptible_sleep(min(self.config.reachability_poll_interval, remaining), generation, "wait_reachable"):
                    return False

    def wake_kef(self, generation: int, reason: str) -> bool:
        outcome = "unknown"
        start_mono = self._log_action_begin("WAKE", generation, reason)
        c = self.config
        target_input = normalize_input_source(c.kef_input)

        try:
            if self._is_session_ending():
                outcome = "skipped_session_ending"
                self._log_structured(
                    "SKIP",
                    action="WAKE",
                    gen=generation,
                    reason=reason,
                    cause="session_ending",
                    mono=f"{self.mono():.3f}",
                )
                return False

            self._log_structured(
                "STEP",
                action="WAKE",
                gen=generation,
                reason=reason,
                step="set_input_source",
                input=target_input,
                target_ip=self.get_current_kef_ip(),
                mono=f"{self.mono():.3f}",
            )

            if not self.wait_until_reachable(c.reachability_wait_timeout, generation, reason):
                outcome = "aborted_before_attempts"
                return False

            for attempt, delay in enumerate(c.wake_attempt_delays, start=1):
                if delay > 0:
                    self._log_structured(
                        "STEP",
                        action="WAKE",
                        gen=generation,
                        reason=reason,
                        step="pre_delay",
                        attempt=attempt,
                        delay_s=f"{delay:.2f}",
                        mono=f"{self.mono():.3f}",
                    )
                    if not self._interruptible_sleep(delay, generation, f"wake_pre_delay_attempt_{attempt}"):
                        outcome = "aborted_during_pre_delay"
                        return False

                if self._should_abort_generation(generation):
                    outcome = "aborted_stale_generation_before_lock"
                    self._log_structured(
                        "ABORT",
                        action="WAKE",
                        gen=generation,
                        reason=reason,
                        step="before_action_lock",
                        current_gen=self._current_generation(),
                        cause="generation_changed",
                        mono=f"{self.mono():.3f}",
                    )
                    return False

                if not self._acquire_action_lock_interruptibly(c.wake_action_lock_timeout, generation, reason, "wake"):
                    outcome = "aborted_action_lock_timeout_or_stale"
                    return False

                try:
                    if self._should_abort_generation(generation):
                        outcome = "aborted_stale_generation_after_lock"
                        self._log_structured(
                            "ABORT",
                            action="WAKE",
                            gen=generation,
                            reason=reason,
                            step="after_action_lock",
                            current_gen=self._current_generation(),
                            cause="generation_changed",
                            mono=f"{self.mono():.3f}",
                        )
                        return False

                    with temporary_socket_timeout(c.socket_timeout):
                        speaker = self.get_speaker(fresh=True)
                        if target_input:
                            speaker.source = target_input

                    self._clear_recent_lock_standby_marker()
                    self.capture_identity_from_current_ip(reason=reason, trigger=f"wake_success_attempt_{attempt}")
                    outcome = f"success_attempt_{attempt}"
                    self._log_structured(
                        "STEP",
                        action="WAKE",
                        gen=generation,
                        reason=reason,
                        step="set_input_source",
                        attempt=attempt,
                        status="success",
                        mono=f"{self.mono():.3f}",
                    )
                    return True
                except Exception as exc:
                    self.reset_speaker()
                    refreshed = self.maybe_refresh_kef_ip(reason=reason, trigger=f"wake_attempt_{attempt}_exception")
                    self._log_structured(
                        "RETRY",
                        action="WAKE",
                        gen=generation,
                        reason=reason,
                        attempt=attempt,
                        cause="set_input_source_failed",
                        error=repr(exc),
                        ip_refresh_attempted=refreshed,
                        target_ip=self.get_current_kef_ip(),
                        mono=f"{self.mono():.3f}",
                    )
                finally:
                    self._action_lock.release()

            outcome = "failed_all_attempts"
            self._log_structured(
                "STEP",
                action="WAKE",
                gen=generation,
                reason=reason,
                step="attempt_loop",
                status="exhausted",
                mono=f"{self.mono():.3f}",
            )
            return False
        finally:
            self._log_action_end("WAKE", generation, reason, outcome, start_mono)

    def standby_kef_preemptive(self, reason: str) -> bool:
        outcome = "unknown"
        start_mono = self._log_action_begin("LOCK_PRE_STANDBY", None, reason)

        try:
            self._log_structured("STEP", action="LOCK_PRE_STANDBY", reason=reason, step="shutdown_request", mono=f"{self.mono():.3f}")
            if self._is_session_ending():
                outcome = "skipped_session_ending"
                self._log_structured(
                    "SKIP",
                    action="LOCK_PRE_STANDBY",
                    reason=reason,
                    cause="session_ending",
                    mono=f"{self.mono():.3f}",
                )
                return False

            if not self._action_lock.acquire(timeout=self.config.lock_standby_action_lock_timeout):
                outcome = "skipped_action_lock_busy"
                self._log_structured(
                    "SKIP",
                    action="LOCK_PRE_STANDBY",
                    reason=reason,
                    cause="action_lock_busy",
                    timeout_s=f"{self.config.lock_standby_action_lock_timeout:.2f}",
                    mono=f"{self.mono():.3f}",
                )
                return False

            try:
                with temporary_socket_timeout(self.config.socket_timeout):
                    speaker = self.get_speaker(fresh=False)
                    speaker.shutdown()
                self._mark_lock_prestandby_success()
                outcome = "success"
                self._log_structured(
                    "STEP",
                    action="LOCK_PRE_STANDBY",
                    reason=reason,
                    step="shutdown_request",
                    status="success",
                    mono=f"{self.mono():.3f}",
                )
                return True
            except Exception as exc:
                self.reset_speaker()
                outcome = "failed_will_fallback_to_apmsuspend"
                self._log_structured(
                    "RETRY",
                    action="LOCK_PRE_STANDBY",
                    reason=reason,
                    attempt=1,
                    cause="shutdown_failed",
                    error=repr(exc),
                    mono=f"{self.mono():.3f}",
                )
                return False
            finally:
                self._action_lock.release()
        finally:
            self._log_action_end("LOCK_PRE_STANDBY", None, reason, outcome, start_mono)

    def standby_kef_end_session(self, reason: str, flags: str) -> bool:
        outcome = "unknown"
        start_mono = self._log_action_begin("ENDSESSION_STANDBY", None, reason)

        try:
            if not self.config.endsession_standby_on_shutdown:
                outcome = "disabled"
                self._log_structured(
                    "SKIP",
                    action="ENDSESSION_STANDBY",
                    reason=reason,
                    cause="disabled",
                    flags=flags,
                    mono=f"{self.mono():.3f}",
                )
                return False

            current_ip = self.get_current_kef_ip()
            if not current_ip:
                outcome = "skipped_no_current_ip"
                self._log_structured(
                    "SKIP",
                    action="ENDSESSION_STANDBY",
                    reason=reason,
                    cause="no_current_ip",
                    flags=flags,
                    mono=f"{self.mono():.3f}",
                )
                return False

            if not self._action_lock.acquire(timeout=self.config.endsession_standby_action_lock_timeout):
                outcome = "skipped_action_lock_busy"
                self._log_structured(
                    "SKIP",
                    action="ENDSESSION_STANDBY",
                    reason=reason,
                    cause="action_lock_busy",
                    flags=flags,
                    timeout_s=f"{self.config.endsession_standby_action_lock_timeout:.2f}",
                    mono=f"{self.mono():.3f}",
                )
                return False

            try:
                with temporary_socket_timeout(self.config.endsession_standby_socket_timeout):
                    speaker = self.get_speaker(fresh=False)
                    speaker.shutdown()

                outcome = "success"
                self._log_structured(
                    "STEP",
                    action="ENDSESSION_STANDBY",
                    reason=reason,
                    step="shutdown_request",
                    status="success",
                    flags=flags,
                    target_ip=current_ip,
                    mono=f"{self.mono():.3f}",
                )
                return True
            except Exception as exc:
                self.reset_speaker()
                outcome = "failed"
                self._log_structured(
                    "RETRY",
                    action="ENDSESSION_STANDBY",
                    reason=reason,
                    attempt=1,
                    cause="shutdown_failed",
                    flags=flags,
                    error=repr(exc),
                    target_ip=current_ip,
                    mono=f"{self.mono():.3f}",
                )
                return False
            finally:
                self._action_lock.release()
        finally:
            self._log_action_end("ENDSESSION_STANDBY", None, reason, outcome, start_mono)

    def _recently_lock_standby_ok(self) -> bool:
        with self._state_lock:
            last = self._last_lock_standby_ok_mono
            if last <= 0:
                return False
            return (self.mono() - last) < self.config.lock_standby_dedup_window

    def standby_kef(self, generation: int, reason: str) -> bool:
        outcome = "unknown"
        start_mono = self._log_action_begin("STANDBY", generation, reason)
        c = self.config

        try:
            self._log_structured("STEP", action="STANDBY", gen=generation, reason=reason, step="shutdown_request", mono=f"{self.mono():.3f}")
            if self._recently_lock_standby_ok():
                outcome = "skipped_recent_lock_pre_standby_ok"
                self._log_structured(
                    "SKIP",
                    action="STANDBY",
                    gen=generation,
                    reason=reason,
                    cause="recent_lock_pre_standby_ok",
                    window_s=f"{c.lock_standby_dedup_window:.2f}",
                    mono=f"{self.mono():.3f}",
                )
                return True

            if self._is_session_ending():
                outcome = "skipped_session_ending"
                self._log_structured(
                    "SKIP",
                    action="STANDBY",
                    gen=generation,
                    reason=reason,
                    cause="session_ending",
                    mono=f"{self.mono():.3f}",
                )
                return False

            for attempt, delay in enumerate(c.standby_attempt_delays, start=1):
                if delay > 0:
                    self._log_structured(
                        "STEP",
                        action="STANDBY",
                        gen=generation,
                        reason=reason,
                        step="pre_delay",
                        attempt=attempt,
                        delay_s=f"{delay:.2f}",
                        mono=f"{self.mono():.3f}",
                    )
                    if not self._interruptible_sleep(delay, generation, f"standby_pre_delay_attempt_{attempt}"):
                        outcome = "aborted_during_pre_delay"
                        return False

                if self._should_abort_generation(generation):
                    outcome = "aborted_stale_generation_before_lock"
                    self._log_structured(
                        "ABORT",
                        action="STANDBY",
                        gen=generation,
                        reason=reason,
                        step="before_action_lock",
                        current_gen=self._current_generation(),
                        cause="generation_changed",
                        mono=f"{self.mono():.3f}",
                    )
                    return False

                if not self._acquire_action_lock_interruptibly(c.suspend_action_lock_timeout, generation, reason, "standby"):
                    outcome = "aborted_action_lock_timeout_or_stale"
                    return False

                try:
                    if self._should_abort_generation(generation):
                        outcome = "aborted_stale_generation_after_lock"
                        self._log_structured(
                            "ABORT",
                            action="STANDBY",
                            gen=generation,
                            reason=reason,
                            step="after_action_lock",
                            current_gen=self._current_generation(),
                            cause="generation_changed",
                            mono=f"{self.mono():.3f}",
                        )
                        return False

                    fresh = attempt > 1
                    with temporary_socket_timeout(c.socket_timeout):
                        speaker = self.get_speaker(fresh=fresh)
                        speaker.shutdown()

                    outcome = f"success_attempt_{attempt}"
                    self._log_structured(
                        "STEP",
                        action="STANDBY",
                        gen=generation,
                        reason=reason,
                        step="shutdown_request",
                        attempt=attempt,
                        fresh=fresh,
                        status="success",
                        mono=f"{self.mono():.3f}",
                    )
                    return True
                except Exception as exc:
                    self.reset_speaker()
                    self._log_structured(
                        "RETRY",
                        action="STANDBY",
                        gen=generation,
                        reason=reason,
                        attempt=attempt,
                        fresh=(attempt > 1),
                        cause="shutdown_failed",
                        error=repr(exc),
                        mono=f"{self.mono():.3f}",
                    )
                finally:
                    self._action_lock.release()

            outcome = "failed_all_attempts"
            self._log_structured(
                "STEP",
                action="STANDBY",
                gen=generation,
                reason=reason,
                step="attempt_loop",
                status="exhausted",
                mono=f"{self.mono():.3f}",
            )
            return False
        finally:
            self._log_action_end("STANDBY", generation, reason, outcome, start_mono)
