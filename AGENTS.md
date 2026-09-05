# Build and release conventions

- Package this project with `./build.ps1` in PowerShell. It cleans and reuses
  the fixed build directories and produces the installer.
- Generated files belong in these locations only:
  - `build/`: temporary build files and logs.
  - `dist/KEF Controller/`: application and runtime files.
  - `installer/output/KEF_Controller_Setup.exe`: installer.
- Do not create version-suffixed or retry-suffixed build/dist directories or
  installer filenames, such as `dist_v1.9.9`, `build_rebuild`, or
  `KEF_Controller_Setup_v1.9.9.exe`. Reuse the fixed paths on every build.
- Keep versions in `installer/KEF_Controller.iss`, `installer/version_info.txt`,
  and `release_notes/RELEASE_NOTES_vX.Y.Z.md`.
- Deliver the fixed installer path. Packaging does not require installing or
  replacing the user's running application.
