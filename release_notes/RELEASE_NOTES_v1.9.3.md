# KEF Controller 1.9.3 - Secure, Focused Diagnostics

Release date: 2026-07-31

## Security

- Removed the authenticated WebView URL from application logs. Host launch
  records now contain only the loopback origin and `auth=token`.
- The per-process API token is no longer placed on the WebView host command
  line. It is passed in a private environment variable that the host removes
  before starting WebView2.

## Useful DEBUG diagnostics

- Records whose `trigger` belongs to a home, tray, or web UI poll now emit at
  `DEBUG`. At the default `INFO` level they do not reach disk or the log view;
  selecting `DEBUG` exposes the complete polling path for diagnosis.
- Removed the duplicate UI-only poll filtering path. Log visibility is now
  controlled by the logger severity at the source.

## Interface

- UI animations now remain enabled regardless of the operating system's
  reduced-motion preference.

## Final field vocabulary cleanup

- `current_ip` now always represents the resolved speaker address. `target_ip`
  is reserved for configured or cached targets. Bare `ip` is rejected by the
  structured-log contract.
- Cooldown elapsed time now uses `elapsed_ms`; all actual elapsed-time fields
  use milliseconds, while configured or future wait intervals retain `_s`.
- Fast standby snapshots the current IP and effective MAC under one lock before
  emitting its time-sensitive diagnostics.

## Verification

- Added regression tests for token redaction, command-line handling, DEBUG poll
  visibility, and IP/elapsed-field guardrails.
- Verified with 246 automated tests and Ruff static checks.
