from __future__ import annotations

from collections import deque
from typing import Iterable

_UI_HIDDEN_LOG_MARKERS = ("ui_home_poll", "ui_tray_poll", "web_ui_poll")


def should_hide_from_ui_log(line: str) -> bool:
    text = line or ""
    return any(marker in text for marker in _UI_HIDDEN_LOG_MARKERS)


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
