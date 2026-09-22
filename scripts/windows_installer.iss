#ifndef AppVersion
  #error AppVersion is required
#endif
#ifndef SourceDir
  #error SourceDir is required
#endif
#ifndef OutputDir
  #error OutputDir is required
#endif
[Setup]
AppId={{7ED2ECF0-0404-493C-AC2D-7F8BE4B4504A}
AppName=Haru
AppVersion={#AppVersion}
AppPublisher=Haru
AppPublisherURL=https://github.com/U1XOvO/Haru
DefaultDirName={localappdata}\Programs\Haru
DefaultGroupName=Haru
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename=Haru-{#AppVersion}-windows-x64-setup
SetupIconFile={#SourceDir}\_internal\ui\Haru.ico
UninstallDisplayIcon={app}\versions\{#AppVersion}\Haru.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
CloseApplicationsFilter=Haru.exe,HaruBackend.exe
RestartApplications=no
SetupMutex=Haru.Setup
AppMutex=Haru.Desktop
#ifdef SignTool
SignTool={#SignTool}
SignedUninstaller=yes
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}\versions\{#AppVersion}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Haru"; Filename: "{app}\versions\{#AppVersion}\Haru.exe"
Name: "{autodesktop}\Haru"; Filename: "{app}\versions\{#AppVersion}\Haru.exe"; Tasks: desktopicon

[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; Flags: unchecked

[Run]
Filename: "{app}\versions\{#AppVersion}\Haru.exe"; Description: "Open Haru"; Flags: nowait postinstall skipifsilent

[Code]
function WebViewInstalled: Boolean;
var Version: String;
begin
  Result := (RegQueryStringValue(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0'))
    or (RegQueryStringValue(HKLM32, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0'));
end;

function InitializeSetup: Boolean;
var ErrorCode: Integer;
begin
  Result := WebViewInstalled;
  if not Result then begin
    if not WizardSilent then begin
      MsgBox('Haru requires Microsoft Edge WebView2 Runtime (x64). Install it from Microsoft, then run this installer again.', mbInformation, MB_OK);
      ShellExec('open', 'https://developer.microsoft.com/microsoft-edge/webview2/', '', '', SW_SHOWNORMAL, ewNoWait, ErrorCode);
    end;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var Version: String; OldVersion, NewVersion: Int64;
begin
  Result := '';
  if RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{7ED2ECF0-0404-493C-AC2D-7F8BE4B4504A}_is1', 'DisplayVersion', Version) then
    if StrToVersion(Version, OldVersion) and StrToVersion('{#AppVersion}', NewVersion) then
    if ComparePackedVersion(OldVersion, NewVersion) > 0 then
      Result := 'A newer Haru version is installed. Downgrading may damage learning data and is blocked.';
end;

// User data is outside {app}; uninstall and upgrade never delete it.
