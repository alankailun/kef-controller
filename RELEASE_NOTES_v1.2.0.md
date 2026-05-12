# KEF Controller 1.2.0 - Reliable Standby

Release date: 2026-05-12

Baseline for this note: GitHub tag `v1.1.0` at `6a82188`.

This release focuses on making standby happen at the right time, with less
guesswork and less log noise. The controller now sends standby from concrete
Windows power events, keeps a last-resort suspend path, and avoids the
false-positive triggers that could shut the speaker off while the PC was only
idle or the display was off.

## Highlights

- **False-positive early triggers removed.** `standby_on_user_inactive` and
  `standby_on_display_off` were removed. A quiet desk, video playback, or a
  display-off state no longer implies the speaker should turn off.
- **Early standby naming cleaned up.** Historical `lock_pre_standby` and
  `recent_lock_pre_standby_ok` naming is now `early_standby` /
  `recent_early_standby_ok`, matching the fact that the same path is used by
  lock and lid-close triggers.
- **Prewarmed standby socket path.** A new background monitor keeps the target
  speaker's TCP route warm with a short verified keepalive. Early standby tries
  this warmed path first, then falls back to the existing fire-and-forget send.
- **Faster standby send path.** Standby uses a raw fire-and-forget HTTP POST
  with an inline first send, short socket timeouts, and parallel follow-up
  attempts. It does not wait for a response when Windows is about to sleep.
- **Modern Standby freeze protection.** The standard fallback is capped by a
  hard deadline so a frozen process cannot wake up much later and report a
  stale success.
- **Shutdown and sign-out standby.** `WM_QUERYENDSESSION` sends standby with
  the same fire-and-forget strategy, then lets Windows continue shutting down.

## Behavior Changes

- The experimental `sleep_countdown` trigger was removed because
  `SystemPowerInformation.TimeRemaining` is unreliable on Modern Standby
  systems and can stay at `0xFFFFFFFF` instead of exposing a usable countdown.
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
- Prewarmed standby sends have a strict mono-time deadline. If Windows freezes
  the process during the send and the call returns much later, the result is
  logged as `frozen_during_send` and the controller falls back instead of
  claiming a stale success.
- The log now emits a direct warning when a Windows lock or lid event is
  delivered, but the message pump is frozen for several seconds before the early
  standby trigger can run.
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
  Current triggers are lock, lid closed, suspend, query-end-session, and
  end-session.
- Wake and standby code were split out of the old monolithic
  `device_power.py`.
- Raw transport primitives moved to `kef_app/devices/transport/`, including
  reusable HTTP POST bytes and host-unreachable classification.
- The prewarmed standby socket monitor lives in
  `kef_app/controller/prewarmed_standby_socket.py`; it defaults to verified
  short-connection keepalives, with persistent socket mode left disabled.
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
- `standby_on_sleep_countdown`
- `sleep_countdown_threshold_s`
- `sleep_countdown_poll_interval_s`
- `diagnostic_logging`
- The `CallNtPowerInformation(SystemPowerInformation)` sleep countdown monitor
- Legacy `lock_standby_action_lock_timeout` alias
- Legacy `lock_standby_dedup_window` alias
- Old `device_power.py`
- Old raw shutdown shim and other dead exports

## Tests

- Unit test suite currently passes: `104 tests`.
- Added coverage for fire-and-forget standby transport, timeout patching,
  discovery scans, trigger routing, prewarmed standby send fallback, standby
  policy behavior, and UI power-behavior settings.

## Diff Size From GitHub v1.1.0

Compared with GitHub `v1.1.0` (`6a82188`), the current tree changes roughly:

- `69` files
- `4998` insertions
- `1276` deletions

The main code movement is the replacement of the old device-power path with
separate wake, standby, trigger, transport, and discovery modules.
