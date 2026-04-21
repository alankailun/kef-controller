# KEF Controller

## Overview

KEF Controller is a Windows desktop tool for supported KEF W2 / KEF Connect speakers.

Supported models:

- LS50 Wireless II
- LSX II
- LS60 Wireless

Current speaker power features:

- wake the speaker when the app starts
- put the speaker in standby when Windows shuts down
- put the speaker in standby when Windows locks
- wake the speaker when Windows unlocks
- put the speaker in standby when Windows sleeps
- recover speaker identity after IP changes
- run as a tray app with a GUI

## Project Structure

```text
kef_controller/
  main_gui.py
  main_headless.py
  README.md
  README.en.md
  README.zh-CN.md
  KEF Controller.spec
  requirements.txt
  installer/
    KEF_Controller.iss
  kef_headless_split/
    __init__.py
    backends.py
    controller.py
    discovery.py
    headless_runtime.py
    logging_setup.py
    models.py
    appdata/
      config.py
      config_store.py
      state_store.py
      system_config.py
      user_settings.py
    controller_support/
    platform_windows/
      windows_api.py
      windows_startup.py
      startup_support/
    ui/
      home_interface.py
      main_window.py
      test_interface.py
      tray_app.py
      logs/
      settings/
```

## Runtime Files

On first launch, the app creates runtime files under `%LocalAppData%\KEF Controller\`:

- `config.json`
- `state.json`
- log files

The packaged executable stays clean. User settings, runtime state, and logs are stored in the current Windows user profile instead of next to the `.exe`.

## Run From Source

Use Windows `cmd.exe`.

GUI mode:

```bat
cd /d "path\to\kef_controller"
.venv\Scripts\activate
python main_gui.py
```

Headless mode:

```bat
cd /d "path\to\kef_controller"
.venv\Scripts\activate
python main_headless.py
```

## Build the EXE With PyInstaller

Direct command in `cmd.exe`:

```bat
cd /d "path\to\kef_controller"
.venv\Scripts\activate
.venv\Scripts\pyinstaller --noconsole --onefile --name "KEF Controller" --collect-all qfluentwidgets main_gui.py
```

What this command does:

- `--noconsole`: build a windowed app without a console window
- `--onefile`: pack the app into one executable
- `--name "KEF Controller"`: set the output file name
- `--collect-all qfluentwidgets`: include QFluentWidgets resources
- `main_gui.py`: use the GUI entry point

Main output:

```text
dist\KEF Controller.exe
```

## Build With the Existing `.spec` File

This repository also includes:

- [KEF Controller.spec](KEF%20Controller.spec)

Build from the spec with:

```bat
cd /d "path\to\kef_controller"
.venv\Scripts\activate
.venv\Scripts\pyinstaller "KEF Controller.spec"
```

Use the `.spec` flow when you want repeatable packaging with a fixed build configuration.

## Create an Installer With Inno Setup

Install Inno Setup first. The compiler is usually:

```text
C:\Program Files (x86)\Inno Setup 6\ISCC.exe
```

Make sure the PyInstaller output already exists:

```text
dist\KEF Controller.exe
```

This repository includes the installer script:

- [installer/KEF_Controller.iss](installer/KEF_Controller.iss)

Compile it from `cmd.exe`:

```bat
cd /d "path\to\kef_controller"
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\KEF_Controller.iss
```

Expected installer output:

```text
output\KEF_Controller_Setup.exe
```

## Suggested Build Workflow

1. Activate `.venv`.
2. Build `dist\KEF Controller.exe` with PyInstaller.
3. Test the generated `.exe`.
4. Compile `installer\KEF_Controller.iss`.
5. Test the installer on a clean Windows machine if possible.

## Notes

- Windows startup support now has two paths: normal Registry Run and optional Task Scheduler startup.
- The app writes config, state, and logs to `%LocalAppData%\KEF Controller\`.
- If SmartScreen or antivirus warns about a fresh unsigned build, that is common for internal test builds.
- If you rename the app or the output executable, update both the PyInstaller command and the Inno Setup script.
