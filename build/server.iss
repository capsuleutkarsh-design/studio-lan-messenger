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
#define DefData   "{commonappdata}\LAN Messenger Server"
#define RegKey    "Software\LAN Messenger Server"
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
; never removed automatically. Access rights are set in [Run]: only Administrators + SYSTEM in service
; mode (the folder holds every chat); in tray mode also the signed-in users, who run the server
Name: "{code:GetDataDir}"; Flags: uninsneveruninstall

[Files]
Source: "{#DistDir}\LANMessengerServer\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "service.ps1"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\LAN Messenger Server console"; Filename: "{app}\{#ExeName}"
Name: "{group}\Connect to another server...";  Filename: "{app}\{#ExeName}"; Parameters: "--console"
Name: "{group}\Server data folder";         Filename: "{code:GetDataDir}"
Name: "{group}\Uninstall LAN Messenger Server"; Filename: "{uninstallexe}"
Name: "{autodesktop}\LAN Messenger Server console"; Filename: "{app}\{#ExeName}"; Tasks: desktopicon

[Registry]
; where the data lives (the server reads DataDir; the others pre-fill this page next time)
Root: HKLM; Subkey: "{#RegKey}"; ValueType: string; ValueName: "DataDir";    ValueData: "{code:GetDataDir}"; Flags: uninsdeletekey
Root: HKLM; Subkey: "{#RegKey}"; ValueType: string; ValueName: "StorageDir"; ValueData: "{code:GetStorageDir}"; Check: not IsUpgrade
Root: HKLM; Subkey: "{#RegKey}"; ValueType: string; ValueName: "BackupDir";  ValueData: "{code:GetBackupDir}"; Check: not IsUpgrade
Root: HKLM; Subkey: "{#RegKey}"; ValueType: string; ValueName: "LogDir";     ValueData: "{code:GetLogDir}"; Check: not IsUpgrade
Root: HKLM; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "LANMessengerServer"; \
  ValueData: """{app}\{#ExeName}"" --minimized"; Tasks: autostart; Flags: uninsdeletevalue

[Run]
; save the folders chosen on the "Where to keep the data" page into the server's settings (first install)
Filename: "{app}\{#ExeName}"; Parameters: "--data=""{code:GetDataDir}"" --configure --storage=""{code:GetStorageDir}"" --backups=""{code:GetBackupDir}"" --logs=""{code:GetLogDir}"""; \
  Flags: runhidden waituntilterminated; Check: not IsUpgrade; StatusMsg: "Saving the data folders..."
; lock the data down: the database holds every chat (service: Administrators + SYSTEM only)
Filename: "{sys}\icacls.exe"; Parameters: """{code:GetDataDir}"" /inheritance:r /grant:r *S-1-5-18:(OI)(CI)F *S-1-5-32-544:(OI)(CI)F /T /C /Q"; \
  Flags: runhidden waituntilterminated; Tasks: service; StatusMsg: "Protecting the data folder..."
Filename: "{sys}\icacls.exe"; Parameters: """{code:GetDataDir}"" /inheritance:r /grant:r *S-1-5-18:(OI)(CI)F *S-1-5-32-544:(OI)(CI)F *S-1-5-32-545:(OI)(CI)M /T /C /Q"; \
  Flags: runhidden waituntilterminated; Tasks: autostart; StatusMsg: "Protecting the data folder..."
Filename: "{sys}\icacls.exe"; Parameters: """{code:GetBackupDir}"" /inheritance:r /grant:r *S-1-5-18:(OI)(CI)F *S-1-5-32-544:(OI)(CI)F /T /C /Q"; \
  Flags: runhidden waituntilterminated; Tasks: service; Check: BackupIsLocalOutside
Filename: "{sys}\icacls.exe"; Parameters: """{code:GetStorageDir}"" /inheritance:r /grant:r *S-1-5-18:(OI)(CI)F *S-1-5-32-544:(OI)(CI)F /T /C /Q"; \
  Flags: runhidden waituntilterminated; Tasks: service; Check: StorageIsLocalOutside
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
var
  PathsPage: TInputDirWizardPage;
  DataDirValue, UninstallData: String;
  Upgrading: Boolean;
  OldDefaults: array[0..2] of String;

