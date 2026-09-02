# KEF Controller 1.9.7 - Consistent Packaging, Smaller Maintenance Surface

## Release correctness

- Aligned the executable file and product versions with the installer version.
- Corrected build documentation to describe the actual PyInstaller onedir
  layout and its executable path.
- Replaced a cross-module private Web API server call with a public activity
  notification method.
- Prevented build-environment ICU DLLs from shadowing Windows' system ICU and
  breaking QtCore at startup, and made upgrades replace the generated runtime
  directory so obsolete dependencies cannot survive an install.

## Maintenance and performance

- Removed obsolete QWebChannel wrappers, translations, result fields, package
  re-exports, and test-only application methods.
- Consolidated structured logging while preserving the controller's injectable
  monotonic clock, and removed duplicate power-generation and session-state
  writes.
- Reused rendered tray icons, simplified configuration coercion and state
  copying, and removed small forwarding helpers.
- Added a Ruff configuration for import sorting, Python upgrades, and core
  correctness checks with a 120-column project line length.
