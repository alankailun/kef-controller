# KEF Controller 1.9.1 - Safe Controller Cleanup

Release date: 2026-07-31

## Fixed

- Removed two unused fast-standby policy fields whose values no longer affected
  behavior.
- Made every standby enablement check name its configuration field explicitly;
  ordinary standby can no longer accidentally use the end-session setting.
- Bounded-standby abort logs now include the intended speaker IP address.
- A background power-action exception now emits a structured failed completion
  toast, so the Web power control clears its pending state immediately.
- Bound structured-log context now ignores a conflicting field and writes a
  warning instead of interrupting a wake or standby action.
- Corrected the startup wake log indentation.

## Verification

- Added regression coverage for bounded-abort target IP, safe bound-log field
  conflicts, and structured background power-action failures.
- Static analysis: Ruff passed.
- Full unit suite: 241 tests passed.
