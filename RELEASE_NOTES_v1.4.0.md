# KEF Controller 1.4.0 - Bounded Early Standby

Release date: 2026-06-02

Baseline for this note: GitHub tag `v1.3.0` at `d636f4b`.

This release closes a Modern Standby edge case found after 1.3.0. When Windows
locked the session or reported a lid-close event immediately before sleep, a
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
- 1.4.0 moves lock and lid-close network work out of the Windows message pump
  and adds a strict event-anchored budget so a frozen process cannot resume an
  obsolete standby request.

## Highlights

- **Lock and lid-close callbacks return quickly.** `WTS_SESSION_LOCK` and
  `GUID_LIDSWITCH_STATE_CHANGE=LidClosed` now schedule a shared background
  worker instead of performing network I/O inside the Windows message pump.
- **Early standby has an event-anchored budget.** The worker gets a `300 ms`
  absolute deadline measured from the original Windows event timestamp. Thread
  scheduling and queueing time count toward the same budget.
- **Expired standby requests stay expired.** Before each prewarmed send,
  fallback connect, and socket write, the app checks both the monotonic deadline
  and the current controller generation. A delayed worker cannot send after
  resume or after a quick unlock changes the desired state.
- **No unbounded standard fallback from early standby.** Lock and lid-close
  workers only use bounded fast paths. If those paths miss their budget, the
  later `PBT_APMSUSPEND` event can still perform its normal best-effort resend.
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

## Compatibility

- Existing configuration files remain compatible.
- No user-facing setting was added or removed.
- Startup, wake, suspend resend, end-session standby, and manual device controls
  keep their existing behavior.

## Tests

- Unit test suite passes: `138 tests`.
- Added regression coverage for lock and lid-close background scheduling,
  event-anchored deadlines, stale-generation cancellation, route-preflight
  behavior, bounded fallback rules, and retry suppression after the send gate
  closes.

## Diff Size From GitHub v1.3.0

Compared with GitHub `v1.3.0` (`d636f4b`), the 1.4.0 tree changes roughly:

- `26` files
- `1794` insertions
- `267` deletions

Most of the change is standby-path hardening, diagnostics, and regression test
coverage.
