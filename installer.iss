#define AppName "AtoZ Voice Studio"
#define AppVersion "0.7.5"
#define AppPublisher "crowsley"
#define AppExeName "AtoZVoiceStudio.exe"

[Setup]
AppId={{92BDCE60-76F7-44EF-BE88-C118598C2E08}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\AtoZ Voice Studio
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=installer-output
OutputBaseFilename=AtoZ-Voice-Studio-Setup
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no
UninstallDisplayName={#AppName}
WizardStyle=modern

[Files]
Source: "dist\AtoZVoiceStudio\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
Type: files; Name: "{app}\YouTubeAIStudio.exe"
Type: files; Name: "{app}\YouTubeAIStudioUpdater.exe"
Type: files; Name: "{autodesktop}\YouTube AI Studio.lnk"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
