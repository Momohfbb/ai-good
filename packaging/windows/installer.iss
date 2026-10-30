; Inno Setup script: builds UltraAI-Setup-<version>.exe from the PyInstaller folder.
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{8C2F2B7E-5D0C-4E5E-9C5A-3F1D2B7A9E10}
AppName=ULTRA AI GOD MODE
AppVersion={#AppVersion}
AppPublisher=Momo
DefaultDirName={localappdata}\Programs\UltraAI
DefaultGroupName=ULTRA AI
PrivilegesRequired=lowest
OutputDir=..\..\dist
OutputBaseFilename=UltraAI-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\UltraAI.exe
ShowLanguageDialog=auto
; The brain may be running in the background (autostart has no window), so Windows' Restart Manager
; cannot close it and setup stalls. We stop it ourselves in PrepareToInstall instead.
CloseApplications=no

[Languages]
#if FileExists(AddBackslash(CompilerPath) + "Languages\Arabic.isl")
Name: "arabic"; MessagesFile: "compiler:Languages\Arabic.isl"
#endif
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "autostart"; Description: "تشغيل العقل تلقائياً في الخلفية عند تسجيل الدخول / Start the brain in the background at sign-in"; GroupDescription: "ULTRA AI:"

[Files]
Source: "..\..\dist\UltraAI\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\ULTRA AI GOD MODE"; Filename: "{app}\UltraAI.exe"
Name: "{group}\ULTRA AI (المتصفح / browser)"; Filename: "{app}\UltraAI.exe"; Parameters: "--browser"
Name: "{group}\{cm:UninstallProgram,ULTRA AI}"; Filename: "{uninstallexe}"

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "UltraAI"; ValueData: """{app}\UltraAI.exe"" --no-browser"; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\UltraAI.exe"; Description: "{cm:LaunchProgram,ULTRA AI GOD MODE}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: files; Name: "{autodesktop}\ULTRA AI GOD MODE.lnk"

[Code]
procedure StopRunningApp();
var
  Code: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM UltraAI.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Sleep(1500);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopRunningApp();
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  StopRunningApp();
  Result := True;
end;

// The desktop shortcut is made here rather than in [Icons]: when the Desktop folder is redirected
// (OneDrive, a moved or missing folder) Windows refuses it with "IPersistFile::Save failed; code
// 0x80070003" and setup stops. A missing desktop shortcut must never block the install.
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and WizardIsTaskSelected('desktopicon') then
  begin
    try
      CreateShellLink(ExpandConstant('{autodesktop}\ULTRA AI GOD MODE.lnk'), 'ULTRA AI GOD MODE',
        ExpandConstant('{app}\UltraAI.exe'), '', ExpandConstant('{app}'), '', 0, SW_SHOWNORMAL);
    except
      Log('Desktop shortcut skipped: ' + GetExceptionMessage);
    end;
  end;
end;
