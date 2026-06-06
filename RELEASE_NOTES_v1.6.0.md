# KEF Controller 1.6.0 - Modern Standby And Discovery Polish

Release date: 2026-06-05

Baseline for this note: GitHub tag `v1.5.0` at `30fa07e`.

This release collects the post-1.5.0 reliability work into one official 1.6.0
build. It improves speaker discovery, reduces long-running background noise
when a speaker is unreachable, and adds a safer Windows 11 Modern Standby
display-off standby path that does not cut active playback.

## Compared With 1.5.0

- 1.5.0 made hot-path logging asynchronous and unified off-pump standby
  dispatch for lock, lid-close, and suspend events.
- 1.6.0 keeps that hot-path shape, then tightens the surrounding behavior:
  manual speaker selection is faster and cancellable, keepalive failures back
  off instead of logging every few seconds forever, resume/discovery avoids
  wasted network scans, and display-off becomes an additional Modern Standby
  signal guarded by playback checks.

## Highlights

- **Display-off is now a Modern Standby trigger.** The app registers
  `GUID_CONSOLE_DISPLAY_STATE` and handles `DisplayOff` as an early standby
  opportunity. `DisplayOn` and `DisplayDim` are logged but do not wake or
  standby the speaker.
- **Playback is protected before display-off standby.** A fresh cached
  `playing` state skips immediately, and the display-off worker performs a
  bounded live `is_playing` read before any standby packet is sent.
- **Unknown playback state fails safe.** If the live playback read fails or the
  state is unknown, display-off standby aborts and the later suspend path remains
  available.
- **Display-off runs off the Windows message pump.** The hidden-window callback
  only records and dispatches work. Network reads and standby sends remain in a
  daemon worker with generation and deadline checks.
- **Display-off gets its own bounded budget.** Lock and lid-close standby keep
  their tight `300 ms` budget. Display-off standby gets a bounded `1.5 s` budget
  so it can perform the live playback check without making the feature mostly
  inert.
- **Manual speaker selection is more responsive.** Scan candidates can surface
  progressively, seed/known-IP identity checks run before broad probing, and
  candidate networks are prioritized by the default route instead of treating
  VPN or overlay interfaces as equally likely speaker networks.
- **Closing the speaker picker cancels the scan.** The background scan now gets
  a cancellation token so a closed selection dialog does not leave an orphan scan
  holding the blind-discovery lock and causing an immediate rescan to report
  "No Speakers Found".
- **Discovery no longer wastes work while the route is down.** Automatic IP
  recovery skips discovery when Windows explicitly reports that there is no
  local route to the current speaker IP.
- **ARP recovery is cheaper.** Existing ARP cache hits are still kept as a
  free fast path, but expensive ARP network sweeps no longer block the reliable
  HTTP identity scan.
- **Prewarmed socket lifecycle is cleaner around suspend/resume.** Stopping the
  monitor for suspend preserves holders long enough for a pending suspend worker
  to use them, while monitor start clears stale holders and resets keepalive
  failure backoff.
- **Keepalive failures back off.** Sustained unreachable-speaker failures now
  back off instead of producing hundreds of repeated keepalive attempts and log
  lines.
- **Diagnostics are wired for the new event.** `DISPLAY_OFF` maps back to
  `GUID_CONSOLE_DISPLAY_STATE`, so early-standby timing logs and frozen-thread
  warnings can attribute this path correctly.

## Compatibility

- Existing configuration and state files remain compatible.
- No user-facing setting was added or removed.
- `standby_on_sleep=False` disables display-off standby as well as regular sleep
  standby.
- If playback cannot be confirmed as stopped/paused, display-off standby is
  skipped rather than risking an audio interruption.

## Tests

- Full unit test suite passes: `175 tests`.
- Added coverage for scan cancellation, route-prioritized discovery, ARP
  recovery fallback behavior, keepalive backoff/reset behavior, console-display
  power-setting decode, display-off trigger registration, cached playback-state
  handling, stale/unknown playback safety, bounded live playback reads,
  display-off worker aborts, and display-off deadline behavior.
