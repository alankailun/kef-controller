# KEF Controller 1.2.0 — "Reliable Standby"

Release date: 2026-05-11

This release is centred on making the speaker go to standby reliably
when the PC sleeps, locks, dims its display, idles out, or closes its
lid — even when Wi-Fi has already begun tearing down.

## Highlights

- **Lock-to-standby that actually works under flaky Wi-Fi.** A new
  fire-and-forget HTTP transport sends the standby command inline on
  the message-pump thread before Windows can drop the network route.
  When all attempts return `WSAEHOSTUNREACH` the speaker is now
  assumed to already be in standby (matching the existing
  standard-path behaviour) instead of falling into a synchronous
  fallback that could survive Modern Standby and incorrectly report
  "success" 1–2 hours later.
- **Earlier standby triggers.** Standby now also fires on
  `GUID_SESSION_USER_PRESENCE` (user idle timeout),
  `GUID_SESSION_DISPLAY_STATUS` (display off), and
  `GUID_LIDSWITCH_STATE_CHANGE` (laptop lid closed) — not just on
  session lock. All four sources share a 30-second dedup window so
  the speaker only sees one command per "user is going away" event.
- **Self-healing speaker event monitor.** Stale `pollQueue`
  subscriptions are now refreshed in place after a single failure;
  the connector is only rebuilt once the failure count crosses the
  configured threshold, which also triggers an IP refresh.
- **Hard 1.5 s ceiling on the standard standby fallback.** Even if
  `socket.connect()` is throttled by Modern Standby it can no longer
  hold the controller generation hostage past the next system event.
- **Shared, pooled HTTP for all KEF API traffic.** A single
  `requests.Session()` with a 16/32 connection pool replaces
  per-call connects.

## Bug fixes

- Fast-standby fire-and-forget now distinguishes "all attempts were
  host-unreachable" from "transport failed" and only enters the
  standard fallback in the latter case.
- Discovery scan tolerates KEF speakers that briefly respond as
  non-KEF HTTP services during cold boot.
- Speaker connector is no longer rebuilt on transient event-poll
  read-timeouts.
- End-session standby uses fire-and-forget so it does not block
  `WM_QUERYENDSESSION` processing.

## New settings (defaults are conservative)

- `standby_on_user_inactive`, `standby_on_display_off`,
  `standby_on_lid_close`
- `endsession_standby_on_shutdown`, `endsession_standby_action_lock_timeout`,
  `endsession_standby_socket_timeout`
- `suspend_fast_standby_enabled`, `suspend_fast_standby_action_lock_timeout`,
  `suspend_fast_standby_socket_timeout`
- `home_event_poll_enabled`, `home_event_poll_timeout`,
  `home_event_reconcile_interval`,
  `speaker_event_recovery_failure_threshold`
- `tray_identity_poll_interval`, `identity_probe_failure_threshold`,
  `resume_dedup_window`

## Automatic migrations

Existing `config.json` files are upgraded in place on first launch:

- `lock_standby_dedup_window`: `8.0 s` → `30.0 s`
- `mac_discovery_probe_timeout`: `0.20 s` → `0.50 s`
- Legacy `expected_speaker_mac` field → `kef_mac`

## Internal refactoring

These do not change behaviour but make future feature additions or
removals safer:

- Standby execution collapsed to a single
  `_execute_standby_policy(...)` driven by `StandbyPolicy` descriptors
  (`PREEMPTIVE`, `FAST_SUSPEND`, `STANDARD`, `ENDSESSION`) instead of
  four near-parallel methods with 15+ keyword arguments each.
- Power-event triggers are now first-class objects under
  `kef_app/controller/triggers/` (`lock`, `user_inactive`,
  `display_off`, `lid_closed`, `suspend`, `query_end_session`,
  `end_session`). Adding a new trigger is a single new file plus a
  registry entry.
- `UserSettings` is split into feature sub-dataclasses
  (`DeviceSettings`, `WakeBehavior`, `StandbyTriggers`,
  `StandbyTuning`, `EndSessionBehavior`, `DiscoverySettings`,
  `StartupSettings`, `SpeakerEventPolling`, `DiagnosticsSettings`).
  Flat field access is preserved via `__getattr__`/`__setattr__` for
  backwards compatibility.
- Transport primitives moved to `kef_app/devices/transport/`
  (`raw_http.py`, `errors.py`, `standby.py`); the controller no
  longer owns network bytes.
- `LockStandbyDedupState` extracted to
  `kef_app/controller/feature_state.py`.

## Changes since 1.1.0

```
9816e9e  Improve standby fast path reliability
2187c74  Use fire-and-forget standby for end session
74d06f4  Benchmark raw standby fast path
7966ef9  Add raw standby fast path
767dbd3  Raise KEF scan probe timeout
cc4d5a2  Make KEF scan tolerate cold speaker startup
5ad860d  Avoid rebuilding KEF connector after poll timeout
b2d92c0  Move event monitor to controller and self-heal stale subscriptions
3c694f9  Use pooled KEF HTTP and event polling
4c8c46b  Gate blind recovery and share standby fast path
f3324b2  Reduce KEF controller network churn
1c9859d  Treat unreachable standby target as asleep
ca4c870  Update packaging docs and runtime shutdown
39f8dc1  Fix lock-time standby race; split power actions; trim dead exports
c783438  Add fast standby path on system suspend
60eab3b  Improve speaker discovery, identity resolution, and logging
```

Plus the 1.2.0 refactor: triggers package, StandbyPolicy descriptors,
segmented UserSettings, transport package, LockStandbyDedupState,
hard timeout on standard fallback, host-unreachable shortcut, and
dedup window auto-migration.
