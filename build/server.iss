; LAN Messenger Server - Inno Setup script
; Compiled by build\build.py (which passes AppVersion, DistDir and RootDir).
; Manual compile: ISCC.exe /DAppVersion=1.1.0 /DDistDir=..\build\dist /DRootDir=.. server.iss

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef DistDir
  #define DistDir "dist"
#endif
#ifndef RootDir
  #define RootDir ".."
#endif

#define AppName   "LAN Messenger Server"
#define ExeName   "LANMessengerServer.exe"
#define DataDir   "{commonappdata}\LAN Messenger Server"
#define FwRule    "LAN Messenger Server"
#define PS        "{sys}\WindowsPowerShell\v1.0\powershell.exe"

[Setup]
AppId={{8F3C2A51-6B1E-4C7D-9A2F-5E0B7D1C4A11}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=LAN Messenger
VersionInfoVersion={#AppVersion}
VersionInfoDescription={#AppName} Setup
DefaultDirName={autopf}\LAN Messenger Server
DefaultGroupName=LAN Messenger Server
DisableProgramGroupPage=yes
OutputBaseFilename=LANMessenger-Server-Setup-{#AppVersion}
SetupIconFile={#RootDir}\assets\app.ico
UninstallDisplayIcon={app}\{#ExeName}
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
; ask to close a server/console running in this Windows session before files are replaced
; (the background service is stopped automatically in PrepareToInstall)
AppMutex=LANMessengerServerMutex
CloseApplications=yes
RestartApplications=no
InfoAfterFile=server_after_install.txt

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "firewall";    Description: "Allow the server through Windows Firewall (needed for other PCs to connect)"; GroupDescription: "Network:"
Name: "service";     Description: "Run as a background service: starts with Windows, even when nobody is logged in (recommended)"; GroupDescription: "How to run the server:"; Flags: exclusive
Name: "autostart";   Description: "Run in the system tray of the logged-in user (starts at sign-in)"; GroupDescription: "How to run the server:"; Flags: exclusive unchecked
Name: "desktopicon"; Description: "Create a desktop shortcut for the console"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Dirs]
; data folder: writable by the (non-admin) user who runs the server; never removed automatically
Name: "{#DataDir}"; Permissions: users-modify; Flags: uninsneveruninstall

[Files]
Source: "{#DistDir}\LANMessengerServer\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "service.ps1"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\LAN Messenger Server console"; Filename: "{app}\{#ExeName}"
Name: "{group}\Connect to another server...";  Filename: "{app}\{#ExeName}"; Parameters: "--console"
Name: "{group}\Server data folder";         Filename: "{#DataDir}"
Name: "{group}\Uninstall LAN Messenger Server"; Filename: "{uninstallexe}"
Name: "{autodesktop}\LAN Messenger Server console"; Filename: "{app}\{#ExeName}"; Tasks: desktopicon

[Registry]
Root: HKLM; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "LANMessengerServer"; \
  ValueData: """{app}\{#ExeName}"" --minimized"; Tasks: autostart; Flags: uninsdeletevalue

[Run]
; firewall rule for the program itself (covers the chat/file TCP port and the discovery UDP port, whatever they are set to)
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""{#FwRule}"""; Flags: runhidden; Tasks: firewall
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""{#FwRule}"" dir=in action=allow program=""{app}\{#ExeName}"" enable=yes profile=any"; \
  Flags: runhidden; Tasks: firewall; StatusMsg: "Configuring Windows Firewall..."
Filename: "{#PS}"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\service.ps1"" install";   Flags: runhidden waituntilterminated; Tasks: service; StatusMsg: "Installing and starting the background service..."
Filename: "{#PS}"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\service.ps1"" uninstall"; Flags: runhidden waituntilterminated; Tasks: not service
Filename: "{app}\{#ExeName}"; Description: "Open the server console"; Flags: postinstall nowait skipifsilent runasoriginaluser

[UninstallRun]
Filename: "{#PS}"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\service.ps1"" uninstall"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveService"
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""{#FwRule}"""; Flags: runhidden; RunOnceId: "RemoveFirewallRule"

[Code]
{ upgrade: stop a running background service before files are replaced (it is started again by
  the "service" task, or stays removed if the tray mode was chosen) }
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  Exec(ExpandConstant('{sys}\schtasks.exe'), '/End /TN "LAN Messenger Server"', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /FI "IMAGENAME eq LANMessengerServer.exe" /FI "USERNAME eq SYSTEM"', '',
       SW_HIDE, ewWaitUntilTerminated, Code);
  Sleep(1000);
  Result := '';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataPath: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataPath := ExpandConstant('{#DataDir}');
    if DirExists(DataPath) and (not UninstallSilent) then
      if MsgBox('Do you also want to DELETE all server data?' + #13#10 + #13#10 +
                'This removes every account, message and shared file in:' + #13#10 + DataPath + #13#10 + #13#10 +
                'Choose "No" to keep the data (recommended if you reinstall or upgrade later).',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataPath, True, True, True);
  end;
end;
