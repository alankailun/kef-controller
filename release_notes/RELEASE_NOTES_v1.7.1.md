# KEF Controller 1.7.1 - Responsive UI Recovery

Release date: 2026-07-15

## Highlights

- **Fully fluid pages.** Home, Settings, and Log now use the available window
  width rather than a fixed maximum content width. The narrow-window header
  still wraps safely, so the page title and controls remain usable at the
  840 px minimum window size.
- **Compact normal launch.** The controller opens at 1120 × 760 by default
  instead of presenting a large, mostly empty canvas. It remains resizable
  and can still be maximized.
- **Near-idle WebView updates.** The front end now uses a 15-second long poll:
  a state change is delivered immediately, while an idle UI keeps one waiting
  loopback request instead of issuing one every 400 ms.
- **Automatic frozen-UI recovery.** If a visible WebView2 host stops sending
  UI heartbeats for 60 seconds, the controller restarts only that host window.
  Speaker control, tray operation, and the headless runtime remain running.
- **Safe shutdown with waiting requests.** Long-poll request threads are
  daemonized and woken during server shutdown, so a pending UI update cannot
  delay application exit.

## Verification

- Added long-poll delivery and WebView-host heartbeat recovery tests.
- Verified the responsive layout at 840 px and 1520 px widths with no browser
  console errors.
- Full automated suite: 191 tests passed.
