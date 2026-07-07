# KEF Controller 1.6.3 - Display-On Wake and Discovery Fixes

Release date: 2026-07-06

Baseline for this note: GitHub tag `v1.6.2` at `255759e`.

This is a correctness and polish release driven by a full code review of the
controller, standby, discovery, and Windows display-power paths.

## Highlights

- **Screen-off standby now has a paired screen-on wake.** If
  `DISPLAY_OFF` was the reason the app put the speaker into standby, a later
  `DISPLAY_ON` wakes the speaker again. This fixes the one-way case where the
  screen timed out, the speaker slept, Windows never locked, and no unlock
  event ever arrived to wake the speaker.
- **Display-on wake is guarded by explicit controller state.** The controller
  now records the current desired state and reason for each generation, and
  tracks whether the Windows session is locked. `DISPLAY_ON` only wakes when
  the last desired state is `sleep` because of `DISPLAY_OFF`, the session is
  not locked, and the session is not ending.
- **Locked-screen display changes stay quiet.** If the screen turns on while
  Windows is still locked, the app does not wake the speaker; it keeps the
  existing `wake_on_unlock_only` behavior and waits for `WTS_SESSION_UNLOCK`.
- **Display-on wake has its own setting and Run Events button.** Settings now
  include `Wake Speaker When the Screen Turns On`, paired with
  `Put Speaker in Standby When the Screen Turns Off`. The diagnostics Run
  Events panel also has a `Display On` test event.
- **Wake de-duplication is directional.** A successful display-on wake marks a
  wake as scheduled so a following unlock does not duplicate the action, but a
  new Display Off -> Display On pair is not blocked by a previous unlock wake.
- **Run Events no longer leaves a fake locked-session state.** The `Lock` test
  still sends the lock standby path, but only the real `WTS_SESSION_LOCK`
  event marks the Windows session as locked.
- **Faster network scans.** The reachability probe and identity phases now
  submit every host to the worker pool at once and harvest completions as they
  arrive, instead of running fixed-size batches where one slow probe stalled
  the whole batch. Mixed fast/slow networks finish sooner while the all-timeout
  case remains bounded by the worker count and probe timeout; results keep a
  stable host order.
- **Recovery scans can be interrupted.** `discover_kef_device_blind` accepts a
  `should_continue` gate (mirroring the manual scan), and IP recovery wires it
  to the session-ending flag so a full-subnet sweep unwinds quickly during
  logoff/shutdown instead of finishing the whole subnet.
- **Wake with no input source is now an explicit skip.** Waking works by
  setting the input source; with `kef_input` cleared, WAKE previously built a
  connector without sending anything, reported success, and marked the speaker
  "on". It now logs `SKIP | cause=no_input_configured` and returns failure.
- **Lock-standby fast path now emits power-action events.** The cached
  prewarmed lock fast path reports `power_action_started` / `power_action_finished`
  like every other standby path, so tray and home-view power hints track it.
- **Live UI controls respect discovery cooldowns.** Volume reads/writes and
  input changes no longer force IP recovery: while the speaker is offline,
  each click could previously bypass the blind-scan cooldown and fire a full
  subnet scan. Power actions (wake/standby) keep forced recovery.
- **Removed the unreachable "exactly one device" adoption branch.** In the
  full scan, every MAC-matching candidate already returns inside the sweep, so
  the trailing single-candidate adoption path could never execute. Non-matching
  leftovers are now reported in a single diagnostic summary line.
- **Removed the unused `flags` parameter** from `_run_standby_action`.

## Compatibility

- Existing `config.json` and `speaker_state.json` files are unaffected. The new
  `wake_on_display_on` and `display_on_wake_delay` settings use defaults when
  absent from older configs.
- Scan results, candidate ordering, and the seed-first cancel-before-sweep
  behavior are unchanged; only scheduling inside a sweep changed.
- The seed identity probe intentionally stays synchronous before the sweep so
  a scan can still be cancelled right after the seed candidate appears.

## Tests

- Added coverage for Display Off -> Display On paired wake, locked-session
  blocking, non-display-off sleep blocking, wake de-duplication direction, and
  the new diagnostics test button.
- The cached-lock fast-path test was updated to assert the new
  `power_action_started` / `power_action_finished` events.
- Full unit test suite passes: 174 tests.
- The 1.6.3 Windows executable was rebuilt with PyInstaller.
