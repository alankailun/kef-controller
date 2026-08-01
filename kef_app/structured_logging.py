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
from collections.abc import Mapping
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


def coerce_log_level(level: object, *, default: int = logging.INFO) -> int:
    """Return a valid logging level, falling back to ``default``."""
    if isinstance(level, int):
        return level
    normalized = str(level or "").strip().upper()
    resolved = getattr(logging, normalized, None)
    return resolved if isinstance(resolved, int) else default


def structured_log_level(tag: str) -> int:
    """Return the default severity for a structured tag.

    STEP and SKIP are deliberately INFO-level diagnostics.  A skipped branch
    often explains why a requested speaker action did not happen, so hiding it
    behind DEBUG leaves field diagnosis incomplete.
    """
    return _TAG_LOG_LEVELS.get(str(tag or "").upper(), logging.INFO)


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
    **fields: object,
) -> None:
    """Emit one universally formatted application log line.

    ``mono`` is filled at the emission point unless a caller supplies the
    event timestamp explicitly.  Controller actions use their own clock and
    delegate only formatting and severity policy to this module.
    """
    level = coerce_log_level(log_level, default=structured_log_level(tag)) if log_level is not None else structured_log_level(tag)
    if not log.isEnabledFor(level):
        return
    if mono is not None:
        fields["mono"] = mono
    elif "mono" not in fields:
        fields["mono"] = f"{time.monotonic():.3f}"
    log.log(level, format_structured_message(tag, fields))
