# KEF Controller 1.9.2 - Unified Diagnostic Logging

Release date: 2026-07-31

## Better diagnostics

- STEP and SKIP records now write at `INFO` by default.  Skip reasons such as
  cooldowns, session shutdown, disabled standby rules, and lock timeouts are
  available during normal diagnosis instead of being permanently hidden.
- Added a persisted **Log verbosity** setting (`DEBUG`, `INFO`, `WARNING`, or
  `ERROR`) in Settings.  It applies to the running process immediately and is
  also used at the next launch.
- Removed redundant per-call `log_level="info"` overrides.  Warnings and
  errors now retain their authoritative Python severity.

## One log format

- All application logging now uses the same `TAG key=value` structured format,
  including the headless Windows event loop, device scanning, startup,
  persistence, WebView host, background tasks, and tray lifecycle.
- The stable envelope documents `action`, `gen`, `reason`, and `trigger`.
  `source` has been consolidated to `trigger` in controller identity and
  persistence paths; IP, MAC, and input changes now use consistent
  `previous_`, `actual_`, `target_`, and `requested_` field names.
- The live log view hides routine polling by exact `trigger` field comparison,
  replacing the previous arbitrary substring check.

## Guardrails

- Added AST-based tests that reject free-form application log calls, deprecated
  field aliases, and redundant INFO overrides, so future changes cannot drift
  away from the unified format.
- Verified with 244 automated tests and Ruff static checks.
