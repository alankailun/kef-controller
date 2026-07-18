# KEF Controller 1.7.4 - Startup Crash Fix

Release date: 2026-07-18

## Fixed

- Fixed a v1.7.3 startup crash when an installed onedir build is checked from
  a status path that uses the minimal `NullLogger`. The outside-installation
  reminder now uses the logger method guaranteed by that interface.

## Verification

- Added a regression test for the frozen onedir path with `NullLogger`.
- Rebuilt the onedir package and ran the complete automated test suite.
