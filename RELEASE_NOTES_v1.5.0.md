# KEF Controller 1.5.0 - Async Hot-Path Logging

Release date: 2026-06-02

Baseline for this note: GitHub tag `v1.4.0` at `b5b4768`.

This release finishes the hot-path hardening work started in 1.4.0. The project
rule is now explicit: Windows event handlers and pre-suspend standby windows
must not perform unbounded blocking I/O, including network, disk, or unbounded
lock waits.

## Highlights

- **Logging is async by default.** Runtime logging now uses
  `QueueHandler`/`QueueListener`, so log calls enqueue records in memory and a
  background listener writes file, console, and UI log handlers.
- **Shutdown still drains logs.** `shutdown_logger()` stops the listener,
  flushes queued records, and closes underlying handlers during normal exit.
- **Windows standby dispatch is unified.** Lock, lid-close, and suspend standby
  events now share one `dispatch_off_pump_standby()` shape: capture event mono,
  dispatch bounded worker, then return.
- **Slow pump callbacks now announce themselves.** If a standby-related Windows
  callback takes more than `20 ms`, the app logs
  `WARN ... step=pump_callback_slow`.
- **Old defer-log workaround removed.** The structured-log defer buffer is gone
  because async logging addresses the root cause rather than wrapping individual
  call sites.
- **Hot-path invariant documented.** `KefPowerController` now records the rule:
  no synchronous network, no synchronous disk, and no unbounded lock waits in
  Windows message callbacks or pre-suspend paths.
- **Maintenance cleanup from post-1.4.0 main is included.** Standby helpers are
  grouped under `controller.standby`, scan helpers under `devices.scan`, startup
  helper module names are shorter, and bounded standby send code is simplified.

## Compatibility

- Existing configuration and state files remain compatible.
- No user-facing setting was added or removed.
- Bounded log draining before suspend is intentionally not enabled yet. The
  default is fully async logging; if suspend-edge logs are observed missing in
  practice, a small bounded drain can be added later.

## Tests

- Unit test suite passes: `146 tests`.
- Added coverage for queued runtime logging, listener shutdown flush, unified
  standby dispatch, and `pump_callback_slow` warnings.
