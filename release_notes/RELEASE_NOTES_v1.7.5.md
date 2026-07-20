# KEF Controller 1.7.5 - Shutdown Reliability

Release date: 2026-07-20

## Fixed

- Fixed a shutdown race where the prewarmed standby stage could consume the
  shared deadline before the independent TCP fallback had a chance to send.
- The prewarmed stage now receives a bounded portion of the available time,
  while fire-and-forget standby always retains at least a 150 ms send window.
  The same deadline isolation also protects sleep, lid-close, lock, and
  display-off standby paths.
- Increased the normal Windows end-session standby budget from 300 ms to two
  seconds. The controller registers a temporary Windows shutdown block reason
  while the speaker request is in progress and removes it immediately when
  the bounded action finishes.
- Removed repeated Windows route-table probes from the time-critical transport
  gate. During shutdown or suspend the networking stack can stall these probes;
  the socket result is now the authoritative reachability signal.
- Prewarmed-stage skip results are retained and written at INFO level, including
  cases such as a missing recent heartbeat. Failed sends include their status,
  duration, socket error, and deadline context for future diagnosis.
- Display-on wake decisions are now visible in the normal application log.
  When the rule does not run, the log records whether it was disabled, the
  preceding sleep was not caused by display-off, the session was locked, or
  Windows was ending the session.

## Reliability review

- Wake, normal standby, discovery, recovery, and visibility-gated polling were
  reviewed against the available July logs. No additional state-machine defect
  was found in those paths.
- The five-second prewarmed socket rotation remains unchanged. KEF does not
  guarantee the lifetime of an idle HTTP connection, so reducing rotation
  without device-side evidence would trade connection count for lower shutdown
  reliability.

## Verification

- Added regression coverage proving that a delayed prewarmed stage cannot
  starve the fire-and-forget fallback.
- Added coverage that the bounded standby path avoids blocking route probes and
  relies on the actual transport result.
- Updated sender coverage so skipped prewarm diagnostics survive into the final
  result.
- Passed all 208 automated tests and Ruff static checks before packaging.
