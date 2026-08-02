# KEF Controller 1.9.5 - Reliable Settings, Leaner Package

Release date: 2026-08-01

## Settings and event simulation

- Fixed the disabled laptop-lid test path. It now reports that no action will
  run instead of raising a `KeyError`.
- Added the lid-close standby rule to the shared settings metadata and a
  regression guard ensuring configuration, the web event table, and settings
  metadata remain aligned.
- Disabled-rule descriptions now safely fall back to the key if future
  metadata is incomplete.

## Reliability and cleanup

- Atomic JSON writes now flush and sync temporary files before replacing the
  live configuration or speaker-state file.
- Simplified local IPv4 route candidate selection and removed unused startup
  and localization definitions.

## Packaging

- Removed unused Qt QML, Quick, and software-OpenGL runtime libraries from
  the frozen application. The `dist` payload is reduced from 107.12 MB to
  74.86 MB while retaining the required Windows platform and ICO plugins.

## Verification

- Verified with 255 automated tests, compilation checks, and a clean diff.
