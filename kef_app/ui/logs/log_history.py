from __future__ import annotations

from collections import deque
import os
import re
from typing import Iterable

_UI_HIDDEN_LOG_TRIGGERS = frozenset(
    {
    "ui_home_poll",
    "ui_tray_poll",
    "web_ui_poll",
    }
)
_STRUCTURED_FIELD_RE = re.compile(r"(?:^|\s\|\s)([a-z_]+)=([^|]*)(?=\s\||$)")


def list_log_history_files(log_file: str) -> list[str]:
    """Return the current log followed by date-rotated files, newest first."""
    base_name = os.path.basename(log_file)
    log_dir = os.path.dirname(log_file)
    if not base_name:
        return []

    rotated_name = re.compile(rf"^{re.escape(base_name)}\.\d{{4}}-\d{{2}}-\d{{2}}$")
    try:
        archived = sorted(
            (name for name in os.listdir(log_dir) if rotated_name.fullmatch(name)),
            reverse=True,
        )
    except OSError:
        archived = []
    return [base_name, *archived]


def resolve_log_history_file(log_file: str, selected_name: str) -> str:
    """Resolve a UI-selected log name without allowing directory traversal."""
    selected = str(selected_name or "").strip()
    base_name = os.path.basename(log_file)
    if not selected or selected == base_name:
        return log_file
    if selected not in list_log_history_files(log_file):
        raise ValueError("Unknown log history file")
    return os.path.join(os.path.dirname(log_file), selected)


def should_hide_from_ui_log(line: str) -> bool:
    """Hide only structured, known-noisy poll records.

    The old implementation searched arbitrary text for a marker.  Now that
    every app emission has a field envelope, an exact trigger comparison
    cannot accidentally hide an unrelated diagnostic containing the same
    characters.
    """
    fields = {key: value.strip() for key, value in _STRUCTURED_FIELD_RE.findall(line or "")}
    return fields.get("trigger") in _UI_HIDDEN_LOG_TRIGGERS


def read_log_tail_lines(path: str, max_lines: int = 400, chunk_size: int = 4096) -> list[str]:
    if max_lines <= 0:
        return []

    try:
        with open(path, "rb") as file:
            file.seek(0, 2)
            file_size = file.tell()
            if file_size <= 0:
                return []

            data = bytearray()
            newline_count = 0
            position = file_size

            while position > 0 and newline_count <= max_lines:
                read_size = min(chunk_size, position)
                position -= read_size
                file.seek(position)
                chunk = file.read(read_size)
                data[:0] = chunk
                newline_count = data.count(b"\n")

        text = data.decode("utf-8", errors="ignore")
        return [line.rstrip("\r") for line in text.splitlines()[-max_lines:] if line.strip()]
    except OSError:
        return []


def merge_recent_lines(*line_sets: Iterable[str], max_lines: int = 400) -> list[str]:
    merged: deque[str] = deque(maxlen=max_lines)

    for lines in line_sets:
        normalized = [line for line in lines if line]
        if not normalized:
            continue

        existing = list(merged)
        max_overlap = min(len(existing), len(normalized))
        overlap = 0
        for size in range(max_overlap, 0, -1):
            if existing[-size:] == normalized[:size]:
                overlap = size
                break

        for line in normalized[overlap:]:
            merged.append(line)

    return list(merged)
