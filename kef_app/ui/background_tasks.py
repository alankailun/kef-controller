from __future__ import annotations

import logging
import threading
import traceback
from collections.abc import Callable
from typing import TypeVar

from ..structured_logging import log_structured

T = TypeVar("T")


def start_background_task(
    name: str,
    work: Callable[[], T],
    *,
    on_success: Callable[[T], None] | None = None,
    on_error: Callable[[Exception], None] | None = None,
    on_finished: Callable[[], None] | None = None,
    lock: threading.Lock | None = None,
    log: logging.Logger | None = None,
) -> threading.Thread | None:
    if lock is not None and not lock.acquire(blocking=False):
        return None

    def log_callback_failure(callback_name: str) -> None:
        if log is not None:
            log_structured(
                log,
                "ERROR",
                action="BACKGROUND_TASK",
                trigger=name,
                cause="callback_failed",
                callback=callback_name,
                error=traceback.format_exc(),
            )

    def guarded() -> None:
        try:
            result = work()
        except Exception as exc:
            if log is not None:
                log_structured(
                    log,
                    "ERROR",
                    action="BACKGROUND_TASK",
                    trigger=name,
                    cause="task_failed",
                    error=traceback.format_exc(),
                )
            if on_error is not None:
                try:
                    on_error(exc)
                except Exception:
                    log_callback_failure("error")
        else:
            if on_success is not None:
                try:
                    on_success(result)
                except Exception:
                    log_callback_failure("success")
        finally:
            if lock is not None:
                lock.release()
            if on_finished is not None:
                try:
                    on_finished()
                except Exception:
                    log_callback_failure("finished")

    thread = threading.Thread(target=guarded, daemon=True, name=name)
    thread.start()
    return thread
