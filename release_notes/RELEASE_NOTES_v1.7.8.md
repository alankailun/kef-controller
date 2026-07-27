# KEF Controller 1.7.8 - Reliable Controls and Structured Logs

Release date: 2026-07-26

## Fixed

- Volume dragging now always clears its active state after pointer cancellation,
  capture loss, window blur, or a pointer release outside the slider.
- A direct volume edit is cancelled, rather than sent to the speaker, when the
  speaker enters standby while the edit field has focus.
- The UI now shows a reconnecting notice after repeated controller update
  failures and clears it immediately when updates resume.
- Log records now include an authoritative Python log level in both disk and
  live UI streams. The UI no longer classifies messages by scanning their text
  for words such as `failed` or `could not`.

## Accessibility and UI

- Named power-rule checkboxes, labelled volume control, live toast updates,
  error alerts, reduced-motion support, and modal focus management.
- Improved light-theme log-level chips, responsive log-page sizing, setup-note
  theme tokens, long speaker-name truncation, and search clear-button handling.
- Added a launch skeleton and reduced unnecessary icon DOM replacement while
  changing volume or viewing simulated event settings.

## Verification

- Added coverage for structured live log formatting and updated web UI source
  checks for the new structured log parser.
