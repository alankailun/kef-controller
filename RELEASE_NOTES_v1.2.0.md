# KEF Controller 1.2.0 - Reliable Standby

Release date: 2026-05-12

Baseline for this note: GitHub tag `v1.1.0` at `6a82188`.

This release focuses on making standby happen at the right time, with less
guesswork and less log noise. The controller now tries to send standby before
Windows tears down networking, keeps a last-resort suspend path, and avoids the
two false-positive triggers that could shut the speaker off while the PC was
only idle or the display was off.

## Highlights

- **Sleep countdown standby.** A new monitor reads Windows
  `CallNtPowerInformation(SystemPowerInformation)` every two seconds and fires
  standby when `TimeRemaining` is within the configured threshold, currently
  `5s`. The value is read dynamically, so changing the Windows sleep timeout is
  picked up without restarting the app.
- **False-positive early triggers removed.** `standby_on_user_inactive` and
  `standby_on_display_off` were removed. A quiet desk, video playback, or a
  display-off state no longer implies the speaker should turn off.
- **Early standby naming cleaned up.** Historical `lock_pre_standby` and
  `recent_lock_pre_standby_ok` naming is now `early_standby` /
  `recent_early_standby_ok`, matching the fact that the same path is used by
  lock, lid-close, and sleep-countdown triggers.
- **Faster standby send path.** Standby uses a raw fire-and-forget HTTP POST
  with an inline first send, short socket timeouts, and parallel follow-up
  attempts. It does not wait for a response when Windows is about to sleep.
- **Modern Standby freeze protection.** The standard fallback is capped by a
  hard deadline so a frozen process cannot wake up much later and report a
  stale success.
- **Shutdown and sign-out standby.** `WM_QUERYENDSESSION` sends standby with
  the same fire-and-forget strategy, then lets Windows continue shutting down.

## Behavior Changes

- Automatic sleep is now primarily handled by `sleep_countdown`, not by broad
  idle/display heuristics.
- `PBT_APMSUSPEND` remains as a final fallback when the system is already
  entering suspend.
- Laptop lid-close remains an early trigger.
- Screen lock remains an early trigger, controlled by `standby_on_lock`.
- Old `lock_standby_*` config aliases were removed. This is intentional because
  the app is self-use and the current config can be regenerated or saved again.
- The `diagnostic_logging` setting and UI switch were removed. Normal logs keep
  important events, outcomes, warnings, and standby sends; low-value debug
  chatter stays out of the default log.

## Reliability Fixes

- Host-unreachable standby attempts are handled explicitly. If every
  fire-and-forget attempt returns a host-unreachable error, the controller treats
  that as an acceptable best-effort result instead of falling into a slow
  synchronous path.
- The speaker event monitor now recovers stale event subscriptions without
  rebuilding the connector on every transient timeout.
- KEF HTTP traffic uses pooled sessions to reduce connection churn.
- Discovery tolerates speakers that briefly respond like generic HTTP services
  during cold boot.
- MAC-based recovery and identity probing are stricter about matching the
  configured target speaker.
- Resume/unlock deduping prevents duplicate wake attempts from closely spaced
  Windows resume events.

## Architecture

- Standby execution is now policy-driven through `StandbyPolicy` descriptors:
  early standby, fast suspend, standard standby, and end-session standby share
  one execution path.
- Power triggers are first-class objects under `kef_app/controller/triggers/`.
  Current triggers are lock, lid closed, sleep countdown, suspend,
  query-end-session, and end-session.
- Wake and standby code were split out of the old monolithic
  `device_power.py`.
- Raw transport primitives moved to `kef_app/devices/transport/`, including
  reusable HTTP POST bytes and host-unreachable classification.
- User settings are grouped into nested dataclasses for device, discovery,
  wake, standby triggers, standby tuning, end-session behavior, startup,
  polling, and diagnostics.
- Early-standby dedup state lives in `EarlyStandbyDedupState` instead of being
  a loose controller field.

## Packaging And Startup

- The packaged app keeps a stable LocalAppData install copy for shortcuts and
  startup entries.
- Inno Setup closes a running `KEF Controller.exe` before install or uninstall.
- Startup registration handling was cleaned up and reconciles task/registry
  state more predictably.

## Removed

- `standby_on_user_inactive`
- `standby_on_display_off`
- `diagnostic_logging`
- Legacy `lock_standby_action_lock_timeout` alias
- Legacy `lock_standby_dedup_window` alias
- Old `device_power.py`
- Old raw shutdown shim and other dead exports

## Tests

- Unit test suite currently passes: `106 tests`.
- Added coverage for sleep countdown polling, Windows power information reads,
  fire-and-forget standby transport, timeout patching, discovery scans, trigger
  routing, standby policy behavior, and UI power-behavior settings.

## Diff Size From GitHub v1.1.0

Compared with GitHub `v1.1.0` (`6a82188`), the current tree changes roughly:

- `68` files
- `4475` insertions
- `1275` deletions

The main code movement is the replacement of the old device-power path with
separate wake, standby, trigger, transport, and discovery modules.
