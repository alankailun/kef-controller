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

## Verification

- Added a regression test for the frozen onedir path with `NullLogger`.
- Added coverage that confirms an onedir installation on another drive is
  used directly for Windows startup registration.
- Rebuilt the onedir package and ran the complete automated test suite.
