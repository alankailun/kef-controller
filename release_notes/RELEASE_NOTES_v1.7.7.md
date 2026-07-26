# KEF Controller 1.7.7 - WebView2 UI Stability

Release date: 2026-07-25

## Fixed

- Normal `PROCESS_START` lifecycle entries remain informational even when an
  optional parent-process diagnostic contains the word `failed`.
- New WebView2 windows now bootstrap from one current controller-state snapshot
  and the current event cursor. They no longer replay stale state changes and
  notifications accumulated while the controller window was hidden.
- Incoming event batches coalesce repeated state snapshots before the UI is
  repainted. Home and Settings are no longer redrawn while the user is viewing
  the Log page.
- Host recovery no longer terminates the WebView2 window after one missing
  60-second heartbeat. It now waits for a visible-window timeout, confirms the
  condition, checks the native window message pump, and limits repeated
  automatic restarts.

## Reliability

- Installing while KEF Controller is not running now proceeds directly to file
  replacement. When it is running, Setup force-closes its process tree after
  confirmation, then waits only for Windows to release its file handles;
  uninstall uses the same no-wait-if-closed behavior.
- Keeps the proven 1.7.6 startup behavior: the hidden native window is shown
  after WebView2's browser control has loaded. It does not wait for a
  JavaScript-ready signal before becoming visible.
- The recovery path does not invoke `CoreWebView2.Reload()` from a renderer
  failure callback, avoiding a potential WinForms message-pump stall when a
  renderer is already unresponsive.

## Verification

- Added API coverage for bootstrap cursor handling and UI-source coverage for
  state coalescing.
- Passed Python bytecode compilation and the Web API, Web bridge, and WebView2
  runtime unit tests before packaging.
