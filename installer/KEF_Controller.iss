[Setup]
AppId={{8D6C2E54-89C6-4B9F-AE63-8F8A2A814101}
AppName=KEF Controller
AppVersion=1.0.1
AppVerName=KEF Controller 1.0.1
DefaultDirName={autopf}\KEF Controller
DefaultGroupName=KEF Controller
DisableProgramGroupPage=yes
OutputDir=output
OutputBaseFilename=KEF_Controller_Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\KEF Controller.exe

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist\KEF Controller.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\KEF Controller"; Filename: "{app}\KEF Controller.exe"
Name: "{autodesktop}\KEF Controller"; Filename: "{app}\KEF Controller.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\KEF Controller.exe"; Description: "Launch KEF Controller"; Flags: nowait postinstall skipifsilent
