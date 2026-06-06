# KEF Controller 1.6.0 - Modern Standby And Discovery Polish

Release date: 2026-06-05

Baseline for this note: GitHub tag `v1.5.0` at `30fa07e`.

This release collects the post-1.5.0 reliability work into one official 1.6.0
build. It improves speaker discovery, reduces long-running background noise
when a speaker is unreachable, and adds a Windows 11 Modern Standby display-off
standby trigger as an independent, toggleable power behavior.

## Compared With 1.5.0

- 1.5.0 made hot-path logging asynchronous and unified off-pump standby
  dispatch for lock, lid-close, and suspend events.
- 1.6.0 keeps that hot-path shape, then tightens the surrounding behavior:
  manual speaker selection is faster and cancellable, keepalive failures back
  off instead of logging every few seconds forever, resume/discovery avoids
  wasted network scans, and display-off becomes an additional Modern Standby
  standby trigger with its own on/off setting.

## Highlights

- **Display-off is now a Modern Standby trigger.** The app registers
  `GUID_CONSOLE_DISPLAY_STATE` and handles `DisplayOff` as a standby
  opportunity. `DisplayOn` and `DisplayDim` are logged but do not wake or
  standby the speaker.
- **It is its own power-behavior setting.** A new `standby_on_display_off`
  toggle appears in Speaker Power Behavior, default on like the other behaviors
  and independent of `standby_on_sleep`, so it can be enabled or disabled on its
  own.
- **Pure display-off standby.** When the screen turns off, the speaker is put
  into standby exactly like the lock and lid-close paths — there is no playback
  or audio check. Deciding whether playback should keep the speaker awake is left
  to the user (toggle the setting) and to the speaker's own no-signal
  auto-standby.
- **Display-off runs off the Windows message pump.** The hidden-window callback
  only records and dispatches work. The standby send runs in a bounded daemon
  worker with generation and deadline checks, sharing the standard `300 ms`
  early-standby budget used by lock and lid-close.
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
- **Run Events can simulate display-off.** The diagnostics panel now includes a
  `Display Off` event test, so the new Modern Standby path can be exercised
  without waiting for the real screen timeout.

## Compatibility

- Existing configuration and state files remain compatible.
- **One new setting:** `standby_on_display_off` (default on). Config files
  written by older builds simply pick up the default on first load.
- Because display-off standby has no playback check, disable
  `standby_on_display_off` in Speaker Power Behavior if you do not want the
  screen turning off to put the speaker into standby.
- `standby_on_sleep` now controls only sleep standby; display-off standby is
  controlled by its own toggle.

## Tests

- Full unit test suite passes: `167 tests`.
- Added coverage for scan cancellation, route-prioritized discovery, ARP
  recovery fallback behavior, keepalive backoff/reset behavior, console-display
  power-setting decode, display-off trigger registration, the independent
  `standby_on_display_off` toggle (round-trip and power-behavior options), and
  display-off dispatch / disabled-skip / scheduled early-standby behavior.
