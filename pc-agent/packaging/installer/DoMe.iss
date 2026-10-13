; DoMe per-user installer (Inno Setup 6.7). Keep this file ASCII.
;
; Built by pc-agent/packaging/installer/build_installer.py, which runs:
;   ISCC /DAppVersion=X.Y.Z /DBuildNumber=N /DBuildTag=X.Y.Z+<commit> /DAppFiles=<pc-agent\dist\DoMe>
;        /DOutputDir=<pc-agent\dist\installer> [/DDevParams] DoMe.iss
; (AppVersion comes from pc-agent/pyproject.toml.) /DDevParams makes a development build that accepts
; /ApiUrl= and /ExtensionId= on its command line; a release build (build_installer.py --release) accepts
; neither. With a signing certificate build_installer.py also passes /DSign and /Sdomesign=... so Inno
; signs DoMe.exe, dome-native-host.exe, Setup and the uninstaller (not wired into CI yet).
;
; What it does, all per user and without elevation (HKCU and %LOCALAPPDATA% only):
;   * installs the PyInstaller folder (DoMe.exe, dome-native-host.exe, _internal\) into
;     %LOCALAPPDATA%\Programs\DoMe and a Start menu shortcut;
;   * writes the DoMe server address into {app}\dome-server.json (https://domeremote.com, or the address
;     of the previous installation, or a development build's /ApiUrl=https://...). Only `DoMe.exe link`
;     reads it, as its last fallback; no environment variable is set, because one would override the
;     addresses stored when the PC was linked (pc-agent/dome_agent/settings.py);
;   * registers the browser bridge with DoMe.exe's own `install-native-host` (needs an extension id: a
;     production id baked into the build, a development build's /ExtensionId=<32 a-p>, or the one already
;     registered on this PC); without one it skips that part and says so;
;   * "Start DoMe when I sign in" (opt-in, unticked) runs DoMe.exe's own `start-at-login on`;
;   * on upgrade and uninstall it first runs `uninstall-native-host` and `quit` (the tray's Quit DoMe) so
;     no file is in use. It never terminates DoMe: if DoMe does not quit, Setup stops before changing
;     anything and asks the user to quit it from its icon. Every DoMe.exe it runs has 60 seconds; only
;     that DoMe.exe (the command Setup itself started) is stopped after that.
; Uninstall removes the program, the shortcut, dome-server.json, the native-host registration and start
; at login; it keeps %LOCALAPPDATA%\DoMe (link, paired phones, logs) unless the user answers Yes to the
; question or passes /REMOVEDATA to a silent uninstall.
;
; This script uses 32-bit Win32 record layouts in [Code] (Setup and the uninstaller of Inno Setup 6 are
; 32-bit programs). Do not build it with an Inno Setup 7 SetupArchitecture=x64 setting.

#ifndef AppVersion
  #error Pass the version: ISCC /DAppVersion=X.Y.Z (pc-agent/pyproject.toml)
#endif
#ifndef BuildNumber
  #define BuildNumber "0"
#endif
#ifndef BuildTag
  #define BuildTag AppVersion
#endif
#ifndef AppFiles
  #define AppFiles "..\..\dist\DoMe"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\dist\installer"
#endif
#ifndef BrandIcon
  #define BrandIcon "..\..\..\brand\exports\windows\dome.ico"
#endif
; How long one DoMe.exe command may take before Setup stops that command (`quit` itself waits up to 30 s).
#ifndef CommandTimeoutMs
  #define CommandTimeoutMs "60000"
#endif

[Setup]
; Never change AppId: it ties upgrades and the uninstaller together. [Code] UninstallKey repeats it.
AppId={{FD238776-147F-4CCF-B2CA-4AC5F30A07FF}
AppName=DoMe
AppVersion={#AppVersion}
AppVerName=DoMe {#AppVersion}
AppPublisher=DoMe
AppPublisherURL=https://domeremote.com/
AppSupportURL=https://domeremote.com/
VersionInfoVersion={#AppVersion}.{#BuildNumber}
VersionInfoTextVersion={#BuildTag}
VersionInfoProductName=DoMe
VersionInfoProductVersion={#AppVersion}.{#BuildNumber}
VersionInfoProductTextVersion={#BuildTag}
VersionInfoDescription=DoMe Setup
VersionInfoCompany=DoMe
DefaultDirName={autopf}\DoMe
DisableDirPage=yes
DefaultGroupName=DoMe
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
WizardStyle=modern
SetupIconFile={#BrandIcon}
UninstallDisplayIcon={app}\DoMe.exe
UninstallDisplayName=DoMe
OutputDir={#OutputDir}
OutputBaseFilename=DoMe-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
CloseApplications=yes
CloseApplicationsFilter=*.exe,*.dll,*.pyd
RestartApplications=no
SetupLogging=yes
UsePreviousTasks=no
#ifdef Sign
SignTool=domesign
SignedUninstaller=yes
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
FinishedLabel=Setup has finished installing DoMe on this PC.%n%nDoMe runs in the notification area, next to the clock.%n%nThis test version of DoMe cannot connect to your DoMe account by itself yet. The DoMe team will tell you when linking is ready.
UninstalledAll=DoMe was removed from this PC.%n%nIf you linked this PC to your DoMe account, remove it in the DoMe app on your phone (Devices). If you added the DoMe add-on to Chrome or Edge, remove it there too.

[CustomMessages]
english.OptionsGroup=Options:
english.StartAtLogin=Start DoMe when I sign in to Windows
english.LaunchDoMe=Start DoMe now
english.BrowserPageCaption=YouTube control
english.BrowserPageDescription=One part of DoMe is not set up yet
english.BrowserPageText=Controlling YouTube in Chrome or Edge needs the DoMe browser add-on. The add-on is not in the Chrome or Edge store yet, so Setup could not connect it.%n%nThe rest of DoMe was installed. A later version of DoMe will set this part up.
english.RemoveDataQuestion=Do you also want to remove DoMe's settings from this PC?%n%nYes removes this PC's link to your DoMe account, the phones you allowed, the apps you approved and DoMe's logs.%n%nNo keeps them, so DoMe works again straight away if you install it again.
english.NewerInstalled=A newer version of DoMe (%1) is already installed, so this older version (%2) was not installed.%n%nTo go back to an older version, uninstall DoMe first (Settings > Apps), then run this Setup again.
english.BadApiUrl=Setup was started with an /ApiUrl= value that is not a plain https:// address (like https://domeremote.com), so nothing was installed.
english.BadExtensionId=Setup was started with an /ExtensionId= value that is not a browser add-on id (32 letters from a to p), so nothing was installed.
english.ElevatedQuestion=Setup is running as an administrator.%n%nDoMe will be installed only for the Windows account "%1". If that is not the account you use every day, click No, then run Setup again normally: double-click it, without "Run as administrator".%n%nInstall DoMe for "%1"?
english.StillRunning=DoMe is still running and did not close, so Setup changed nothing.%n%nRight-click the DoMe icon next to the clock, choose Quit DoMe, then run Setup again.
english.StillRunningUninstall=DoMe is still running and did not close.%n%nRight-click the DoMe icon next to the clock, choose Quit DoMe, then click OK.%n%nClick Cancel to stop uninstalling.

[Tasks]
Name: "startatlogin"; Description: "{cm:StartAtLogin}"; GroupDescription: "{cm:OptionsGroup}"; Flags: unchecked

[InstallDelete]
; A new version brings a complete PyInstaller runtime; never mix it with the old one.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
; signonce is ignored unless SignTool is set (/DSign).
Source: "{#AppFiles}\DoMe.exe"; DestDir: "{app}"; Flags: ignoreversion signonce
Source: "{#AppFiles}\dome-native-host.exe"; DestDir: "{app}"; Flags: ignoreversion signonce
Source: "{#AppFiles}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\DoMe"; Filename: "{app}\DoMe.exe"; WorkingDir: "{app}"; Comment: "Control this PC from your phone"

[UninstallDelete]
; [Code] writes dome-server.json; _internal may also hold files the program created there at run time.
Type: files; Name: "{app}\dome-server.json"
Type: filesandordirs; Name: "{app}\_internal"
Type: dirifempty; Name: "{app}"

[Run]
; Never started from an elevated Setup: DoMe must run with the user's normal rights (spec section 10).
Filename: "{app}\DoMe.exe"; Description: "{cm:LaunchDoMe}"; Flags: postinstall nowait skipifsilent; Check: not IsAdmin

[Code]
const
  RunKey = 'Software\Microsoft\Windows\CurrentVersion\Run';
  StartupApprovedRunKey = 'Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run';
  ChromeHostKey = 'Software\Google\Chrome\NativeMessagingHosts\com.dome.agent';
  EdgeHostKey = 'Software\Microsoft\Edge\NativeMessagingHosts\com.dome.agent';
  { AppId + '_is1'; must match [Setup] AppId. }
  UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{FD238776-147F-4CCF-B2CA-4AC5F30A07FF}_is1';
  ThisVersion = '{#AppVersion}';
  DefaultApiUrl = 'https://domeremote.com';
  { pc-agent/dome_agent/single_instance.py: one agent per Windows logon session. }
  AgentMutexPrefix = 'Local\DoMe.Agent.';
  CommandTimeout = {#CommandTimeoutMs};
  WaitTimeoutCode = 258;
  StillActiveCode = 259;
  QsAllInput = $04FF;
  PmRemove = 1;
  WmQuit = $0012;
  StartfUseStdHandles = $00000100;
  GenericWrite = $40000000;
  FileShareRead = 1;
  CreateAlways = 2;
  FileAttributeNormal = $80;

type
  { Win32 structures, 32-bit layout (see the note at the top of this file). }
  TDmSecurityAttributes = record
    nLength: Integer;
    lpSecurityDescriptor: Integer;
    bInheritHandle: Integer;
  end;
  TDmStartupInfo = record
    cb: Integer;
    lpReserved: Integer;
    lpDesktop: Integer;
    lpTitle: Integer;
    dwX: Integer;
    dwY: Integer;
    dwXSize: Integer;
    dwYSize: Integer;
    dwXCountChars: Integer;
    dwYCountChars: Integer;
    dwFillAttribute: Integer;
    dwFlags: Integer;
    wShowWindow: Word;
    cbReserved2: Word;
    lpReserved2: Integer;
    hStdInput: Integer;
    hStdOutput: Integer;
    hStdError: Integer;
  end;
  TDmProcessInformation = record
    hProcess: Integer;
    hThread: Integer;
    dwProcessId: Integer;
    dwThreadId: Integer;
  end;
  TDmMsg = record
    hWindow: Integer;
    msgId: Integer;
    wParam: Integer;
    lParam: Integer;
    msgTime: Integer;
    ptX: Integer;
    ptY: Integer;
  end;

var
  ApiUrl: String;
  ExtensionId: String;
  NativeHostRegistered: Boolean;
  HostUnregistered: Boolean;
  InstallDone: Boolean;
  RemoveData: Boolean;
  BrowserPage: TOutputMsgWizardPage;

function DmSetEnvironmentVariable(lpName, lpValue: String): Integer;
  external 'SetEnvironmentVariableW@kernel32.dll stdcall';
{ The same function with a NULL value, which deletes the variable. }
function DmDeleteEnvironmentVariable(lpName: String; lpValueNull: Integer): Integer;
  external 'SetEnvironmentVariableW@kernel32.dll stdcall';
function DmCreateFile(lpFileName: String; dwDesiredAccess, dwShareMode: Integer;
  var lpSecurityAttributes: TDmSecurityAttributes; dwCreationDisposition, dwFlagsAndAttributes,
  hTemplateFile: Integer): Integer;
  external 'CreateFileW@kernel32.dll stdcall';
function DmCreateProcess(lpApplicationName, lpCommandLine: String; lpProcessAttributes,
  lpThreadAttributes, bInheritHandles, dwCreationFlags, lpEnvironment: Integer; lpCurrentDirectory: String;
  var lpStartupInfo: TDmStartupInfo; var lpProcessInformation: TDmProcessInformation): Integer;
  external 'CreateProcessW@kernel32.dll stdcall';
function DmMsgWaitForMultipleObjects(nCount: Integer; var pHandles: Integer; bWaitAll, dwMilliseconds,
  dwWakeMask: Integer): Integer;
  external 'MsgWaitForMultipleObjects@user32.dll stdcall';
function DmWaitForSingleObject(hHandle, dwMilliseconds: Integer): Integer;
  external 'WaitForSingleObject@kernel32.dll stdcall';
function DmGetExitCodeProcess(hProcess: Integer; var lpExitCode: Integer): Integer;
  external 'GetExitCodeProcess@kernel32.dll stdcall';
function DmTerminateProcess(hProcess, uExitCode: Integer): Integer;
  external 'TerminateProcess@kernel32.dll stdcall';
function DmCloseHandle(hObject: Integer): Integer;
  external 'CloseHandle@kernel32.dll stdcall';
function DmGetTickCount: Integer;
  external 'GetTickCount@kernel32.dll stdcall';
function DmGetCurrentProcessId: Integer;
  external 'GetCurrentProcessId@kernel32.dll stdcall';
function DmProcessIdToSessionId(dwProcessId: Integer; var pSessionId: Integer): Integer;
  external 'ProcessIdToSessionId@kernel32.dll stdcall';
function DmPeekMessage(var lpMsg: TDmMsg; hWnd, wMsgFilterMin, wMsgFilterMax, wRemoveMsg: Integer): Integer;
  external 'PeekMessageW@user32.dll stdcall';
function DmTranslateMessage(var lpMsg: TDmMsg): Integer;
  external 'TranslateMessage@user32.dll stdcall';
function DmDispatchMessage(var lpMsg: TDmMsg): Integer;
  external 'DispatchMessageW@user32.dll stdcall';
procedure DmPostQuitMessage(nExitCode: Integer);
  external 'PostQuitMessage@user32.dll stdcall';

function HasParam(const Name: String): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), Name) = 0 then begin
      Result := True;
      Exit;
    end;
end;

{ Trimmed, without trailing slashes. }
function NormaliseUrl(const S: String): String;
begin
  Result := Trim(S);
  while (Length(Result) > 0) and (Result[Length(Result)] = '/') do
    Delete(Result, Length(Result), 1);
end;

{ A plain https origin, https://host or https://host:port and nothing else: the same rule as
  installed_api_url() in pc-agent/dome_agent/settings.py, which ignores anything else. }
function IsHttpsOrigin(const S: String): Boolean;
var
  I, Colon: Integer;
  C: Char;
begin
  Result := (Length(S) > 8) and (Length(S) <= 300) and (Copy(S, 1, 8) = 'https://');
  if not Result then
    Exit;
  Colon := 0;
  for I := 9 to Length(S) do begin
    C := S[I];
    if C = ':' then begin
      if Colon <> 0 then begin
        Result := False;
        Exit;
      end;
      Colon := I;
    end else if (Colon <> 0) and not ((C >= '0') and (C <= '9')) then begin
      Result := False;
      Exit;
    end else if not (((C >= 'a') and (C <= 'z')) or ((C >= 'A') and (C <= 'Z')) or ((C >= '0') and (C <= '9'))
                     or (C = '.') or (C = '-')) then begin
      Result := False;
      Exit;
    end;
  end;
  { a host before the colon, and 1 to 5 digits after it }
  if Colon <> 0 then
    Result := (Colon > 9) and (Length(S) > Colon) and (Length(S) - Colon <= 5);
end;

function IsExtensionId(const S: String): Boolean;
var
  I: Integer;
begin
  Result := Length(S) = 32;
  if Result then
    for I := 1 to Length(S) do
      if (S[I] < 'a') or (S[I] > 'p') then begin
        Result := False;
        Exit;
      end;
end;

{ X.Y.Z (what pyproject.toml holds) as a packed version. }
function VersionValue(const S: String; var V: Int64): Boolean;
begin
  Result := StrToVersion(S + '.0', V);
end;

{ DoMe.exe must see nothing from the shell Setup was started from: never a fake platform, another
  state folder, a server address (that would override the PC's link) or a development extension. }
procedure CleanEnvironment;
begin
  DmDeleteEnvironmentVariable('DOME_AGENT_API_URL', 0);
  DmDeleteEnvironmentVariable('DOME_AGENT_RELAY_URL', 0);
  DmDeleteEnvironmentVariable('DOME_AGENT_PLATFORM', 0);
  DmDeleteEnvironmentVariable('DOME_AGENT_STATE_DIR', 0);
  DmDeleteEnvironmentVariable('DOME_AGENT_HEADLESS', 0);
  DmDeleteEnvironmentVariable('DOME_AGENT_LOG_LEVEL', 0);
  DmDeleteEnvironmentVariable('DOME_AGENT_DEV_EXTENSION_ID', 0);
end;

{ Keep Setup's window responsive while it waits for DoMe.exe. }
procedure PumpMessages;
var
  M: TDmMsg;
begin
  while DmPeekMessage(M, 0, 0, 0, PmRemove) <> 0 do begin
    if M.msgId = WmQuit then begin
      DmPostQuitMessage(M.wParam);
      Exit;
    end;
    DmTranslateMessage(M);
    DmDispatchMessage(M);
  end;
end;

{ Is a DoMe agent running in this Windows session? (Its single-instance mutex exists.) }
function AgentRunning: Boolean;
var
  Session: Integer;
  Name: String;
begin
  if DmProcessIdToSessionId(DmGetCurrentProcessId, Session) <> 0 then
    Name := AgentMutexPrefix + IntToStr(Session)
  else
    Name := AgentMutexPrefix + 'unknown';
  Result := CheckForMutexes(Name);
  Log('DoMe running in this session (' + Name + '): ' + IntToStr(Ord(Result)));
end;

{ Runs the installed DoMe.exe with its output in Setup's or the uninstaller's log. Returns its exit code, -1 when
  it could not be started, -2 when it did not finish within CommandTimeout ms (then that one DoMe.exe,
  which Setup itself started, is stopped; nothing else ever is). }
function RunDoMe(const Params: String): Integer;
var
  Exe, CmdLine, WorkDir, OutFile: String;
  Sa: TDmSecurityAttributes;
  Si: TDmStartupInfo;
  ProcInfo: TDmProcessInformation;
  OutHandle, Process, Wait, Started, Code, I: Integer;
  Lines: TArrayOfString;
begin
  Result := -1;
  Exe := ExpandConstant('{app}\DoMe.exe');
  if not FileExists(Exe) then begin
    Log('DoMe.exe not found at ' + Exe + '; skipped: ' + Params);
    Exit;
  end;
  WorkDir := ExpandConstant('{app}');
  OutFile := GetTempDir + 'dome-setup-' + IntToStr(DmGetCurrentProcessId) + '.txt';
  Sa.nLength := 12;
  Sa.lpSecurityDescriptor := 0;
  Sa.bInheritHandle := 1;
  OutHandle := DmCreateFile(OutFile, GenericWrite, FileShareRead, Sa, CreateAlways, FileAttributeNormal, 0);
  if (OutHandle = 0) or (OutHandle = -1) then begin
    Log('Could not create ' + OutFile + '; the output of DoMe.exe is not logged.');
    OutHandle := 0;
  end;
  Si.cb := 68;
  Si.lpReserved := 0;
  Si.lpDesktop := 0;
  Si.lpTitle := 0;
  Si.dwX := 0;
  Si.dwY := 0;
  Si.dwXSize := 0;
  Si.dwYSize := 0;
  Si.dwXCountChars := 0;
  Si.dwYCountChars := 0;
  Si.dwFillAttribute := 0;
  Si.dwFlags := 0;
  Si.wShowWindow := 0;
  Si.cbReserved2 := 0;
  Si.lpReserved2 := 0;
  Si.hStdInput := 0;
  Si.hStdOutput := 0;
  Si.hStdError := 0;
  if OutHandle <> 0 then begin
    Si.dwFlags := StartfUseStdHandles;
    Si.hStdOutput := OutHandle;
    Si.hStdError := OutHandle;
  end;
  CmdLine := '"' + Exe + '" ' + Params;
  Log('Running DoMe.exe ' + Params);
  if DmCreateProcess(Exe, CmdLine, 0, 0, 1, 0, 0, WorkDir, Si, ProcInfo) = 0 then begin
    Log('DoMe.exe could not be started: ' + SysErrorMessage(DLLGetLastError));
    if OutHandle <> 0 then
      DmCloseHandle(OutHandle);
    Exit;
  end;
  if OutHandle <> 0 then
    DmCloseHandle(OutHandle);  { DoMe.exe has its own copy }
  DmCloseHandle(ProcInfo.hThread);
  Process := ProcInfo.hProcess;
  Started := DmGetTickCount;
  repeat
    PumpMessages;
    Wait := DmMsgWaitForMultipleObjects(1, Process, 0, 100, QsAllInput);
  until ((Wait <> WaitTimeoutCode) and (Wait <> 1)) or (DmGetTickCount - Started >= CommandTimeout);
  Code := StillActiveCode;
  if DmGetExitCodeProcess(Process, Code) = 0 then
    Code := -1;
  if Code = StillActiveCode then begin
    Log('DoMe.exe ' + Params + ' did not finish within ' + IntToStr(CommandTimeout div 1000) +
      ' seconds; Setup stopped that command (only the DoMe.exe it had started itself).');
    DmTerminateProcess(Process, 1);
    DmWaitForSingleObject(Process, 5000);
    Result := -2;
  end else
    Result := Code;
  DmCloseHandle(Process);
  if LoadStringsFromFile(OutFile, Lines) then
    for I := 0 to GetArrayLength(Lines) - 1 do
      if Trim(Lines[I]) <> '' then
        Log('  DoMe.exe: ' + Lines[I]);
  DeleteFile(OutFile);
  Log('DoMe.exe ' + Params + ' exited with code ' + IntToStr(Result));
end;

function InitializeSetup(): Boolean;
var
  Installed, Previous, Given: String;
  InstalledValue, ThisValue: Int64;
begin
  Result := True;
  Log('DoMe Setup {#BuildTag}');
  { The server address: the default, or the one the previous installation used ... }
  ApiUrl := DefaultApiUrl;
  Previous := NormaliseUrl(GetPreviousData('ApiUrl', ''));
  if Previous <> '' then begin
    if IsHttpsOrigin(Previous) then
      ApiUrl := Previous
    else
      Log('Ignored the server address of the previous installation: ' + Previous);
  end;
  Given := NormaliseUrl(ExpandConstant('{param:ApiUrl|}'));
  ExtensionId := Trim(ExpandConstant('{param:ExtensionId|}'));
#ifdef DevParams
  { ... or, in a development build only, /ApiUrl= (a staging server) and /ExtensionId= (a development add-on). }
  if Given <> '' then begin
    if not IsHttpsOrigin(Given) then begin
      Log('Refused /ApiUrl=' + Given + ': not a plain https:// address.');
      SuppressibleMsgBox(CustomMessage('BadApiUrl'), mbCriticalError, MB_OK, IDOK);
      Result := False;
      Exit;
    end;
    ApiUrl := Given;
  end;
  if (ExtensionId <> '') and not IsExtensionId(ExtensionId) then begin
    Log('Refused /ExtensionId=' + ExtensionId + ': not 32 letters a-p.');
    SuppressibleMsgBox(CustomMessage('BadExtensionId'), mbCriticalError, MB_OK, IDOK);
    Result := False;
    Exit;
  end;
#else
  if Given <> '' then
    Log('Ignored /ApiUrl=: this Setup is a release build and does not accept it.');
  if ExtensionId <> '' then
    Log('Ignored /ExtensionId=: this Setup is a release build and does not accept it.');
  ExtensionId := '';
#endif
  if RegQueryStringValue(HKCU, UninstallKey, 'DisplayVersion', Installed) then begin
    if VersionValue(Installed, InstalledValue) and VersionValue(ThisVersion, ThisValue) then begin
      if ComparePackedVersion(InstalledValue, ThisValue) > 0 then begin
        Log('Refusing to downgrade: DoMe ' + Installed + ' is installed, this Setup is ' + ThisVersion + '.');
        SuppressibleMsgBox(FmtMessage(CustomMessage('NewerInstalled'), [Installed, ThisVersion]), mbCriticalError, MB_OK, IDOK);
        Result := False;
        Exit;
      end;
    end else
      Log('Installed version "' + Installed + '" could not be compared; continuing.');
  end;
  if IsAdmin then begin
    { "Run as administrator": everything DoMe keeps belongs to the account Setup runs as. }
    Log('Setup runs with administrator rights as ' + GetUserNameString + '.');
    if SuppressibleMsgBox(FmtMessage(CustomMessage('ElevatedQuestion'), [GetUserNameString]), mbConfirmation, MB_YESNO, IDYES) <> IDYES then begin
      Log('Stopped: the user chose not to install DoMe for this administrator account.');
      Result := False;
      Exit;
    end;
  end;
  CleanEnvironment;
  { Only for the DoMe.exe commands Setup runs (install-native-host); never stored anywhere. }
  if ExtensionId <> '' then
    DmSetEnvironmentVariable('DOME_AGENT_DEV_EXTENSION_ID', ExtensionId);
  Log('DoMe server address: ' + ApiUrl);
end;

procedure RegisterPreviousData(PreviousDataKey: Integer);
begin
  SetPreviousData(PreviousDataKey, 'ApiUrl', ApiUrl);
end;

procedure InitializeWizard();
begin
  BrowserPage := CreateOutputMsgPage(wpInstalling, CustomMessage('BrowserPageCaption'),
    CustomMessage('BrowserPageDescription'), CustomMessage('BrowserPageText'));
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (BrowserPage <> nil) and (PageID = BrowserPage.ID) and NativeHostRegistered;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  Result := '';
  if not FileExists(ExpandConstant('{app}\DoMe.exe')) then
    Exit;
  { Upgrade or repair: stop the browsers from starting the old dome-native-host.exe, then ask the running
    DoMe of this Windows session to quit and wait for it, so no file in the folder is in use. }
  HostUnregistered := RunDoMe('uninstall-native-host') = 0;
  Code := RunDoMe('quit --timeout 20');
  if Code <> 0 then begin
    if AgentRunning then begin
      Log('DoMe is still running in this session (quit exit code ' + IntToStr(Code) + '); nothing was changed.');
      Result := CustomMessage('StillRunning');
      Exit;
    end;
    Log('DoMe.exe quit did not succeed (exit code ' + IntToStr(Code) + '), but no DoMe runs in this session; continuing.');
  end;
  Sleep(1000);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ServerFile: String;
begin
  if CurStep = ssPostInstall then begin
    InstallDone := True;
    ServerFile := ExpandConstant('{app}\dome-server.json');
    if SaveStringToFile(ServerFile, '{"api_url": "' + ApiUrl + '"}' + #13#10, False) then
      Log('Wrote ' + ServerFile + ' (' + ApiUrl + ')')
    else
      Log('Could not write ' + ServerFile);
    NativeHostRegistered := RunDoMe('install-native-host') = 0;
    if not NativeHostRegistered then
      Log('Browser bridge not registered (no extension id available, or install-native-host failed).');
    if WizardIsTaskSelected('startatlogin') then
      if RunDoMe('start-at-login on') <> 0 then
        Log('Start at login could not be turned on.');
  end;
end;

procedure DeinitializeSetup();
begin
  { Setup unregistered the browser bridge of the installed DoMe but did not finish: register it again. }
  if HostUnregistered and not InstallDone then begin
    Log('Setup did not finish: registering the browser bridge of the installed DoMe again.');
    RunDoMe('install-native-host');
  end;
end;

function InitializeUninstall(): Boolean;
begin
  CleanEnvironment;
  Result := True;
end;

{ After the user confirmed: stop the browsers from starting dome-native-host.exe and quit DoMe. If DoMe
  does not quit, ask the user to quit it; Cancel stops the uninstall with nothing removed. }
procedure StopDoMeForUninstall;
var
  Code: Integer;
begin
  HostUnregistered := RunDoMe('uninstall-native-host') = 0;
  Code := RunDoMe('quit --timeout 20');
  while (Code <> 0) and AgentRunning do begin
    if SuppressibleMsgBox(CustomMessage('StillRunningUninstall'), mbError, MB_OKCANCEL, IDCANCEL) <> IDOK then begin
      Log('Uninstall stopped: DoMe is still running.');
      if HostUnregistered then
        RunDoMe('install-native-host');
      Abort;
    end;
    Code := RunDoMe('quit --timeout 20');
  end;
end;

procedure RemoveRegistration;
begin
  if HasParam('/REMOVEDATA') then
    RemoveData := True
  else if UninstallSilent() then
    RemoveData := False
  else
    RemoveData := MsgBox(CustomMessage('RemoveDataQuestion'), mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
  if RemoveData then
    Log('Settings, link and logs in %LOCALAPPDATA%\DoMe will be removed.')
  else
    Log('Settings, link and logs in %LOCALAPPDATA%\DoMe are kept.');
  { The browsers must not start dome-native-host.exe again (the registration is not the user's data). }
  if not HostUnregistered then begin
    RegDeleteKeyIncludingSubkeys(HKCU, ChromeHostKey);
    RegDeleteKeyIncludingSubkeys(HKCU, EdgeHostKey);
  end;
  DeleteFile(ExpandConstant('{localappdata}\DoMe\com.dome.agent.json'));
  { Start at login: DoMe's own Run entry, and Windows' record of it from Task Manager. }
  if RunDoMe('start-at-login off') <> 0 then
    RegDeleteValue(HKCU, RunKey, 'DoMe');
  RegDeleteValue(HKCU, StartupApprovedRunKey, 'DoMe');
end;

procedure RemoveLocalData;
var
  DataDir: String;
begin
  DataDir := ExpandConstant('{localappdata}\DoMe');
  if RemoveData then begin
    if DirExists(DataDir) and not DelTree(DataDir, True, True, True) then begin
      Sleep(2000);
      DelTree(DataDir, True, True, True);
    end;
    if DirExists(DataDir) then
      Log('Could not remove everything in ' + DataDir)
    else
      Log('Removed ' + DataDir);
  end else begin
    { Kept, but leave no empty folders behind (for someone who never started DoMe). }
    RemoveDir(DataDir + '\logs');
    RemoveDir(DataDir + '\secrets');
    RemoveDir(DataDir + '\diagnostics');
    RemoveDir(DataDir);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usAppMutexCheck then
    StopDoMeForUninstall
  else if CurUninstallStep = usUninstall then
    RemoveRegistration
  else if CurUninstallStep = usPostUninstall then
    RemoveLocalData;
end;
