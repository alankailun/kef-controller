# KEF Controller 2.0.0

## Reliability

- Register Windows power and session notifications before starting speaker connection work. The startup connector now uses the configured request timeout, and prebuilding runs in the background.
- Hold the startup wake until the background prebuild finishes recovering the target IP. A lock during startup still cancels the wake. Previously a concurrent wake could lose the discovery lock and skip waking after the speaker's IP changed.
- Fence discovery results, identity probes, polls, and fast standby sends to the selected target. A late result for a previous speaker cannot replace a newly selected speaker.
- Clear the previous speaker's identity and live state when the target changes. Verified details from manual selection are applied immediately.
- Keep prewarmed connection failures on their existing keepalive retry path without starting background IP discovery. Lock, unlock, startup, and visible UI actions can still recover a changed speaker IP when needed.
- Allow a new wake after an intervening standby even when it falls inside the duplicate notification window.
- Apply the WebView host restart limit to native window hangs as well as heartbeat stalls.

## State and discovery

- Publish UI state only after the selected speaker has passed identity validation, and treat an empty input source as missing data.
- Preserve action failures when later state polls also fail.
- Limit manual scan lock waits to ten seconds, with cancellation and logging. Waiters use a condition notification when a scan finishes. A timed-out wait tells the user that a background search is running instead of reporting that no speakers were found.
- Send discovered speakers to the scan dialog as soon as HTTP identification finishes, while TCP probing continues. TCP and HTTP worker limits are independent.
- Reuse a recent verified identity for routine UI and tray polls. Actions still perform their own required identity checks. When every live read fails, the poll verifies the speaker again instead of treating the cached identity as proof that it is reachable.
- Scope HTTP timeouts and connection pools to the speaker connector instead of changing global `requests` behavior. Sessions are isolated by thread and speaker host.

## Maintenance

- Split the web state bridge and Windows event dispatcher into focused modules, and move the web app script out of `index.html`.
- Centralize MAC validation and align English and Chinese installation documentation with the current installer.
- Separate runtime, build, and development dependencies. The retained audit script is `tests/review_repro.py`.
- Fix the concurrent snapshot test to wait for an actual reader observation.
- Remove the unfenced `update_kef_ip` helper and the unused scan identification helper. Recovered IP changes and rejected stale results are logged.
- Make `build.ps1` work in Windows PowerShell 5.1. PyInstaller writes progress to stderr, which previously stopped the script; exit codes now decide success and the logs are plain text.

## Validation

- Ruff and Git whitespace checks passed; all 304 unit tests passed, including JavaScript checks through Node.js.
- The startup wake regression test fails without the prebuild wait and passes with it.
- Built the application and installer with `build.ps1`; verified version 2.0.0 and the packaged WebView2 import check. Loaded the packaged web UI through the real local API server with a simulated speaker: the home, log, and settings pages and the scan dialog rendered without console errors.
- All six isolated audit scenarios in `tests/review_repro.py` passed after changing their assertions to the corrected behavior.
- Real speaker, suspend, lid close, and shutdown hardware tests remain to be performed on a Windows machine with a KEF speaker.
