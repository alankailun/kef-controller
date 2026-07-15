#define AppExeName "KEF Controller.exe"
#define StableAppDir "{localappdata}\Programs\KEF Controller"
#define AppVersion "1.7.0"
#define WebView2RuntimeKey "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"

[Setup]
AppId={{8D6C2E54-89C6-4B9F-AE63-8F8A2A814101}
AppName=KEF Controller
AppVersion={#AppVersion}
AppVerName=KEF Controller {#AppVersion}
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
Source: "assets\MicrosoftEdgeWebView2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall ignoreversion

[Icons]
Name: "{autoprograms}\KEF Controller"; Filename: "{#StableAppDir}\{#AppExeName}"; WorkingDir: "{#StableAppDir}"; IconFilename: "{#StableAppDir}\{#AppExeName}"; IconIndex: 0
Name: "{autodesktop}\KEF Controller"; Filename: "{#StableAppDir}\{#AppExeName}"; WorkingDir: "{#StableAppDir}"; IconFilename: "{#StableAppDir}\{#AppExeName}"; IconIndex: 0; Check: ShouldCreateDesktopIcon

[Run]
Filename: "{tmp}\MicrosoftEdgeWebView2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "Installing Microsoft Edge WebView2 Runtime..."; Flags: waituntilterminated skipifdoesntexist; Check: NeedsWebView2Runtime
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
