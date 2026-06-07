# KEF Controller 1.4.0 - Bounded Standby Callbacks

Release date: 2026-06-02

Baseline for this note: GitHub tag `v1.3.0` at `d636f4b`.

This release closes a Modern Standby edge case found after 1.3.0. When Windows
locked the session, reported a lid-close event, or delivered `PBT_APMSUSPEND`, a
blocking network call could remain inside the hidden-window message callback
until the process resumed. The delayed call could then continue sending a
standby request that was already several minutes old.

## Compared With 1.3.0

- 1.3.0 removed the ineffective sleep-hold wrapper and kept standby requests
  immediate and best-effort.
- 1.4.0 adds a cached lock fast path backed by a two-socket prewarmed pool, so
  the common case can hand a standby packet to the kernel with less setup work.
- 1.4.0 treats early sends as unconfirmed and keeps the later
  `PBT_APMSUSPEND` resend available instead of inheriting an earlier
  route-loss result.
- 1.4.0 moves lock, lid-close, and system-suspend network work out of the
  Windows message pump and adds a strict event-anchored budget so a frozen
  process cannot resume an obsolete bounded standby request.

## Highlights

- **Power callbacks return quickly.** `WTS_SESSION_LOCK`,
  `GUID_LIDSWITCH_STATE_CHANGE=LidClosed`, and `PBT_APMSUSPEND` now schedule
  background workers instead of performing network I/O inside the Windows
  message pump.
- **Bounded standby has an event-anchored budget.** Lock, lid-close, and the
  default fast system-suspend worker get a `300 ms` absolute deadline measured
  from the original Windows event timestamp. Thread scheduling and queueing
  time count toward the same budget.
- **Expired standby requests stay expired.** Before each prewarmed send,
  fallback connect, and socket write, the app checks both the monotonic deadline
  and the current controller generation. A delayed worker cannot send after
  resume or after a quick unlock changes the desired state.
- **No unbounded standard fallback from bounded standby.** Lock, lid-close, and
  default fast system-suspend workers only use bounded fast paths. If an early
  path misses its budget, the later `PBT_APMSUSPEND` event can still perform its
  own best-effort resend.
- **Lightweight route preflight.** Before bounded sends, the app asks Windows
  for the best local interface to the configured speaker IP. It skips the send
  only when Windows explicitly reports that no route exists. Unknown results
  fail open, and the check does not send ping, ARP, or HTTP traffic.
- **Fallback retries stop after the gate closes.** If the first raw HTTP
  attempt returns after the deadline or generation changes, no additional
  worker attempts are created.
- **More precise timing evidence.** Deferred structured logging preserves the
  original event timestamps, and lock callback logs now report worker
  scheduling separately from standby execution.
- **Cleaner internal module names.** Fast standby helpers now live under
  `controller.standby`, low-level network scan helpers under `devices.scan`,
  and Windows startup helper modules no longer repeat the `startup_` prefix.
  These are import-only maintenance moves with no intended behavior change.

## Compatibility

- Existing configuration files remain compatible.
- No user-facing setting was added or removed.
- Startup, wake, end-session standby, and manual device controls keep their
  existing behavior.
- If `suspend_fast_standby_enabled` is explicitly disabled, the full verified
  suspend request is preserved but now runs off the Windows message pump.

## Tests

- Unit test suite passes: `144 tests`.
- Added regression coverage for lock, lid-close, and system-suspend background
  scheduling, event-anchored deadlines, stale-generation cancellation,
  route-preflight behavior, bounded fallback rules, and retry suppression after
  the send gate closes.
- Confirmed the maintenance renames with the same full unit suite after each
  rename group.

## Diff Size From GitHub v1.3.0

Compared with GitHub `v1.3.0` (`d636f4b`), the 1.4.0 tree changes roughly:

- `50` files
- `2449` insertions
- `566` deletions

Most of the change is standby-path hardening, diagnostics, regression test
coverage, and import-only module organization cleanup.
