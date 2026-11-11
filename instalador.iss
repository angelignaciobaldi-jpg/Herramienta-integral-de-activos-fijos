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
; Al terminar, avisa al shell (SHChangeNotify) para que vuelva a leer los iconos.
; Windows los guarda en cache POR RUTA: las versiones anteriores se compilaron sin
; icono propio, y como la ruta del .exe no cambia, la barra de tareas seguia
; mostrando el de PyInstaller aunque el archivo nuevo ya trajera el nuestro.
ChangesAssociations=yes

[Files]
; Carpeta de salida de flet pack/PyInstaller (onedir). El nombre 'ActivosFijos'
; debe coincidir con el -n del build (ver README).
; Excludes: NO se instalan scripts de shell. Los unicos .ps1/.sh del paquete son
; los 17 de playwright\driver\package\bin, y la app NO ejecuta ninguno: solo
; llama a `install chromium --no-shell`, mientras que esos sirven para WebKit
; dentro de WSL, el media pack de Windows y reinstalar Chrome/Edge de marca.
; Se excluyen porque un antivirus puede poner en cuarentena un .ps1 suelto
; dentro de la carpeta del programa; el archivo queda en estado "borrado
; pendiente" y cualquier intento de reemplazarlo devuelve acceso denegado, con
; lo que la instalacion se traba (MoveFile failed; code 5 sobre
; install_webkit_wsl.ps1, reportado en un equipo con la 1.4.1). Codigo que no se
; envia no se puede bloquear.
Source: ".\dist\ActivosFijos\*"; DestDir: "{app}"; Excludes: "*.ps1,*.sh"; Flags: ignoreversion recursesubdirs createallsubdirs
; Icono para los accesos directos, copiado a la RAIZ de {app}. Se toma del codigo
; fuente (no del build): PyInstaller (onedir) mete 'Imagenes' dentro de
; {app}\_internal, asi que un IconFilename a {app}\Imagenes\icon.ico no existiria.
Source: ".\Imagenes\icon.ico"; DestDir: "{app}"; Flags: ignoreversion

[InstallDelete]
; Las versiones hasta la 1.4.1 SI instalaron esos scripts, asi que en los equipos
; ya instalados la copia vieja sigue en disco (y es justo la que se traba). Se
; intenta borrarla al actualizar. Si alguna esta bloqueada por el antivirus, Inno
; lo anota en el log y CONTINUA: borrarlas es limpieza, no requisito, y con
; [Files] ya no se intenta sobrescribirlas.
Type: files; Name: "{app}\_internal\playwright\driver\package\bin\*.ps1"
Type: files; Name: "{app}\_internal\playwright\driver\package\bin\*.sh"

[Icons]
; IconFilename apunta a {app}\icon.ico (copiado a la raiz en [Files]).
; AppUserModelID: DEBE coincidir con AUMID en core/win_taskbar.py para que el
; acceso y la ventana (creada por el flet.exe cliente) se agrupen como la MISMA
; app en la barra de tareas.
Name: "{group}\Herramientas Activos Fijos"; Filename: "{app}\ActivosFijos.exe"; WorkingDir: "{app}"; IconFilename: "{app}\icon.ico"; AppUserModelID: "QuetzalticSolutions.HerramientasActivosFijos"
; {autodesktop} = escritorio del usuario (no el comun, que requeriria admin).
Name: "{autodesktop}\Herramientas Activos Fijos"; Filename: "{app}\ActivosFijos.exe"; WorkingDir: "{app}"; IconFilename: "{app}\icon.ico"; AppUserModelID: "QuetzalticSolutions.HerramientasActivosFijos"

[Run]
; Refresca la cache de iconos del usuario (herramienta de Windows, sin admin). Va
; ademas de ChangesAssociations porque este corre TAMBIEN en la actualizacion
; silenciosa, que es justo donde el icono viejo se quedaba pegado.
Filename: "{sys}\ie4uinit.exe"; Parameters: "-show"; Flags: runhidden skipifdoesntexist
; Ejecuta la app al terminar la instalacion (no en modo silencioso/actualizacion).
Filename: "{app}\ActivosFijos.exe"; Description: "{cm:LaunchProgram,Herramientas Activos Fijos}"; Flags: nowait postinstall skipifsilent
