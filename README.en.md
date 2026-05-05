# KEF Controller

## Overview

KEF Controller is a Windows tray application for supported KEF W2 / KEF Connect speakers.

Supported models:

- LS50 Wireless II
- LSX II
- LS60 Wireless

Current speaker power features:

- wake the speaker when the app starts
- put the speaker in standby when Windows shuts down, signs out, or ends the session
- put the speaker in standby when Windows locks
- wake the speaker when Windows unlocks
- put the speaker in standby when Windows sleeps
- recover the speaker after IP changes by using the target MAC address
- scan the local network and select the target speaker from the UI
- run from the Windows system tray with a main window, settings, logs, and event tests

## Project Structure

```text
kef_controller/
  main_gui.py                  GUI entry point: tray app + main window
  main_background.py           headless entry point for message-pump runtime only
  KEF Controller.spec          PyInstaller build configuration
  requirements.txt
  installer/
    KEF_Controller.iss         Inno Setup installer script
    assets/                    installer and application icon assets
  kef_app/
    config/                    AppConfig, SystemConfig, and user settings
    storage/                   JSON config and speaker state stores
    devices/                   speaker backend, model helpers, and network discovery
    controller/                power controller, wake/standby actions, identity recovery
    platform/
      windows/                 Win32 events, session/power APIs, startup registration
        startup/               Registry Run and Task Scheduler helpers
    runtime/                   startup bootstrap, logging, and headless message loop
    ui/                        PySide6/QFluent tray app, main window, settings, logs
      logs/                    UI log history and handlers
      settings/                settings cards, save logic, startup sync
  tests/                       unittest coverage for config, startup, and event logic
```

## Runtime Files

The app keeps user data under the current Windows profile:

```text
%LocalAppData%\KEF Controller\
  config.json
  state.json
  logs\
    kef_controller.log
```

The packaged executable and installer location stay separate from runtime state. User settings, selected speaker identity, recovered IP state, and logs are not written next to the `.exe`.

## Startup and Install Paths

The app supports Windows startup through:

- Registry Run
- Task Scheduler
- Off

The settings UI shows the active startup method and can repair stale startup entries. Task Scheduler repair/removal may ask for administrator approval.

For installed builds, the launcher path is intentionally stable:

```text
%LocalAppData%\Programs\KEF Controller\KEF Controller.exe
```

The Inno Setup installer still lets the user choose the main install directory. It also writes a synchronized copy to the stable LocalAppData path above. Start Menu shortcuts, optional desktop shortcuts, post-install launch, and Windows startup entries use that stable path so they do not depend on whether the user installed the app on `C:`, `F:`, OneDrive, or another location.

At runtime, a frozen executable also calls the startup launch helper to keep the stable copy up to date before registering startup entries. If the stable copy cannot be updated, the app logs the failure and falls back to the current executable.

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

The `.spec` file uses `main_gui.py`, builds a windowed one-file executable, includes QFluentWidgets resources, and embeds the installer icon.

You can also build directly:

```bat
.venv\Scripts\pyinstaller --noconsole --onefile --name "KEF Controller" --collect-all qfluentwidgets main_gui.py
```

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
output\KEF_Controller_Setup.exe
```

The installer closes any running `KEF Controller.exe` before install/uninstall so files can be replaced cleanly.

## Test

Run the unit tests with:

```bat
cd /d "path\to\kef_controller"
.venv\Scripts\activate
python -m unittest discover -s tests
```

## Suggested Release Workflow

1. Activate `.venv`.
2. Run the unit tests.
3. Build `dist\KEF Controller.exe` with the `.spec` file.
4. Test the generated `.exe`.
5. Compile `installer\KEF_Controller.iss`.
6. Test the installer, shortcuts, startup method, shutdown/lock/sleep behavior, and logs on a Windows machine.

## Notes

- `main_gui.py` is the normal packaged entry point.
- `main_background.py` is still available for running the headless runtime directly.
- `RegisterApplicationRestart` is not used. The app handles shutdown/session messages itself and exits quickly on Restart Manager `CLOSEAPP` requests to avoid Windows reporting an application hang.
- Fresh unsigned builds may trigger SmartScreen or antivirus warnings during internal testing.
