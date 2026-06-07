# KEF Controller 1.3.0 - Faster Standby Path

Release date: 2026-05-15

Baseline for this note: GitHub tag `v1.2.0` at `0b1ba60`.

This release tightens the standby path after the 1.2.0 reliability work. The
main change is removing the Windows `PowerRequestSystemRequired` sleep-hold
wrapper from standby fallback sends. In practice it did not protect the explicit
lock, lid, or suspend paths that this app uses, and prior logs showed it could
delay suspend instead of helping standby complete.

## Compared With 1.2.0

- 1.2.0 made standby reliable by moving to concrete Windows power events,
  prewarmed standby sockets, fast fire-and-forget sends, and stricter identity
  matching.
- 1.3.0 keeps that reliability model but removes the unused sleep-hold layer
  around standby fallback sends.
- 1.3.0 also includes the post-1.2.0 diagnostics work: lock-event logging is
  deferred until after early standby sends, prewarmed sends record `SO_ERROR`,
  duplicate network interface notifications are summarized, and the speaker
  event monitor stops during suspend and restarts on resume.

## Highlights

- **Removed standby sleep hold.** Standby no longer calls
  `PowerCreateRequest` / `PowerSetRequest` around fire-and-forget fallback
  sends. The standby mental model is now: send immediately, log the result, and
  let Windows continue.
- **Lower risk during explicit sleep.** `PowerRequestSystemRequired` only
  prevents idle-sleep timers; it does not block user sleep, lid-close sleep,
  lock-triggered suspend, Modern Standby throttling, or Wi-Fi power-down. Removing
  it avoids a misleading layer of protection.
- **Cleaner fallback path.** Prewarmed standby still runs first. If it fails in
  a recoverable way, fire-and-forget standby still runs, just without trying to
  hold system sleep open.
- **Sharper lock timing diagnostics.** The WTS lock handler records event state
  first, triggers early standby immediately, then writes deferred diagnostic
  logs. Logs now show the split between state recording, `on_lock`, and deferred
  logging.
- **Socket-level send evidence.** Prewarmed standby send logs now include
  `so_error=0` on success or a nonzero `SO_ERROR` status when the socket reports
  a local TCP error after `sendall`.
- **Resume log noise reduced.** Repeated network `ParameterNotification` events
  are summarized with `INTERFACE_CHANGE_DEDUP`, and long-poll speaker events are
  stopped during suspend and restarted on resume.

## Removed

- `TemporarySystemRequiredRequest`
- `temporary_system_required_request`
- PowerRequest ctypes bindings used only by the standby sleep hold:
  `PowerCreateRequest`, `PowerSetRequest`, `PowerClearRequest`, and related
  request-context structs/constants
- `system_required_hold` standby log step
- `hold_fire_and_forget` standby plumbing

## Compatibility

- No user-facing setting was removed.
- Fast standby, early standby, suspend fallback, end-session standby, and
  verified standard standby remain available.
- Existing config files remain compatible.

## Tests

- Unit test suite passes: `114 tests`.
- Updated standby fallback coverage to assert the fire-and-forget fallback runs
  directly and no longer emits `system_required_hold`.

## Diff Size From GitHub v1.2.0

Compared with GitHub `v1.2.0` (`0b1ba60`), the current tree changes roughly:

- `28` files
- `2035` insertions
- `683` deletions

Most of the added code since 1.2.0 is diagnostics and test coverage. The 1.3.0
cleanup itself removes the standby sleep-hold implementation and its Windows API
exports.
