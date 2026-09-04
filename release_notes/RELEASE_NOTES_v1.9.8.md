# KEF Controller 1.9.8 - Reliable Session State and Responsive Live Controls

## Fixed

- Routed real Windows lock, lid-close, and suspend notifications through the
  controller event entry points so lock state is recorded before standby is
  dispatched. Display-on wake requests received at the lock screen are now
  deferred until unlock as intended.
- Prevented a slow Task Scheduler or UAC operation from restoring an old copy
  of unrelated settings. Startup reconciliation now merges only the resulting
  startup mode into the latest in-memory configuration on the Qt thread.
- Moved background action results back to the Qt thread before changing bridge
  state or saving configuration.
- Coalesced rapid volume changes into one serial worker that always converges
  on the latest requested value without action-lock error toasts for obsolete
  slider positions.
- Rewrote legacy device-target configuration when migration removes obsolete
  keys, and aligned the TV input label with the backend's `TV (eARC)` label.
- Made the startup simulation report completion and its real elapsed time
  instead of describing the synchronous wake as merely scheduled.
- Preserved the standby icon on the initial power-request notification and
  localized disabled simulated-event names in both supported UI languages.
- Shows a neutral startup-registration progress state until the asynchronous
  Windows snapshot is ready, avoiding a false "not registered" first frame.
- Finds and reconciles a legacy Task Scheduler entry even when it has a
  different task name and no canonical task or Registry entry exists.
- Keeps controller-originated volume and input updates from resetting the UI's
  network-poll age; "Last heartbeat" now reflects network activity only.

## Performance

- Reuses a recent successful UI poll as short-lived target validation for live
  volume and input controls, avoiding repeated full identity probes.
- Avoids a second wake identity probe when the target IP has not changed and
  removes the redundant post-wake identity capture.
- Uses targeted Task Scheduler queries on the normal frozen startup path,
  moving the exceptional legacy-task enumeration and reconciliation onto the
  GUI startup worker so it cannot delay the tray icon.
- Publishes frequently changing runtime state separately from static settings
  and input metadata.
- Enables HTTP/1.1 keep-alive for the loopback WebView API.
- Removes an extra input-source read before changing inputs.

## Maintenance

- Replaced text-matching toast translation with stable message codes and
  parameters.
- Removed the forwarding-only trigger package, unused startup facade and
  status helpers, stale logging/outcome branches, redundant Win32 loading,
  duplicate identity checks, and unused CSS.
- Corrected README references to the retired QFluentWidgets UI and pinned all
  direct runtime/build dependencies, including `requests`.