function GetDriveTypeW(lpRootPathName: String): Cardinal;
  external 'GetDriveTypeW@kernel32.dll stdcall';

function RegValue(Name, Default: String): String;
begin
  if not RegQueryStringValue(HKLM, '{#RegKey}', Name, Result) or (Trim(Result) = '') then
    Result := Default;
end;

function IsUNC(P: String): Boolean;
begin
  Result := Copy(Trim(P), 1, 2) = '\\';
end;

{ 0 unknown, 1 no such drive, 2 removable, 3 fixed, 4 network (mapped letter), 5 CD, 6 RAM disk }
function DriveKind(P: String): Cardinal;
begin
  if IsUNC(P) then
    Result := 4
  else
    Result := GetDriveTypeW(Copy(Trim(P), 1, 3));
end;

function StripSlash(P: String): String;
begin
  Result := RemoveBackslashUnlessRoot(Trim(P));
end;

function GetDataDir(Param: String): String;
begin
  if (PathsPage <> nil) and not Upgrading then
    Result := StripSlash(PathsPage.Values[0])
  else
    Result := DataDirValue;
end;

function GetStorageDir(Param: String): String;
begin
  Result := StripSlash(PathsPage.Values[1]);
end;

function GetBackupDir(Param: String): String;
begin
  Result := StripSlash(PathsPage.Values[2]);
end;

function GetLogDir(Param: String): String;
begin
  Result := StripSlash(PathsPage.Values[3]);
end;

function IsUpgrade: Boolean;
begin
  Result := Upgrading;
end;

{ a local folder that is not inside the data folder (that one is protected already) }
function LocalOutside(P: String): Boolean;
begin
  Result := (not IsUNC(P)) and (Pos(Lowercase(AddBackslash(GetDataDir(''))), Lowercase(AddBackslash(P))) <> 1);
end;

function BackupIsLocalOutside: Boolean;
begin
  Result := LocalOutside(GetBackupDir(''));
end;

function StorageIsLocalOutside: Boolean;
begin
  Result := LocalOutside(GetStorageDir(''));
end;

function InitializeSetup: Boolean;
begin
  DataDirValue := RegValue('DataDir', ExpandConstant('{#DefData}'));
  { an earlier install already has settings: its folders are changed in the console, not here }
  Upgrading := FileExists(AddBackslash(DataDirValue) + 'config.json');
  Result := True;
end;

procedure InitializeWizard;
begin
  PathsPage := CreateInputDirPage(wpSelectTasks, 'Where to keep the data',
    'Choose where the server stores its database, shared files, backups and log.',
    'SERVER DATA holds the database (all accounts and messages) and the settings. It must be on a disk of ' +
    'THIS PC (not a network drive).' + #13#10 +
    'The other folders may be on another disk or a network share - use a \\server\share path for those, ' +
    'not a mapped drive letter (Z:), which the background service can''t see.',
    False, '');
  PathsPage.Add('Server data (database and settings) - local disk:');
  PathsPage.Add('Shared files:');
  PathsPage.Add('Database backups and readable chat backups:');
  PathsPage.Add('Server log:');
  PathsPage.Values[0] := DataDirValue;
  PathsPage.Values[1] := RegValue('StorageDir', AddBackslash(DataDirValue) + 'files');
  PathsPage.Values[2] := RegValue('BackupDir', AddBackslash(DataDirValue) + 'backups');
  PathsPage.Values[3] := RegValue('LogDir', DataDirValue);
  OldDefaults[0] := AddBackslash(DataDirValue) + 'files';
  OldDefaults[1] := AddBackslash(DataDirValue) + 'backups';
  OldDefaults[2] := DataDirValue;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (PathsPage <> nil) and (PageID = PathsPage.ID) and Upgrading;
end;

function CheckFolder(Title, P: String; LocalOnly: Boolean): Boolean;
var
  Kind: Cardinal;
