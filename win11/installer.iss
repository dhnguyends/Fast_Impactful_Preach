; Inno Setup script — FastImpact Preach installer for Windows 10/11 (x64).
; Built by win11\build.ps1 (it passes /DAppVersion=… and prepares build\dist and build\model).

#ifndef AppVersion
  #define AppVersion "2.0.0"
#endif
#define AppName "FastImpact Preach"
#define AppExe "FastImpactPreach.exe"

[Setup]
AppId={{7C1E3B2A-5F4D-4E8B-9A61-FA57140AC7E5}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Eglise Evangélique Internationale de Genève (EEIG GVA)
AppPublisherURL=https://shorts.dhnguyen68.eu/about
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Per-user install by default (no administrator rights needed); "for all users" stays possible.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=installer
OutputBaseFilename=FastImpactPreach-Setup-{#AppVersion}
SetupIconFile=build\app.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/normal
SolidCompression=no
DiskSpanning=no
WizardStyle=modern
LicenseFile=licenses\LICENSE-NOTICE.txt

[Languages]
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
fr.CompMain=Application FastImpact Preach (obligatoire)
en.CompMain=FastImpact Preach application (required)
fr.CompModel=Modèle de transcription Whisper large-v3-turbo (1,6 Go) — recommandé, évite un téléchargement au premier usage
en.CompModel=Whisper large-v3-turbo speech model (1.6 GB) — recommended, avoids a download on first use
fr.Launch=Lancer FastImpact Preach
en.Launch=Launch FastImpact Preach

[Components]
Name: "main"; Description: "{cm:CompMain}"; Types: full compact custom; Flags: fixed
#ifndef NoModel
Name: "model"; Description: "{cm:CompModel}"; Types: full custom
#endif

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "build\dist\FastImpactPreach\*"; DestDir: "{app}"; Components: main; Flags: ignoreversion recursesubdirs createallsubdirs
#ifndef NoModel
Source: "build\model\faster-whisper-large-v3-turbo\*"; DestDir: "{app}\model\faster-whisper-large-v3-turbo"; Components: model; Flags: ignoreversion
#endif
Source: "licenses\*"; DestDir: "{app}\licenses"; Components: main; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Components: main; Flags: ignoreversion
Source: "..\docs\YouTube_Setup_Guide.docx"; DestDir: "{app}\docs"; Components: main; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:Launch}"; Flags: nowait postinstall skipifsilent

; User data (%LOCALAPPDATA%\FastImpactPreach: projects, API keys, logs) is kept on uninstall on purpose.
