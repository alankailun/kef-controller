from __future__ import annotations

import re


_HOST_UNREACHABLE_CODES = {10065, 10051, 113, 101}
_HOST_UNREACHABLE_MARKERS = (
    "winerror 10065",
    "wsaehostunreach",
    "winerror 10051",
    "wsaenetunreach",
    "errno 10065",
    "errno 10051",
    "errno 113",
    "ehostunreach",
    "errno 101",
    "enetunreach",
    "network is unreachable",
    "no route to host",
    "unreachable host",
)
_HOST_UNREACHABLE_PATTERN = re.compile("|".join(re.escape(marker) for marker in _HOST_UNREACHABLE_MARKERS))


def is_host_unreachable(exc: BaseException) -> bool:
    seen: set[int] = set()
    pending: list[BaseException] = [exc]

    while pending:
        current = pending.pop()
        current_id = id(current)
        if current_id in seen:
            continue
        seen.add(current_id)

        errno_value = getattr(current, "errno", None)
        winerror_value = getattr(current, "winerror", None)
        if errno_value in _HOST_UNREACHABLE_CODES or winerror_value in _HOST_UNREACHABLE_CODES:
            return True

        text = f"{type(current).__name__}: {current!r} {current}".casefold()
        if _HOST_UNREACHABLE_PATTERN.search(text):
            return True

        cause = getattr(current, "__cause__", None)
        context = getattr(current, "__context__", None)
        if isinstance(cause, BaseException):
            pending.append(cause)
        if isinstance(context, BaseException):
            pending.append(context)

    return False
