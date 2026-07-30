# KEF Controller 1.7.9 - Reliable Display-Off Standby

Release date: 2026-07-29

## Fixed

- Display-off and lock standby now use the same resident dispatcher and
  cancellable intent/retry state machine instead of constructing a new worker
  thread for each Windows event.
- Lid-close and suspend fast paths now also use the resident dispatcher, while
  retaining their original event-anchored 300 ms hard deadlines. Suspend never
  enters the retry chain.
- A display-on event always cancels outstanding display-off work before the
  wake policy is evaluated, including when display-on wake is disabled or the
  Windows session remains locked.
- Display-off retries are generation-cancellable and no longer lose their
  intended standby action merely because the original 300 ms event window was
  consumed before a worker began running.
- Display-off cold TCP sends now use one synchronous attempt per state-machine
  step.  No background follow-up sender can change the speaker state after the
  attempt was reported as failed.
- A failed persistent prewarmed socket now falls through to the second pooled
  socket before a cold TCP connection is attempted.
- Before a pooled standby send, the app now rejects a socket that is already
  readable (peer FIN/RST) instead of accepting a local `sendall` buffer write
  as a false standby success. Each pooled failover attempt is timed separately.
- A verified fallback runs outside the resident dispatcher, so a slow identity
  check or HTTP verification cannot delay a later lid-close or suspend send.
- Cancellable retry cancellation/supersession is explicitly logged, and
  UI/tray listener notifications run outside the dispatcher.
- Intent cancellation is recorded explicitly in structured logs; a verified
  success can now transition directly from `sending` to `confirmed`.
- Display-on no longer decides whether to wake from the application's prior
  send record. It always schedules a safe recovery wake after cancelling the
  matching display-off intent; if a fresh source read shows the speaker is
  already on, the configured input is left untouched.
- Unlock now uses that same safe recovery wake path after cancelling a lock
  standby intent, including when the last attempt was recorded as failed.
- Removed the obsolete standalone lock cached-send path and its duplicate
  diagnostics. The shared dispatcher is now the sole production path.
- Removed the obsolete no-deadline early-standby API path. Lid-close and
  suspend now require their event-anchored deadlines at every fast-send layer;
  the display-off intent path remains generation-cancellable by design.

## Verification

- Added coverage for lock cancellation, dispatcher isolation from verified
  fallback, direct `sending → confirmed`, deadline-preserving dispatcher work,
  generation-only sending, and prewarmed socket failover.
- Full unit suite: 233 tests passed.
