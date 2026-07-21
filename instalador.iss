[Setup]
; AppId FIJO: identifica la app entre versiones. Es lo que permite que el
; instalador descargado por el AutoUpdater actualice EN SITIO (sobrescribe) en
; vez de instalar una copia paralela. No lo cambies entre versiones.
AppId={{C0BFE019-AB7E-4879-A557-4F8FAC3CDEB9}
AppName=Herramientas Activos Fijos
; Mantener en sync con core/version.py (__version__). El CI lo reescribe con el
; tag del Release antes de compilar.
AppVersion=0.1.0
AppPublisher=Quetzaltic Solutions
; Instalacion POR USUARIO (en %LOCALAPPDATA%\Programs), NO en Archivos de
; Programa. Al ser una carpeta escribible por el usuario, la actualizacion
; silenciosa la sobrescribe SIN pedir permisos de administrador (sin UAC).
DefaultDirName={autopf}\Quetzaltic Solutions\Herramientas Activos Fijos
DefaultGroupName=Quetzaltic Solutions
OutputDir=.\Output
; Debe coincidir con el asset que busca el AutoUpdater (NOMBRE_ASSET):
;   Instalador_ActivosFijos.exe
OutputBaseFilename=Instalador_ActivosFijos
Compression=lzma2/ultra64
SolidCompression=yes
; 'lowest' = no solicita elevacion (sin UAC). Requisito para actualizar sin admin.
PrivilegesRequired=lowest

[Files]
; Carpeta de salida de flet pack/PyInstaller (onedir). El nombre 'ActivosFijos'
; debe coincidir con el -n del build (ver README).
Source: ".\dist\ActivosFijos\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Motor Tesseract OCR empaquetado (lo prepara el CI en '.\tesseract_bundle').
; skipifsourcedoesntexist: si no esta (build local sin OCR empaquetado) NO rompe
; la compilacion; la app cae a un Tesseract del sistema si existe.
Source: ".\tesseract_bundle\*"; DestDir: "{app}\Tesseract-OCR"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
; Icono para los accesos directos, copiado a la RAIZ de {app}. Se toma del codigo
; fuente (no del build): PyInstaller (onedir) mete 'Imagenes' dentro de
; {app}\_internal, asi que un IconFilename a {app}\Imagenes\icon.ico no existiria.
Source: ".\Imagenes\icon.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; IconFilename apunta a {app}\icon.ico (copiado a la raiz en [Files]).
; AppUserModelID: DEBE coincidir con AUMID en core/win_taskbar.py para que el
; acceso y la ventana (creada por el flet.exe cliente) se agrupen como la MISMA
; app en la barra de tareas.
Name: "{group}\Herramientas Activos Fijos"; Filename: "{app}\ActivosFijos.exe"; WorkingDir: "{app}"; IconFilename: "{app}\icon.ico"; AppUserModelID: "QuetzalticSolutions.HerramientasActivosFijos"
; {autodesktop} = escritorio del usuario (no el comun, que requeriria admin).
Name: "{autodesktop}\Herramientas Activos Fijos"; Filename: "{app}\ActivosFijos.exe"; WorkingDir: "{app}"; IconFilename: "{app}\icon.ico"; AppUserModelID: "QuetzalticSolutions.HerramientasActivosFijos"

[Run]
; Ejecuta la app al terminar la instalacion (no en modo silencioso/actualizacion).
Filename: "{app}\ActivosFijos.exe"; Description: "{cm:LaunchProgram,Herramientas Activos Fijos}"; Flags: nowait postinstall skipifsilent
