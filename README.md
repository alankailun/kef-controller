# KEF Controller

KEF Controller is a Windows tray app for KEF W2 / KEF Connect speakers.

- English documentation: [README.en.md](README.en.md)
- 中文说明: [README.zh-CN.md](README.zh-CN.md)
- Release notes: [release_notes/](release_notes/)

## Quick Overview

- Supported speakers: LS50 Wireless II, LSX II, LS60 Wireless
- Wake the speaker when the app starts or when Windows unlocks
- Put the speaker in standby when the screen turns off, Windows locks,
  Windows sleeps, or Windows shuts down
- Select a speaker from the local network or enter a manual IP/MAC target
- Recover the selected speaker when its IP changes
- Run as a Windows tray app with settings, logs, and event tests
- Build as a PyInstaller onedir application and optional Inno Setup installer

## Build and Release Files

- Run `./build.ps1` in PowerShell to build the application and installer. It
  cleans and reuses `build/` and `dist/` and overwrites the fixed installer
  filename. Do not add version or retry suffixes to generated paths.
- PyInstaller spec: [KEF Controller.spec](KEF%20Controller.spec)
- Inno Setup script: [installer/KEF_Controller.iss](installer/KEF_Controller.iss)
- Versioned release notes: [release_notes/](release_notes/)
- Installer output: `installer/output/KEF_Controller_Setup.exe`

## Screenshots

<img width="1470" height="1079" alt="KEF Controller home page" src="https://github.com/user-attachments/assets/e4585ab3-28c7-4920-aa69-da52f8263170" />
<img width="1470" height="1079" alt="KEF Controller settings page" src="https://github.com/user-attachments/assets/32f21355-b34a-493d-92ae-6045a938e4bc" />
<img width="1470" height="1079" alt="KEF Controller logs page" src="https://github.com/user-attachments/assets/5680acc8-bb4d-494c-b6b0-d6fc5007943a" />
<img width="1470" height="1079" alt="KEF Controller event tests page" src="https://github.com/user-attachments/assets/fac464de-5915-4d4e-9760-840808aa860a" />
<img width="1470" height="1079" alt="KEF Controller tray app" src="https://github.com/user-attachments/assets/195020aa-6f4f-49a9-8c95-51ab375c382a" />
