# KEF Controller 1.7.0 - Reliable WebView2 Deployment

Release date: 2026-07-15

## Highlights

- **No more silent white-screen fallback on Windows 10.** The app now verifies
  the Microsoft Edge WebView2 Runtime and the .NET Framework prerequisite
  before it creates the web UI. If either prerequisite is unavailable, it
  shows a clear message and exits instead of silently falling back to the
  legacy Internet Explorer engine.
- **The installer now provisions WebView2 when needed.** `KEF_Controller_Setup`
  includes Microsoft's signed Evergreen WebView2 Bootstrapper. It runs only
  when the Runtime is missing and automatically installs the correct
  architecture from Microsoft during setup.
- **Lower antivirus false-positive risk.** UPX packing is disabled for the
  unsigned executable. This increases the file size slightly, but avoids a
  common trigger for heuristic antivirus and SmartScreen warnings.
- **Cleaner application startup code.** Removed the unused no-op window-icon
  handoff; the native WebView2 host correctly uses the icon embedded in the
  packaged executable.
- **Clean hidden-window shutdown.** The message-window class is now
  unregistered only after its final window has been destroyed, removing the
  misleading `UnregisterClass` 1412 exit diagnostic.
- **Confirmed wake readiness.** A successful wake command now means the
  command reached the speaker; volume and input controls remain unavailable
  until a real poll confirms an on state and a live, non-standby input source.
- **Useful, bounded diagnostics.** Web UI polling no longer writes a successful
  identity verification every two seconds. Temporary network failures are
  retained as a first diagnostic and then rate-limited to once every 30
  seconds, instead of flooding the log while the speaker is unavailable.
- **Safe duplicate-launch handling.** The single-instance check now runs
  before startup self-repair, so an accidentally launched second EXE cannot
  attempt to replace the active installed EXE or rewrite its startup task.
- **Rapid open clicks create only one window.** While the native WebView2
  host is starting, repeated tray/menu open requests are coalesced instead of
  spawning overlapping controller windows. The controller also identifies the
  host window by its child-process ID, preventing it from attaching to a
  leftover window from another launch.
- **Complete power-event diagnostics.** The Laptop Lid Close row now has its
  own simulation button and exercises the same controller path as a real lid
  close event.
- **Historical log reader.** The Log page can open date-rotated log files from
  the app log directory. Archived views stay static, while navigating away
  resets the page to today's live log on the next visit.
- **Accurate prewarm diagnostics.** A temporary prewarmed-standby keepalive
  retry is now shown as a warning instead of a red application error; real
  action failures remain errors.

## Compatibility

- Windows 11 includes the Evergreen WebView2 Runtime.
- Windows 10 is supported. The installer repairs the smaller set of Windows
  10 installations that do not already have the Runtime, as long as setup has
  internet access.
- For offline installations, the app displays an explicit prerequisite message
  rather than opening a blank window. Install the Evergreen WebView2 Runtime
  and launch the app again.

## Tests and packaging checks

- Added registry-based tests for installed, missing, and too-old WebView2
  Runtime/.NET prerequisite states.
- Added tests for wake confirmation, rate-limited UI-poll diagnostics, and
  safe rotated-log selection.
- Added host-lifecycle tests for rapid repeated open requests and window
  ownership filtering.
- Verified the packaged executable's WebView2 preflight mode.
- Rebuilt the Windows executable with UPX disabled and compiled the 1.7.0
  Inno Setup installer.
