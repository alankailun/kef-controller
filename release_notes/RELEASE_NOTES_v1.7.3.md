# KEF Controller 1.7.3 - Fast Onedir Deployment

Release date: 2026-07-18

## Highlights

- **Fast onedir startup.** KEF Controller now ships as a launcher plus a
  `runtime` directory instead of a self-extracting onefile executable. Normal
  startup, Windows login startup, and the WebView2 host no longer unpack a
  temporary application image before running.
- **Cleaner installation and upgrades.** Inno Setup installs the complete
  application directory in one operation, closes the running process tree
  before updating files, and detects the existing single-instance mutex.
- **No runtime self-copying.** Installation is now solely Inno Setup's job.
  A frozen executable launched outside the installed folder is logged as such
  but is never copied by the application without the user's action.
- **Less antivirus friction.** UPX remains disabled; Inno Setup performs the
  final solid LZMA compression for the whole onedir payload.

## Verification

- Added a startup-registration regression test proving an external onedir
  launcher is not self-copied.
- Validated the onedir output layout, version resources, Inno Setup package,
  and the complete automated test suite.