begin
  Result := False;
  P := Trim(P);
  if (P = '') or ((not IsUNC(P)) and ((Length(P) < 3) or (Copy(P, 2, 2) <> ':\'))) then
  begin
    MsgBox(Title + ': enter a full folder path, e.g. D:\Messenger or \\server\share\Messenger.', mbError, MB_OK);
    Exit;
  end;
  Kind := DriveKind(P);
  if LocalOnly and (Kind = 4) then
  begin
    MsgBox(Title + ' must be on a disk of this PC. A database on a network share can get damaged.',
           mbError, MB_OK);
    Exit;
  end;
  if Kind = 1 then
  begin
    MsgBox(Title + ': drive ' + Copy(P, 1, 2) + ' does not exist on this PC.', mbError, MB_OK);
    Exit;
  end;
  if (Kind = 4) and not IsUNC(P) then
  begin
    MsgBox(Title + ': ' + Copy(P, 1, 2) + ' is a mapped network drive. The background service can''t see ' +
           'mapped drives - use the \\server\share\... path instead.', mbError, MB_OK);
    Exit;
  end;
  if not ForceDirectories(P) then
  begin
    if MsgBox(Title + ': the folder ' + P + ' can''t be created or reached right now.' + #13#10 + #13#10 +
              'Use it anyway?', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) <> IDYES then
      Exit;
  end;
  Result := True;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  I: Integer;
  NewDefaults: array[0..2] of String;
  HasShare: Boolean;
begin
  Result := True;
  if (PathsPage = nil) or (CurPageID <> PathsPage.ID) then Exit;
  { the data folder moved: sub-folders still on the old default follow it }
  NewDefaults[0] := AddBackslash(StripSlash(PathsPage.Values[0])) + 'files';
  NewDefaults[1] := AddBackslash(StripSlash(PathsPage.Values[0])) + 'backups';
  NewDefaults[2] := StripSlash(PathsPage.Values[0]);
  for I := 0 to 2 do
    if CompareText(StripSlash(PathsPage.Values[I + 1]), OldDefaults[I]) = 0 then
      PathsPage.Values[I + 1] := NewDefaults[I];
  for I := 0 to 2 do
    OldDefaults[I] := NewDefaults[I];
  Result := CheckFolder('Server data', PathsPage.Values[0], True) and
            CheckFolder('Shared files', PathsPage.Values[1], False) and
            CheckFolder('Backups', PathsPage.Values[2], False) and
            CheckFolder('Server log', PathsPage.Values[3], False);
  if not Result then Exit;
  HasShare := IsUNC(PathsPage.Values[1]) or IsUNC(PathsPage.Values[2]) or IsUNC(PathsPage.Values[3]);
  if HasShare and WizardIsTaskSelected('service') then
    MsgBox('A network share is used.' + #13#10 + #13#10 +
           'The background service runs as this PC''s SYSTEM account and reaches network shares as the ' +
           'computer account (' + GetComputerNameString + '$). Give that account "Modify" rights on the share ' +
           '(in a domain), or choose "Run in the system tray" instead so the server uses the signed-in user.' +
           ''#13#10#13#10'If the share can''t be reached, the server still starts: uploads and backups ' +
           'report the problem in the console.', mbInformation, MB_OK);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and not Upgrading then
    DataDirValue := GetDataDir('');
end;

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
begin
  if CurUninstallStep = usUninstall then           { read before the registry key is removed }
    UninstallData := RegValue('DataDir', ExpandConstant('{#DefData}'));
  if CurUninstallStep = usPostUninstall then
  begin
    if DirExists(UninstallData) and (not UninstallSilent) then
      if MsgBox('Do you also want to DELETE all server data?' + #13#10 + #13#10 +
                'This removes every account, message and the settings in:' + #13#10 + UninstallData + #13#10 +
                '(shared files and backups kept in other folders are not touched)' + #13#10 + #13#10 +
                'Choose "No" to keep the data (recommended if you reinstall or upgrade later).',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(UninstallData, True, True, True);
  end;
end;
