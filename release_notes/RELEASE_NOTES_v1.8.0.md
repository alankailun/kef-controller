# KEF Controller 1.8.0 - Secure Web Control and Efficient UI

Release date: 2026-07-30

## Improved

- The local WebView control API now uses a per-launch random capability token.
  Other local processes and web pages cannot issue control requests without the
  URL opened by the application.
- Web API requests now reject bodies over 1 MiB, and the local server joins its
  serving thread during shutdown.
- Normal Web UI polling reuses the existing speaker connector. A failed status
  read clears it before the following read opens a fresh connection.
- Web and tray power actions now use a public controller method, keeping action
  generation ownership inside the controller.
- The tray now reads the controller's authoritative active-action state instead
  of maintaining a second counter.
- The Web UI receives structured power-action fields (`action`, `phase`, and
  `success`) and no longer depends on English notification wording to update
  its pending controls.
- The Web API now returns native JSON payloads internally instead of encoding,
  decoding, then encoding the same data again. Unused event descriptions are
  no longer sent in every state update.

## Maintenance

- Consolidated the duplicated power-action success predicate.
- Removed unreachable wake-input branches and redundant JSON exception
  handling.

## Verification

- Full unit suite: 237 tests passed.
