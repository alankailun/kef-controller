# KEF Controller 1.7.2 - Warm Window Recovery

Release date: 2026-07-18

## Highlights

- **Close now hides, not exits.** Clicking the native window close button keeps
  the warm WebView2 host alive in the tray. Reopening the controller restores
  it immediately instead of triggering a PyInstaller cold start and white
  placeholder window.
- **No visible cold-start placeholder.** On an actual first launch, the native
  window remains hidden until WebView2 has loaded the controller page.
- **Watchdog recovery is race-safe.** Restoring a hidden window records an
  immediate UI heartbeat, so the renderer watchdog cannot restart a healthy
  host before the browser resumes its long-poll request.
- **No orphan WebView windows.** Host restarts and application shutdown now
  terminate the complete PyInstaller process tree, including the WinForms and
  WebView2 child processes.

## Verification

- Added coverage for close-to-hide, deferred first display, visibility
  heartbeat refresh, and process-tree termination.
- Full automated suite run before packaging.
