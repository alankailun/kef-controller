# KEF Controller 1.7.4 - Startup Reliability Fixes

Release date: 2026-07-18

## Fixed

- Fixed a v1.7.3 startup crash when an installed onedir build is checked from
  a status path that uses the minimal `NullLogger`. The outside-installation
  reminder now uses the logger method guaranteed by that interface.
- Folders selected during installation are now first-class installation
  locations. Installing to another drive, such as `F:\KEF Controller`, no
  longer produces a false outside-installation warning or an incorrect
  startup-registration path.
- The installer always shows the installation-location page.
- When Task Scheduler returns Access Denied while enabling startup, the
  settings page now requests UAC elevation and retries the operation.
- Redesigned the Windows Startup section: its main switch is now the only
  off control, while Task Scheduler and Registry Run are presented as two
  clear, mutually exclusive buttons with an actual-registration status.
- Registry Run is now the frictionless default when startup is enabled. It is
  shown on the left, needs no administrator approval, and Task Scheduler
  remains available on the right as an explicit advanced choice.
- Startup registration changes now run in the background with one consistent
  applying state, preventing the settings page from appearing frozen or
  showing a saved message before a later failure.
- Fixed the startup switch passing its browser event object as the selected
  startup mode, which could produce an unsupported-mode error.
- Interactive startup changes no longer enumerate every scheduled task; the
  broader scan remains in the startup migration/self-heal path.
- Cancelling a UAC request while disabling startup now leaves the existing
  registration untouched. For example, a previously verified Task Scheduler
  entry stays enabled and the Settings page returns to Task Scheduler instead
  of incorrectly showing Registry Run or Off.
- Changing from Task Scheduler to Registry Run is now transactional: if the
  scheduled task cannot be removed, the newly written Registry Run entry is
  rolled back and the original task remains active.
- Fixed a WebView2 watchdog false positive while the controller is minimized.
  Minimized now has the same semantics as hidden for the watchdog, speaker
  polling, and tray toggle; restoring the window refreshes the heartbeat
  before watchdog evaluation. This prevents repeating taskbar attention
  flashes and unexpected window pop-ups every roughly 60 seconds.
- Switching to Registry Run, or disabling startup, now also requests UAC to
  remove a protected older scheduled task before retrying the operation.
- During an upgrade, Setup now offers to close every running KEF Controller
  instance automatically. Choosing OK terminates the application process tree
  and continues installation; choosing Cancel leaves Setup unchanged.

## Verification

- Added a regression test for the frozen onedir path with `NullLogger`.
- Added coverage that confirms an onedir installation on another drive is
  used directly for Windows startup registration.
- Added coverage for the elevated cleanup-and-retry path when moving to
  Registry Run.
- Added coverage for the Registry Run default, button ordering, non-blocking
  registration update, and fast canonical-task lookup.
- Added regression coverage for cancelled disable and cancelled method-change
  flows, ensuring the actual Windows registration is restored in the UI.
- Added coverage for minimized-host visibility, watchdog pausing, and the
  restore-heartbeat race.
- Rebuilt the onedir package and ran the complete automated test suite.
- Compiled the installer with the new running-application handoff flow.
