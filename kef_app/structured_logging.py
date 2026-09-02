"""Shared structured-log primitives.

Every application log line is emitted as ``TAG key=value | ...``.  The file
formatter adds the timestamp, thread, and authoritative Python log level.
Keeping this small formatter outside the controller lets runtime, discovery,
and UI-host code use the same wire format without depending on controller
internals.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from typing import Final

STRUCTURED_LOG_TAGS: Final[frozenset[str]] = frozenset(
    {"ABORT", "BEGIN", "END", "ERROR", "EVENT", "INFO", "RETRY", "SKIP", "STATE", "STEP", "WARN"}
)

_TAG_LOG_LEVELS: Final[dict[str, int]] = {
    "ABORT": logging.WARNING,
    "ERROR": logging.ERROR,
    "RETRY": logging.WARNING,
    "WARN": logging.WARNING,
}
_UI_POLL_TRIGGER_PREFIXES: Final[tuple[str, ...]] = (
    "ui_home_poll",
    "ui_tray_poll",
    "web_ui_poll",
)
# A UI poll may escalate into an offline warning or a recovery operation. Its
# repeated progress and "not run this time" diagnostics are noise at INFO.
_UI_POLL_DEMOTABLE_TAGS: Final[frozenset[str]] = frozenset({"STEP", "SKIP"})


def coerce_log_level(level: object, *, default: int = logging.INFO) -> int:
    """Return a valid logging level, falling back to ``default``."""
    if isinstance(level, int):
        return level
    normalized = str(level or "").strip().upper()
    resolved = getattr(logging, normalized, None)
    return resolved if isinstance(resolved, int) else default


def is_ui_poll_trigger(trigger: object) -> bool:
    """Return whether ``trigger`` belongs to a high-frequency UI poll."""
    return str(trigger or "").startswith(_UI_POLL_TRIGGER_PREFIXES)


def structured_log_level(tag: str, fields: Mapping[str, object] | None = None) -> int:
    """Return the default severity for a structured tag.

    STEP and SKIP are deliberately INFO-level diagnostics.  A skipped branch
    often explains why a requested speaker action did not happen, so hiding it
    behind DEBUG leaves field diagnosis incomplete.

    Routine UI-poll steps and skipped branches are the exception: they are
    frequent background observations, not user actions.  Warnings and
    recovery boundaries retain their normal severity so an offline speaker
    remains diagnosable at the default INFO verbosity.
    """
    normalized_tag = str(tag or "").upper()
    if (
        fields is not None
        and normalized_tag in _UI_POLL_DEMOTABLE_TAGS
        and is_ui_poll_trigger(fields.get("trigger"))
    ):
        return logging.DEBUG
    return _TAG_LOG_LEVELS.get(normalized_tag, logging.INFO)


def format_structured_message(tag: str, fields: Mapping[str, object]) -> str:
    normalized_tag = str(tag or "INFO").upper()
    if normalized_tag not in STRUCTURED_LOG_TAGS:
        raise ValueError(f"Unsupported structured log tag: {tag!r}")
    parts = [f"{key}={value}" for key, value in fields.items() if value is not None]
    return f"{normalized_tag} " + " | ".join(parts) if parts else normalized_tag


def log_structured(
    log: logging.Logger,
    tag: str,
    *,
    log_level: object = None,
    mono: object = None,
    monotonic: Callable[[], float] | None = None,
    **fields: object,
) -> None:
    """Emit one universally formatted application log line.

    ``mono`` is filled at the emission point unless a caller supplies the
    event timestamp explicitly. Controllers can inject their own monotonic
    clock while sharing the same formatting and severity policy.
    """
    level = (
        coerce_log_level(log_level, default=structured_log_level(tag, fields))
        if log_level is not None
        else structured_log_level(tag, fields)
    )
    if not log.isEnabledFor(level):
        return
    if mono is not None:
        fields["mono"] = mono
    elif "mono" not in fields:
        clock = monotonic or time.monotonic
        fields["mono"] = f"{clock():.3f}"
    log.log(level, format_structured_message(tag, fields))
