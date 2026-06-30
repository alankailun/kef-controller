# KEF Controller 1.6.2 - Faster Shutdown Path Cleanup

Release date: 2026-06-30

Baseline for this note: GitHub tag `v1.6.1` at `41bb3d8`.

This is a focused reliability and cleanup release for Windows power-event
handling. It keeps the 1.6.1 UI and packaging shape, then tightens shutdown and
manual event-test behavior so the app avoids slow standby fallbacks in
time-sensitive paths.

## Highlights

- **Faster shutdown standby.** `WM_QUERYENDSESSION` now sends standby
  synchronously through the bounded fast path, using the cached speaker IP only.
  It no longer waits for identity verification, IP recovery, action-lock waits,
  or the slower pykefcontrol standby fallback while Windows is shutting down.
- **Shutdown has a hard budget.** The end-session standby path now uses a
  `300 ms` deadline and treats a missing local route as best-effort success,
  matching the rest of the pre-suspend design.
- **Run Event now matches production power events.** The manual Lock, Lid Close,
  and Suspend event tests now dispatch through the same off-pump, deadline-based
  path used by the real Windows message pump instead of exercising an older
  inline path.
- **Removed the obsolete fast-standby standard fallback.** Fast standby paths no
  longer fall back to a slower standard standby request after bounded raw HTTP
  sending fails. This keeps time-sensitive paths fast and predictable.
- **Removed unused early-standby dedup state.** The disabled
  `EarlyStandbyState` scaffold and its no-op "recently confirmed" checks were
  deleted.
- **Removed obsolete tuning fields.** Configuration fields that no longer had
  runtime consumers were removed:
  `early_standby_dedup_window`,
  `early_standby_action_lock_timeout`,
  `suspend_fast_standby_action_lock_timeout`,
  `suspend_fast_standby_socket_timeout`,
  `endsession_standby_action_lock_timeout`, and
  `endsession_standby_socket_timeout`.

## Compatibility

- Existing `config.json` files remain compatible. Obsolete keys are ignored on
  load and disappear naturally the next time settings are saved.
- No speaker discovery, startup, UI layout, or installer-location changes from
  1.6.1.
- The shutdown path intentionally favors "send quickly, then let Windows
  continue" over slower confirmation work.

## Tests

- Power-event unit tests pass after the cleanup.
- Prewarmed standby socket tests pass after removing the obsolete early-standby
  state assertions.
- Full unit test suite and `compileall` were run before packaging.
- The 1.6.2 Windows installer was rebuilt with Inno Setup.
