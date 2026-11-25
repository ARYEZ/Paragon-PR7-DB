; Paragon PR7-DB installer (Inno Setup 6): SQLite databases, the way DB Browser for SQLite works.
;   iscc /DAppVersion=0.1.0 installer\pr7db.iss      (after pyinstaller pr7db.spec has made dist\PR7-DB)
;
; Per-user (no admin rights) into %LOCALAPPDATA%\Programs\PR7-DB, with a Start menu entry. Open with for SQLite
; files (ticked) is registered by PR7-DB itself (--register; --unregister on uninstall), HKCU only, so the installer,
; Tools > Open SQLite Files with PR7-DB and the command line share shell.py. It never takes .db files over.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
; AppId is permanent: it's how a new version finds and upgrades the installed one. Never change it.
AppId={{59D3D2F2-BE74-4683-B47A-BEE9B3265EC5}
AppName=Paragon PR7-DB
AppVersion={#AppVersion}
AppVerName=Paragon PR7-DB {#AppVersion}
AppPublisher=Paragon
VersionInfoVersion={#AppVersion}
PrivilegesRequired=lowest
DefaultDirName={autopf}\PR7-DB
DefaultGroupName=Paragon PR7-DB
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
UsePreviousTasks=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist
OutputBaseFilename=PR7-DB-Setup-{#AppVersion}
SetupIconFile=..\pr7db\icons\app.ico
UninstallDisplayIcon={app}\PR7-DB.exe
UninstallDisplayName=Paragon PR7-DB
WizardStyle=modern
WizardImageFile=wizard-large-1x.bmp,wizard-large-2x.bmp
WizardSmallImageFile=wizard-small-1x.bmp,wizard-small-2x.bmp
Compression=lzma2/max
SolidCompression=yes
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "dbfiles"; Description: "Open SQLite databases (.db, .sqlite, .sqlite3) with PR7-DB"; GroupDescription: "Windows:"
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\PR7-DB\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\NOTICE.md"; DestDir: "{app}"; Flags: ignoreversion

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\Paragon PR7-DB"; Filename: "{app}\PR7-DB.exe"; Comment: "SQLite databases"
Name: "{autodesktop}\Paragon PR7-DB"; Filename: "{app}\PR7-DB.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\PR7-DB.exe"; Parameters: "--register"; Tasks: dbfiles; Flags: runhidden waituntilterminated
Filename: "{app}\PR7-DB.exe"; Description: "{cm:LaunchProgram,Paragon PR7-DB}"; Flags: nowait postinstall skipifsilent
; Updates (Check for updates runs this installer with /SILENT /relaunch=1): start PR7-DB again when done.
Filename: "{app}\PR7-DB.exe"; Flags: nowait runasoriginaluser; Check: Relaunch

[UninstallRun]
Filename: "{app}\PR7-DB.exe"; Parameters: "--unregister"; Flags: runhidden waituntilterminated; RunOnceId: "Unregister"

[Code]
// True only when the updater asked for it (/relaunch=1): normal and CI installs behave as before.
function Relaunch: Boolean;
begin
  Result := ExpandConstant('{param:relaunch|0}') = '1';
end;
