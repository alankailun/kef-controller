# KEF Controller 1.7.0 - Display-Off Modern Standby Guard

Release date: 2026-06-05

Baseline for this note: the first 1.6.0 build, represented in git by `7d76d1c`
(`fix: reset keepalive backoff on monitor restart`). That 1.6.0 build already
included the speaker-scan cancellation, default-route scan priority, keepalive
failure backoff, and prewarmed-socket restart fixes.

This release adds a safer Windows 11 Modern Standby early-standby signal. The
app now listens for the console display turning off and can send KEF standby
before `PBT_APMSUSPEND` arrives, while still refusing to cut audio that is
currently playing.

## Compared With 1.6.0

- 1.6.0 focused on discovery and maintenance-path efficiency: faster speaker
  selection, cancellable scans, default-route network priority, and quieter
  keepalive behavior when the speaker is unreachable.
- 1.7.0 focuses on Modern Standby timing: display-off is now an additional
  bounded standby opportunity before Windows may freeze or delay the normal
  suspend notification.
- 1.7.0 keeps the same user-facing settings. Display-off standby reuses the
  existing `standby_on_sleep` switch and adds no new UI option.

## Highlights

- **Display-off is now a Modern Standby trigger.** The app registers
  `GUID_CONSOLE_DISPLAY_STATE` and handles `DisplayOff` as an early standby
  opportunity. `DisplayOn` and `DisplayDim` are logged but do not wake or
  standby the speaker.
- **Playback is protected before standby.** Display-off standby is gated by the
  speaker playback state. A fresh cached `playing` state skips immediately, and
  the worker performs a bounded live `is_playing` read before any standby packet
  is sent.
- **Unknown playback state fails safe.** If the live playback read fails or the
  state is unknown, display-off standby aborts and the later suspend path remains
  available.
- **Display-off runs off the Windows message pump.** The hidden-window callback
  only records and dispatches work. Network reads and standby sends remain in a
  daemon worker with generation and deadline checks.
- **Display-off gets its own budget.** Lock and lid-close standby keep their
  tight `300 ms` budget. Display-off standby gets a bounded `1.5 s` budget so it
  can perform the live playback check without making the feature mostly inert.
- **Diagnostics are wired for the new event.** `DISPLAY_OFF` now maps back to
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
- Added coverage for console-display power-setting decode, display-off trigger
  registration, cached playback-state handling, stale/unknown playback safety,
  bounded live playback reads, display-off worker aborts, and display-off
  deadline behavior.
