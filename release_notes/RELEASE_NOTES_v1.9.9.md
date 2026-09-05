# KEF Controller 1.9.9 - Web Controller Validation and State Fixes

## Fixed

- Preserve the original manual MAC input until validation. Invalid characters
  now produce a format error instead of silently removing the MAC constraint.
- Cancel speaker discovery when its dialog closes. Reopened scans wait for
  previous work to release the scan lock, and scan IDs isolate late callbacks.
- Distinguish successful, failed, and skipped UI polls. Failed reads no longer
  refresh the last successful poll time; sleep skips remain neutral and
  unexpected exceptions still reach the error handler.
- Share standby confirmation rules between the controller and web bridge.
  Unconfirmed sends retain the last known power state until a live update.

## Maintenance

- Remove three unreachable input alias keys while preserving accepted spellings.
- Clarify that the current web form requires an IP with an optional MAC.
- Add behavior regression tests for validation, scan cancellation and lock
  handoff, poll outcomes, and standby confirmation.
- Add `build.ps1` and documented build conventions. Builds clean and reuse
  `build/` and `dist/` and produce `installer/output/KEF_Controller_Setup.exe`
  without version or retry suffixes.

## Validation

- All 286 tests passed, including isolated JavaScript scan behavior checks.
- Ruff and Git whitespace checks passed.
- Built the application and installer; verified version 1.9.9 and the packaged
  WebView2 import check. Real-speaker and Windows sleep tests were not performed.
