# KEF Controller

## Overview

KEF Controller is a Windows tray application for supported KEF W2 / KEF
Connect speakers. It gives a Windows PC predictable local control over KEF
speaker power behavior, input source, volume, target selection, event testing,
and logs.

Supported models:

- LS50 Wireless II
- LSX II
- LS60 Wireless

The app is designed for Windows. It uses Win32 session, power, shutdown,
display, lid, network-interface, startup, and tray APIs.

## What It Can Do

### Home Page

- Show the selected speaker identity, model, IP, power state, and availability.
- Wake the speaker manually.
- Put the speaker into standby manually.
- Change the speaker input source.
- Change speaker volume with debounced background updates.
- Refresh speaker state without blocking the UI.

### Tray App

- Run quietly from the Windows system tray.
- Show the current speaker/status in the tray tooltip and menu.
- Open the main controller window.
- Wake the speaker from the tray menu.
- Put the speaker into standby from the tray menu.
- Exit cleanly.

### Speaker Selection

- Scan the local network for supported KEF speakers.
- Show discovered speakers as soon as candidates are found.
- Cancel an active scan when the selection window is closed.
- Prefer the last known speaker IP and the default-route LAN to avoid wasting
  time on VPN or virtual-adapter subnets in common home-network setups.
- Enter a manual IP address, MAC address, or both.
- Verify manual targets when possible.
- Save a MAC-only target so the app can recover the speaker IP later.

### Speaker Power Behavior

The Settings page exposes the current automation toggles:

- Wake Speaker When the App Starts
- Put Speaker in Standby When the Screen Turns Off
- Wake Speaker When Windows Unlocks
- Put Speaker in Standby When Windows Locks
- Put Speaker in Standby When Windows Sleeps
- Put Speaker in Standby When Windows Shuts Down

The screen-off trigger listens to the Windows console display power-setting
notification. It is useful on Windows 11 Modern Standby systems because the
screen-off signal can arrive before a traditional suspend notification. It is
still a normal Windows display-power event, not a Windows-11-only feature.

### Windows Power and Session Handling

- Listen for Windows lock/unlock notifications.
- Listen for sleep/resume notifications.
- Listen for shutdown/sign-out/end-session notifications.
- Listen for lid-close notifications.
- Listen for screen on/off/dim notifications.
- Restart speaker event polling after resume.
- Use generation checks and deadlines so stale standby/wake work is not sent
  after the desired state changes.
- Keep Windows message-pump callbacks short by dispatching time-sensitive work
  off the pump thread.

### Fast Standby

- Maintain prewarmed speaker sockets while the runtime is active.
- Use a fast prewarmed standby path for lock, display-off, lid-close, sleep,
  and shutdown windows.
- Fall back to bounded fire-and-forget HTTP standby when the prewarmed path is
  unavailable.
- Avoid blocking network calls in Windows event handlers.
- Keep standby logs aligned so the outer action and socket send use the same
  reason, such as `DISPLAY_OFF`, `WTS_SESSION_LOCK`, or `PBT_APMSUSPEND`.

### Wake Behavior

- Wake on app startup when enabled.
- Wake after resume only when Windows unlocks by default.
- Wait briefly after unlock/resume so the local network can become reachable.
- Retry wake attempts with bounded delays.
- Switch to the configured default input after a successful wake.

### IP Recovery and Discovery

- Store the selected speaker identity in `state.json`.
- Verify the current IP before using it.
- Recover the speaker IP from the stored KEF identity/MAC when the IP changes.
- Skip expensive discovery when the local route to the current target is
  clearly unavailable.
- Keep manual scans separate from automatic recovery so selecting a speaker
  does not accidentally change the current target until the user chooses one.

### Logs and Diagnostics

- Show application logs in the UI.
- Reload logs from the UI.
- Open the log folder from the UI.
- Record structured power, session, network, discovery, wake, standby, and
  startup events.
- Record Wi-Fi diagnostics from the speaker when available.
- Keep runtime state and logs under the current Windows user profile.

### Event Tests

The Event Tests page can simulate these behaviors:

- Startup
- Shutdown
- Lock
- Unlock
- Display Off
- Sleep

Tests respect the same settings as real events. If a behavior is disabled, the
test is skipped and the UI explains why.

### Windows Startup

- Register startup through Registry Run.
- Register startup through Task Scheduler.
- Disable startup registration.
- Show the effective startup state in Settings.
- Repair stale startup entries when possible.
- Use a stable installed launcher path for shortcuts and startup entries.

Registry Run is the simple per-user Windows startup method. Task Scheduler is
usually faster/earlier after sign-in and is the better choice if you want KEF
Controller to start as soon as possible and take over speaker wake/standby
handling quickly.

## Runtime Files

User state is stored under the current Windows profile:

