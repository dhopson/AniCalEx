; Inno Setup script for AniCal on Windows. Installs for the current user only
; (no administrator rights), adds a Start menu shortcut carrying the
; AppUserModelID that anical-notify's toasts use (D24), and leaves the library
; in %LOCALAPPDATA%\AniCal alone on uninstall.
; Build from the repository root, after dist\AniCal is complete:
;   iscc /DAppVersion=0.1.0 /DIconFile=AniCal.ico packaging\windows\anical.iss

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{DDBC16BF-37EF-4016-B7F8-28FBC30BC33C}
AppName=AniCal
AppVersion={#AppVersion}
AppVerName=AniCal {#AppVersion}
AppPublisher=AniCal contributors
AppPublisherURL=https://github.com/dhopson/AniCal
DefaultDirName={localappdata}\Programs\AniCal
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\..\out
OutputBaseFilename=AniCal-{#AppVersion}-windows-x64-setup
#ifdef IconFile
SetupIconFile={#IconFile}
#endif
UninstallDisplayIcon={app}\AniCal.exe
UninstallDisplayName=AniCal
LicenseFile=..\..\LICENSE
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Files]
Source: "..\..\dist\AniCal\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; Files of an older version that the new one no longer ships.
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\anical-notify"

[Icons]
Name: "{userprograms}\AniCal"; Filename: "{app}\AniCal.exe"; AppUserModelID: "AniCal"

[Registry]
; Written by anical-notify on first use; removed with the app.
Root: HKCU; Subkey: "Software\Classes\AppUserModelId\AniCal"; Flags: uninsdeletekey dontcreatekey

[Run]
Filename: "{app}\AniCal.exe"; Description: "Start AniCal"; Flags: nowait postinstall skipifsilent
