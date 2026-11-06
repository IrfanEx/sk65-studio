; Inno Setup script for Womier SK65 Studio.
; Build the exe first:
;   pyinstaller --noconfirm --onefile --windowed --uac-admin --icon "assets\via.ico" --add-data "assets\via.ico;assets" --name "Womier_SK65_Studio" sk65_studio.py
; Then compile with: ISCC.exe installer.iss

#define AppName "Womier SK65 Studio"
#define AppVersion "1.0.0"
#define AppExe "Womier_SK65_Studio.exe"

[Setup]
AppId={{6E1B4C7A-3F2D-4B8E-9A51-0C7D2E8F4A93}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=SK65 Studio
DefaultDirName={autopf}\Womier SK65 Studio
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=Womier_SK65_Studio_Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#AppExe}
SetupIconFile=assets\via.ico

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion
; Keep the user's profile on upgrade.
Source: "sk65_profile.json"; DestDir: "{app}"; Flags: onlyifdoesntexist uninsneveruninstall

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent shellexec
