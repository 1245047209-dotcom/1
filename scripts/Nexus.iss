#ifndef PayloadDir
  #error PayloadDir is required
#endif
#ifndef DistDir
  #error DistDir is required
#endif

[Setup]
AppId={{B9FD643E-E364-4B04-A497-F362A96D00CC}
AppName=Nexus 外设电量中心
AppVersion={#AppVersion}
AppVerName=Nexus 外设电量中心 {#ReleaseTag}
VersionInfoVersion={#AppVersion}
AppPublisher=1245047209-dotcom
AppPublisherURL=https://github.com/1245047209-dotcom/1
AppUpdatesURL=https://github.com/1245047209-dotcom/1/releases
DefaultDirName={localappdata}\Programs\Nexus
DefaultGroupName=Nexus 外设电量中心
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes
DisableDirPage=no
OutputDir={#DistDir}
OutputBaseFilename=Nexus-{#ReleaseTag}-Windows-x64-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\assets\nexus.ico
SetupIconFile={#PayloadDir}\assets\nexus.ico
CloseApplications=no
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式"

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__\*,*.pyc,*.pyo"

[Icons]
Name: "{group}\Nexus 外设电量中心"; Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\my_devices.pyw"""; WorkingDir: "{app}"; IconFilename: "{app}\assets\nexus.ico"
Name: "{group}\Nexus 电量托盘"; Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\tray_app.py"""; WorkingDir: "{app}"; IconFilename: "{app}\assets\nexus.ico"
Name: "{group}\检查 Nexus 更新"; Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\my_devices.pyw"" --check-updates"; WorkingDir: "{app}"; IconFilename: "{app}\assets\nexus.ico"
Name: "{group}\卸载 Nexus"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Nexus 外设电量中心"; Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\my_devices.pyw"""; WorkingDir: "{app}"; IconFilename: "{app}\assets\nexus.ico"; Tasks: desktopicon
Name: "{autodesktop}\Nexus 电量托盘"; Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\tray_app.py"""; WorkingDir: "{app}"; IconFilename: "{app}\assets\nexus.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\my_devices.pyw"""; WorkingDir: "{app}"; Description: "打开 Nexus 外设电量中心"; Flags: nowait postinstall skipifsilent
Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\tray_app.py"""; WorkingDir: "{app}"; Description: "启动电量托盘"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\runtime\python.exe"; Parameters: """{app}\installer_helper.py"" --uninstall"; WorkingDir: "{app}"; Flags: runhidden waituntilterminated skipifdoesntexist

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  PythonPath, HelperPath: String;
  ExitCode: Integer;
begin
  Result := '';
  PythonPath := ExpandConstant('{app}\runtime\python.exe');
  HelperPath := ExpandConstant('{app}\installer_helper.py');
  if FileExists(PythonPath) and FileExists(HelperPath) then
    if (not Exec(PythonPath, '"' + HelperPath + '"', ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ExitCode)) or (ExitCode <> 0) then
      Result := '请先退出 Nexus 主窗口和托盘，再重新安装。';
end;
