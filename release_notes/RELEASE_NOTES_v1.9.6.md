# KEF Controller 1.9.6 - Safer Return From Lock, Leaner Runtime

## Reliability

- A DisplayOn event received while Windows is still locked is now deferred to
  the corresponding unlock instead of being lost.
- Deferred wakes are generation-fenced, so a later DisplayOff, lock, suspend,
  end-session, or manual action cannot wake the speaker late.
- A deferred DisplayOn wake and the normal unlock rule are deduplicated into
  one wake action.
- UI polling and automatic blind discovery pause only while Windows is entering
  sleep. Prewarmed-socket failures no longer suppress UI recovery polling.

## Performance and maintenance

- Startup-registration status is read once before and once after a settings
  change instead of repeatedly querying Task Scheduler.
- Removed unused startup helpers, stale settings fields, unused controller and
  model helpers, and redundant standby helper arguments.
- Split the headless runtime into focused startup, power-message,
  session-message, and notification-registration helpers.
- Excluded unused Qt SVG and Qt Network payloads from the packaged app.
- Logging is now fixed at INFO; the obsolete log-verbosity setting was removed.
