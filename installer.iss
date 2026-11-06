; Inno Setup script for Womier SK65 Studio.
; Build the exe first:
;   pyinstaller --noconfirm --onefile --windowed --icon "assets\via.ico" --add-data "assets\via.ico;assets" --hidden-import pystray._win32 --name "Womier_SK65_Studio" sk65_studio.py
; Then compile with: ISCC.exe installer.iss
; Releases pass the tag's version with /DAppVersion=x.y.z

#define AppName "Womier SK65 Studio"
#ifndef AppVersion
  #define AppVersion "1.6.0"
#endif
#define AppExe "Womier_SK65_Studio.exe"

[Setup]
AppId={{6E1B4C7A-3F2D-4B8E-9A51-0C7D2E8F4A93}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=SK65 Studio
AppPublisherURL=https://github.com/IrfanEx/sk65-studio
; Same name as INSTANCE_NAME in sk65_studio.py, so Setup asks to close a running copy.
AppMutex=WomierSK65Studio
DefaultDirName={autopf}\Womier SK65 Studio
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=Womier_SK65_Studio_Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExe}
SetupIconFile=assets\via.ico

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent runasoriginaluser
