; Inno Setup script para Logitime
#define MyAppName "Logitime"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Logitime"
#define MyAppExeName "Logitime.exe"

[Setup]
AppId={{6E454EF6-9B2C-4C9D-B2A5-4F1D4BC8F5C0}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir={#SourcePath}\..\Output
OutputBaseFilename=Setup_Logitime
Compression=lzma
SolidCompression=yes
WizardStyle=modern

#ifexist "{#SourcePath}\\..\\assets\\icon.ico"
SetupIconFile={#SourcePath}\..\assets\icon.ico
#endif

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear acceso directo en el escritorio"; GroupDescription: "Accesos directos:"; Flags: unchecked

[Files]
Source: "{#SourcePath}\..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Ejecutar {#MyAppName}"; Flags: nowait postinstall skipifsilent