```text
%LocalAppData%\KEF Controller\
  config.json
  state.json
  logs\
    kef_controller.log
```

The packaged executable and installer location stay separate from runtime
state. User settings, selected speaker identity, recovered IP state, and logs
are not written next to the `.exe`.

## Install and Startup Paths

For installed builds, the launcher path is intentionally stable:

```text
%LocalAppData%\Programs\KEF Controller\KEF Controller.exe
```

The Inno Setup installer still lets the user choose the main install directory.
It also writes a synchronized copy to the stable LocalAppData path above. Start
Menu shortcuts, optional desktop shortcuts, post-install launch, and Windows
startup entries use that stable path so they do not depend on whether the user
installed the app on `C:`, `F:`, OneDrive, or another location.

At runtime, a frozen executable also calls the startup launch helper to keep the
stable copy up to date before registering startup entries. If the stable copy
cannot be updated, the app logs the failure and falls back to the current
executable.

## Project Structure

```text
kef_controller/
  main_gui.py                  GUI entry point: tray app + main window
  main_background.py           Headless entry point for message-pump runtime only
  KEF Controller.spec          PyInstaller build configuration
  requirements.txt
  installer/
    KEF_Controller.iss         Inno Setup installer script
    assets/                    installer and application icon assets
  release_notes/               versioned release notes
  kef_app/
    config/                    AppConfig, SystemConfig, and user settings
    storage/                   JSON config and speaker state stores
    devices/                   KEF backend, model helpers, and network discovery
    controller/                wake/standby actions, event handling, recovery
    platform/
      windows/                 Win32 session/power APIs and startup registration
        startup/               Registry Run and Task Scheduler helpers
    runtime/                   startup bootstrap, logging, and headless loop
    ui/                        PySide6/QFluentWidgets tray app and pages
      assets/icons/            themed SVG icons used by the settings UI
      logs/                    UI log history and handlers
      settings/                settings cards, save logic, startup sync
  tests/                       unittest coverage for config, UI, startup, events
```

## Run From Source

Use Windows `cmd.exe` or PowerShell.

Create or update the virtual environment, then install dependencies:

```bat
cd /d "path\to\kef_controller"
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

GUI mode:

```bat
python main_gui.py
```

Headless mode:

```bat
python main_background.py
```

## Build the EXE With PyInstaller

Recommended build:

```bat
cd /d "path\to\kef_controller"
.venv\Scripts\activate
.venv\Scripts\pyinstaller "KEF Controller.spec"
```

Main output:

```text
dist\KEF Controller.exe
```

The `.spec` file uses `main_gui.py`, builds a windowed one-file executable,
includes the QFluentWidgets and SVG resources the UI needs, keeps the Modern
Windows Qt style plugin, keeps the software OpenGL fallback, and trims unused
Qt/PySide6 modules and plugins to keep the bundle smaller.

## Create an Installer With Inno Setup

Install Inno Setup first. The compiler is usually:

```text
C:\Program Files (x86)\Inno Setup 6\ISCC.exe
```

Make sure the PyInstaller output already exists:

```text
dist\KEF Controller.exe
```

Compile the installer:

```bat
cd /d "path\to\kef_controller"
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\KEF_Controller.iss
```

Expected installer output:

```text
installer\output\KEF_Controller_Setup.exe
```

The installer closes any running `KEF Controller.exe` before install/uninstall
so files can be replaced cleanly.

## Test

Run the unit tests with:

```bat
cd /d "path\to\kef_controller"
.venv\Scripts\activate
python -m unittest discover -s tests
```

Run a bytecode compile check with:

```bat
python -m compileall -q kef_app tests
```

## Release Notes

Versioned release notes live in:

```text
release_notes/
```

The current installer version is declared in:

```text
installer\KEF_Controller.iss
```

## Suggested Release Workflow

1. Update the version in `installer/KEF_Controller.iss` when needed.
2. Update the matching file under `release_notes/`.
3. Run the unit tests and `compileall`.
4. Build `dist\KEF Controller.exe` with `KEF Controller.spec`.
5. Test the generated `.exe`.
6. Compile `installer\KEF_Controller.iss`.
7. Test the installer, shortcuts, startup method, shutdown/lock/sleep/display
   behavior, and logs on a Windows machine.
8. Commit, tag, and push the release.

## Notes

- `main_gui.py` is the normal packaged entry point.
- `main_background.py` is still available for running the headless runtime
  directly.
- `RegisterApplicationRestart` is not used. The app handles
  shutdown/session messages itself and exits quickly on Restart Manager
  `CLOSEAPP` requests to avoid Windows reporting an application hang.
- Fresh unsigned builds may trigger SmartScreen or antivirus warnings during
  internal testing.
- Some settings-page icons are SVGs adapted from Microsoft Fluent UI System
  Icons, which are distributed under the MIT license.
