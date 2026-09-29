; Quillo (client) - Inno Setup script
; Compiled by build\build.py (which passes AppVersion, DistDir and RootDir).
;
; Silent install for IT (e.g. via GPO / PDQ / a login script):
;   Quillo-Client-Setup-x.y.z.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SERVER=192.168.1.10
;   add /MERGETASKS="autostart" to start it with Windows, "!desktopicon" to skip the desktop shortcut

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef DistDir
  #define DistDir "dist"
#endif
#ifndef RootDir
  #define RootDir ".."
#endif

#define AppName  "Quillo"
; internal names below stay as before 1.6.0 so upgrades find the installed copy
#define ExeName  "LANMessenger.exe"
#define FwRule   "LAN Messenger Client"
#define RegKey   "Software\LAN Messenger"
; HKA = HKLM when installed for all users, HKCU when installed for me only

[Setup]
AppId={{2C7E9B44-1D3A-4F6B-8E25-9A0C6D3B7F22}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Utkarsh Tripathi
AppCopyright=(c) 2026 Utkarsh Tripathi - UT Community Licence 2.0
VersionInfoVersion={#AppVersion}
VersionInfoDescription={#AppName} Setup
DefaultDirName={autopf}\Quillo
DefaultGroupName=Quillo
UsePreviousGroup=no
DisableProgramGroupPage=yes
OutputBaseFilename=Quillo-Client-Setup-{#AppVersion}
LicenseFile={#DistDir}\LANMessenger\LICENSE.txt
SetupIconFile={#RootDir}\assets\app.ico
UninstallDisplayIcon={app}\{#ExeName}
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
PrivilegesRequired=admin
; "Install for me only" needs no administrator (goes to %LOCALAPPDATA%\Programs);
; "for all users" asks for admin. /CURRENTUSER or /ALLUSERS on the command line picks one.
PrivilegesRequiredOverridesAllowed=dialog commandline
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
; A running client is closed before files are replaced (see InitializeSetup). No AppMutex: in a silent
; install its "Quillo is running - OK / Cancel" box is answered Cancel, so every update started from inside
; Quillo (which is running, by definition) quit without installing anything.
CloseApplications=force
RestartApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "autostart";   Description: "Start Quillo when Windows starts"; GroupDescription: "Startup:"
Name: "firewall";    Description: "Allow Quillo through Windows Firewall (automatic server discovery)"; GroupDescription: "Network:"; Check: IsAdminInstallMode

[Files]
Source: "{#DistDir}\LANMessenger\*"; DestDir: "{app}"; Excludes: "client_config.json"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; shortcuts made by versions before 1.6.0, when the app was called LAN Messenger
Type: filesandordirs; Name: "{autoprograms}\LAN Messenger"
Type: files; Name: "{autodesktop}\LAN Messenger.lnk"

[Icons]
Name: "{group}\Quillo";           Filename: "{app}\{#ExeName}"
Name: "{group}\Quillo licence";   Filename: "{app}\LICENSE.txt"
Name: "{group}\Uninstall Quillo"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Quillo";     Filename: "{app}\{#ExeName}"; Tasks: desktopicon

[Registry]
Root: HKA; Subkey: "{#RegKey}"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "LANMessenger"; \
  ValueData: """{app}\{#ExeName}"" --minimized"; Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""{#FwRule}"""; Flags: runhidden; Tasks: firewall
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""{#FwRule}"" dir=in action=allow program=""{app}\{#ExeName}"" enable=yes profile=any"; \
  Flags: runhidden; Tasks: firewall; StatusMsg: "Configuring Windows Firewall..."
Filename: "{app}\{#ExeName}"; Description: "Start Quillo now"; Flags: postinstall nowait skipifsilent runasoriginaluser
; an update started from inside Quillo: open it again when done (not for IT's /VERYSILENT roll-outs)
Filename: "{app}\{#ExeName}"; Flags: nowait runasoriginaluser; Check: InAppUpdate

[UninstallRun]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""{#FwRule}"""; Flags: runhidden; RunOnceId: "RemoveFirewallRule"

[UninstallDelete]
Type: files; Name: "{app}\client_config.json"

[Code]
var
  ServerPage: TInputQueryWizardPage;

const
  ClientMutexes = 'LANMessengerClientMutex,Global\LANMessengerClientMutex';

{ Started by Quillo itself to update: Quillo 1.8 passes /SILENT, 1.9 and later also /UPDATE.
  IT roll-outs use /VERYSILENT and must not start Quillo as whoever runs the deployment. }
function InAppUpdate: Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if (CompareText(ParamStr(I), '/SILENT') = 0) or (CompareText(ParamStr(I), '/UPDATE') = 0) then
      Result := True;
end;

function QuilloRunning: Boolean;
begin
  Result := CheckForMutexes(ClientMutexes);
end;

{ Close every running Quillo of this PC and wait (up to 15 s) until it is gone. }
procedure CloseQuillo;
var
  Code, Waited: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM {#ExeName}', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Waited := 0;
  while QuilloRunning and (Waited < 15000) do
  begin
    Sleep(250);
    Waited := Waited + 250;
  end;
end;

function InitializeSetup: Boolean;
begin
  Result := True;
  if not QuilloRunning then Exit;
  if WizardSilent then
    CloseQuillo                     { an update or an IT roll-out: nobody is there to click OK }
  else
    while QuilloRunning do
      if MsgBox('Quillo is running.' + #13#10 + #13#10 +
                'Close Quillo (right-click its tray icon > Quit), then click OK to continue. ' +
                'Or click Cancel to leave this setup.', mbError, MB_OKCANCEL) <> IDOK then
      begin
        Result := False;
        Exit;
      end;
end;

{ /SERVER=... on the command line wins, otherwise the address used last time }
function InitialServer: String;
begin
  Result := Trim(ExpandConstant('{param:SERVER|}'));
  if Result = '' then
    if not RegQueryStringValue(HKA, '{#RegKey}', 'ServerAddress', Result) then
      Result := '';
end;

procedure InitializeWizard;
begin
  ServerPage := CreateInputQueryPage(wpSelectTasks,
    'Server address', 'Where is the Quillo server?',
    'Leave this EMPTY to find the server automatically on the network (recommended).' + #13#10 + #13#10 +
    'Only if this PC is on a different subnet / VLAN than the server, enter the server''s IP address ' +
    'or computer name, for example 192.168.1.10  (or 192.168.1.10:5150 if the port was changed).');
  ServerPage.Add('Server address (optional):', False);
  ServerPage.Values[0] := InitialServer;
end;

procedure SplitServer(S: String; var Host, Port: String);
var
  P: Integer;
begin
  S := Trim(S);
  P := Pos(':', S);
  if P > 0 then
  begin
    Host := Trim(Copy(S, 1, P - 1));
    Port := Trim(Copy(S, P + 1, 10));
  end else
  begin
    Host := S;
    Port := '5150';
  end;
end;

function ValidServer(S: String): Boolean;
var
  Host, Port: String;
  N: Integer;
begin
  Result := True;
  if Trim(S) = '' then Exit;
  SplitServer(S, Host, Port);
  N := StrToIntDef(Port, -1);
  Result := (Host <> '') and (Pos(' ', Host) = 0) and (Pos('"', Host) = 0) and (Pos('\', Host) = 0) and
            (N > 0) and (N < 65536) and (IntToStr(N) = Port);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (ServerPage <> nil) and (CurPageID = ServerPage.ID) and not ValidServer(ServerPage.Values[0]) then
  begin
    MsgBox('That does not look like a server address.' + #13#10 +
           'Use an IP address or computer name, optionally followed by :port  (e.g. 192.168.1.10:5150).',
           mbError, MB_OK);
    Result := False;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  S, Host, Port: String;
begin
  if CurStep <> ssPostInstall then Exit;
  S := Trim(ServerPage.Values[0]);
  if (S <> '') and ValidServer(S) then
  begin
    SplitServer(S, Host, Port);
    { preset for every user of this PC; users can still change the server on the sign-in screen }
    SaveStringToFile(ExpandConstant('{app}\client_config.json'),
      '{' + #13#10 +
      '  "server_host": "' + Host + '",' + #13#10 +
      '  "server_port": ' + Port + #13#10 +
      '}' + #13#10, False);
    RegWriteStringValue(HKA, '{#RegKey}', 'ServerAddress', S);
  end else
  begin
    { empty = automatic discovery: remove a preset left by an earlier install }
    DeleteFile(ExpandConstant('{app}\client_config.json'));
    RegDeleteValue(HKA, '{#RegKey}', 'ServerAddress');
  end;
end;
