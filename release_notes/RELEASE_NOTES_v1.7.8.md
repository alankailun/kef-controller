# KEF Controller 1.7.8 - Reliable Controls and Structured Logs

Release date: 2026-07-26

## Fixed

- Volume dragging now always clears its active state after pointer cancellation,
  capture loss, window blur, or a pointer release outside the slider.
- A direct volume edit is cancelled, rather than sent to the speaker, when the
  speaker enters standby while the edit field has focus.
- Setup now closes a running KEF Controller instance automatically before
  replacing its files, without an extra confirmation dialog.
- The UI now shows a reconnecting notice after repeated controller update
  failures and clears it immediately when updates resume.
- Log records now include an authoritative Python log level in both disk and
  live UI streams. The UI no longer classifies messages by scanning their text
  for words such as `failed` or `could not`.
- Historical log files remain visible. A leading `WARN` or `ERROR` token is
  preserved; otherwise their severity defaults to INFO, while a leading
  lifecycle token still supplies the STEP, EVENT, or STATE category without
  scanning arbitrary fields such as `error=`.

## Accessibility and UI

- Named power-rule checkboxes, labelled volume control, live toast updates,
  error alerts, reduced-motion support, and modal focus management.
- Improved light-theme log-level chips, responsive log-page sizing, setup-note
  theme tokens, long speaker-name truncation, search clear-button handling,
  and the original left-aligned input-source grid.
- Added a launch skeleton and reduced unnecessary icon DOM replacement while
  changing volume or viewing simulated event settings.
- Moved stylesheet and localization data into independent static assets, with
  fixed HTML/CSS/JavaScript MIME types so Windows registry associations cannot
  prevent the UI from loading.

## Verification

- Added coverage for structured live log formatting and updated web UI source
  checks for the new structured log parser.
- The final onedir executable is emitted into `dist/`, while the Inno Setup
  installer is emitted into `installer/output/`; PyInstaller's temporary work
  files stay outside the repository.
