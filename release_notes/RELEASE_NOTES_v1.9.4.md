# KEF Controller 1.9.4 - Quiet, Useful Offline Diagnostics

Release date: 2026-08-01

## Offline diagnostic signal

- UI polling now records routine `STEP` and `SKIP` diagnostics at `DEBUG`.
  This prevents repeated recovery prerequisites from displacing the initial
  failure and startup records in the 800-line UI log buffer.
- Warnings and recovery `BEGIN`/`END` records remain visible at the default
  `INFO` level, so a disconnected speaker still shows its failure and recovery
  attempts.
- Identity-probe failures now log only on the first failure and when the
  configured offline threshold is crossed.

## Prewarmed standby diagnostics

- Restored keepalive failure throttling: failures 1, 3, 6, 9, and so on are
  visible at `INFO`; intervening retries are retained at `DEBUG`.

## Verification

- Added regression tests for UI-poll `SKIP` severity, identity-failure edge
  logging, and prewarmed keepalive throttling.
