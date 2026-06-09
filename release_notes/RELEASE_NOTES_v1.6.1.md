# KEF Controller 1.6.1 - Smaller Windows Bundle

Release date: 2026-06-06

Baseline for this note: GitHub tag `v1.6.0` at `8021854`.

This is a packaging- and polish-focused maintenance release. Speaker control
behavior is unchanged from 1.6.0; the work in this build trims unused Qt modules
and DLLs from the frozen Windows executable and cleans up the Speaker Power
Behavior icons. The final 1.6.1 bundle is about `40 MB` for the one-file
executable and about `42 MB` for the installer.

## Highlights

- **Smaller PyInstaller bundle.** The app now excludes unused PySide6 modules
  and strips leftover unused Qt feature DLLs/plugins from the final one-file
  executable.
- **Optional Qt plugins were trimmed too.** Unused platform backends, image
  format plugins, virtual keyboard/touch plugins, and Qt translation payloads
  are removed from the bundle. The app still keeps the Windows platform plugin
  plus the Modern Windows style plugin and SVG/ICO/GIF support needed by the
  current UI and icon stack.
- **QFluentWidgets media widgets are excluded.** The app does not use
  QFluentWidgets' media-player/video widgets, so those hidden imports are
  filtered out to avoid pulling multimedia analysis back into the build.
- **Kept the Qt pieces the app actually uses.** QtCore, QtGui, QtWidgets,
  QtSvg, QtSvgWidgets, and QtXml remain available for the QFluentWidgets UI and
  icon stack.
- **Kept the software OpenGL fallback.** `opengl32sw.dll` is intentionally left
  in the bundle for VMs, RDP sessions, and machines without reliable graphics
  drivers.
- **Release notes are now organized.** Historical release notes were moved into
  the `release_notes/` folder so future release notes live in one place.
- **Standby diagnostics are easier to follow.** Prewarmed standby socket
  `send_enter` logs now use the same trigger reason as the surrounding standby
  action, such as `DISPLAY_OFF`, `WTS_SESSION_LOCK`, or `PBT_APMSUSPEND`.
- **Clearer Speaker Power Behavior icons.** Mismatched icons were replaced with
  proper ones: real lock/unlock, screen on/off (Desktop / Desktop Off), an edit
  pencil for the manual target, and a test beaker for Event Tests. The new icons
  come from Microsoft Fluent UI System Icons (MIT) as themed light/dark SVGs.
- **Grouped power-behavior list.** The list is reordered so the two "Wake
  Speaker" actions sit on top and the "Put Speaker in Standby" actions follow,
  making the wake-versus-standby split obvious at a glance.

## Compatibility

- No configuration changes.
- No speaker control behavior changes.
- Existing `config.json`, `state.json`, startup registration, and logs remain
  compatible with 1.6.0.

## Tests

- Full unit test suite passes: `167 tests`.
- `compileall` passes for `kef_app` and `tests`.
- The 1.6.1 Windows installer was rebuilt with Inno Setup after the slimmer
  PyInstaller build.
