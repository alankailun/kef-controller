#define AppExeName "KEF Controller.exe"
#define StableAppDir "{localappdata}\Programs\KEF Controller"

[Setup]
AppId={{8D6C2E54-89C6-4B9F-AE63-8F8A2A814101}
AppName=KEF Controller
AppVersion=1.6.1
AppVerName=KEF Controller 1.6.1
DefaultDirName={autopf}\KEF Controller
DefaultGroupName=KEF Controller
DisableProgramGroupPage=yes
OutputDir=output
OutputBaseFilename=KEF_Controller_Setup
SetupIconFile=assets\setup-icon.ico
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#AppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\{#AppExeName}"; DestDir: "{#StableAppDir}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\KEF Controller"; Filename: "{#StableAppDir}\{#AppExeName}"; WorkingDir: "{#StableAppDir}"; IconFilename: "{#StableAppDir}\{#AppExeName}"; IconIndex: 0
Name: "{autodesktop}\KEF Controller"; Filename: "{#StableAppDir}\{#AppExeName}"; WorkingDir: "{#StableAppDir}"; IconFilename: "{#StableAppDir}\{#AppExeName}"; IconIndex: 0; Check: ShouldCreateDesktopIcon

[Run]
Filename: "{#StableAppDir}\{#AppExeName}"; WorkingDir: "{#StableAppDir}"; Description: "Launch KEF Controller"; Flags: nowait postinstall skipifsilent

[Code]
procedure RunTaskKill(Args: String);
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), Args, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

procedure CloseRunningApp();
begin
  RunTaskKill('/IM "{#AppExeName}" /T');
  Sleep(1500);
  RunTaskKill('/F /IM "{#AppExeName}" /T');
end;

function DesktopShortcutExists(): Boolean;
begin
  Result := FileExists(ExpandConstant('{autodesktop}\KEF Controller.lnk'));
end;

function ShouldCreateDesktopIcon(): Boolean;
begin
  Result := WizardIsTaskSelected('desktopicon') or DesktopShortcutExists();
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  CloseRunningApp();
  Result := '';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    CloseRunningApp();
end;
