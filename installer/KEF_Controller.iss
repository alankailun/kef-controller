#define AppExeName "KEF Controller.exe"
#define AppVersion "1.9.1"
#define WebView2RuntimeKey "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
#ifndef BuildSource
  #define BuildSource "..\dist\release-1.9.1\KEF Controller"
#endif

[Setup]
AppId={{8D6C2E54-89C6-4B9F-AE63-8F8A2A814101}
AppName=KEF Controller
AppVersion={#AppVersion}
AppVerName=KEF Controller {#AppVersion}
DefaultDirName={autopf}\KEF Controller
; Keep the location page available so a per-user installation can be placed
; on another drive (for example F:\KEF Controller).
DisableDirPage=no
DefaultGroupName=KEF Controller
DisableProgramGroupPage=yes
; Inno Setup packages are kept in the conventional installer/output folder.
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
Source: "{#BuildSource}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "assets\MicrosoftEdgeWebView2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall ignoreversion

[Icons]
Name: "{autoprograms}\KEF Controller"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#AppExeName}"; IconIndex: 0
Name: "{autodesktop}\KEF Controller"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#AppExeName}"; IconIndex: 0; Check: ShouldCreateDesktopIcon

[Run]
Filename: "{tmp}\MicrosoftEdgeWebView2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "Installing Microsoft Edge WebView2 Runtime..."; Flags: waituntilterminated skipifdoesntexist; Check: NeedsWebView2Runtime
Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Description: "Launch KEF Controller"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; pywebview normally stores its profile in AppData. This only removes a
; profile if a future build explicitly places it beneath the app runtime.
Type: filesandordirs; Name: "{app}\runtime\pywebview"

[Code]
procedure RunTaskKill(Args: String);
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), Args, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function IsControllerRunning(): Boolean;
begin
  Result := CheckForMutexes('KEFController_SingleInstance_Mutex');
end;

function WaitForControllerExit(MaxMilliseconds: Integer): Boolean;
var
  Elapsed: Integer;
begin
  Elapsed := 0;
  while IsControllerRunning() and (Elapsed < MaxMilliseconds) do
  begin
    Sleep(100);
    Elapsed := Elapsed + 100;
  end;
  Result := not IsControllerRunning();
end;

procedure CloseRunningApp();
begin
  if not IsControllerRunning() then
    exit;

  RunTaskKill('/F /IM "{#AppExeName}" /T');
  { Wait only long enough for Windows to release loaded EXE/DLL handles. }
  WaitForControllerExit(500);
end;

function CloseRunningAppForInstall(): Boolean;
begin
  Result := True;
  if not IsControllerRunning() then
    exit;

  { Close the previous instance automatically before replacing its files. }
  CloseRunningApp();
  if IsControllerRunning() then
  begin
    MsgBox(
      'KEF Controller could not be closed. Please close it manually, then try again.',
      mbError,
      MB_OK
    );
    Result := False;
  end;
end;

function WebView2VersionIsSupported(const Version: String): Boolean; forward;

function HasWebView2Runtime(): Boolean;
var
  Version: String;
  MachineKey: String;
begin
  if IsWin64 then
    MachineKey := 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{#WebView2RuntimeKey}'
  else
    MachineKey := 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{#WebView2RuntimeKey}';

  Version := '';
  if not RegQueryStringValue(HKLM, MachineKey, 'pv', Version) then
    RegQueryStringValue(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\{#WebView2RuntimeKey}', 'pv', Version);

  Result := WebView2VersionIsSupported(Version);
end;

function NeedsWebView2Runtime(): Boolean;
begin
  Result := not HasWebView2Runtime();
end;

function VersionComponent(const Version: String; ComponentIndex: Integer): Integer;
var
  Index: Integer;
  StartIndex: Integer;
  CurrentComponent: Integer;
begin
  StartIndex := 1;
  CurrentComponent := 0;
  for Index := 1 to Length(Version) + 1 do
  begin
    if (Index > Length(Version)) or (Version[Index] = '.') then
    begin
      if CurrentComponent = ComponentIndex then
      begin
        Result := StrToIntDef(Copy(Version, StartIndex, Index - StartIndex), 0);
        exit;
      end;
      CurrentComponent := CurrentComponent + 1;
      StartIndex := Index + 1;
    end;
  end;
  Result := 0;
end;

function WebView2VersionIsSupported(const Version: String): Boolean;
var
  Major: Integer;
  Minor: Integer;
  Build: Integer;
begin
  Major := VersionComponent(Version, 0);
  Minor := VersionComponent(Version, 1);
  Build := VersionComponent(Version, 2);
  Result := (Major > 86) or
    ((Major = 86) and (Minor > 0)) or
    ((Major = 86) and (Minor = 0) and (Build >= 622));
end;

function DesktopShortcutExists(): Boolean;
begin
  Result := FileExists(ExpandConstant('{autodesktop}\KEF Controller.lnk'));
end;

function ShouldCreateDesktopIcon(): Boolean;
begin
  Result := WizardIsTaskSelected('desktopicon') or DesktopShortcutExists();
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = wpReady then
    Result := CloseRunningAppForInstall();
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    CloseRunningApp();
end;
