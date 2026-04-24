from __future__ import annotations

import logging
import threading
import traceback
from typing import Callable, Optional, TypeVar


T = TypeVar("T")


def start_background_task(
    name: str,
    work: Callable[[], T],
    *,
    on_success: Optional[Callable[[T], None]] = None,
    on_error: Optional[Callable[[Exception], None]] = None,
    on_finished: Optional[Callable[[], None]] = None,
    lock: Optional[threading.Lock] = None,
    log: Optional[logging.Logger] = None,
) -> Optional[threading.Thread]:
    if lock is not None and not lock.acquire(blocking=False):
        return None

    def log_callback_failure(callback_name: str) -> None:
        if log is not None:
            log.error("%s %s callback failed:\n%s", name, callback_name, traceback.format_exc())

    def guarded() -> None:
        try:
            result = work()
        except Exception as exc:
            if log is not None:
                log.error("%s failed:\n%s", name, traceback.format_exc())
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
