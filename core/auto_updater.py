"""AutoUpdater: actualización automática desde releases PRIVADAS de GitHub.

Pensado para una app empaquetada con PyInstaller (modo `sys.frozen`) e instalada
con Inno Setup. Flujo:

  1. Consulta la última release: GET /repos/{owner}/{repo}/releases/latest, con el
     Token PAT en el header `Authorization: Bearer <TOKEN>`.
  2. Compara la versión local (core.version.__version__) contra el `tag_name`.
  3. Si hay una más nueva, ubica el asset del instalador, toma su id y lo descarga
     por la API de assets privados
     (GET /repos/{owner}/{repo}/releases/assets/{asset_id}) con
     `Accept: application/octet-stream`. Lo guarda como `nuevo_instalador.exe`.
  4. Escribe un .bat temporal que espera ~3 s (a que la app cierre con
     sys.exit()), corre el instalador en modo silencioso de Inno Setup
     (`/SILENT /SUPPRESSMSGBOXES /NORESTART`), REINICIA la app ya actualizada y
     luego se borra a sí mismo y al instalador. El .bat se lanza desacoplado para
     sobrevivir al cierre.

Sin dependencias externas: usa solo la librería estándar (urllib), apto para
empaquetar con PyInstaller.

Nota de seguridad: en una app distribuida, el PAT queda accesible (binario o
config). Usa un token de mínimo alcance (solo lectura de este repo). El token se
toma de la variable de entorno QUETZALTIC_GITHUB_PAT (ver core/entorno.py), que
puede definirse en el sistema o en un archivo .env junto a la app; evita
embeberlo en el código.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

from core import certificados, entorno, rutas
from core.version import __version__ as VERSION_ACTUAL

# --- Configuración del repositorio (privado) ---
OWNER = "angelignaciobaldi-jpg"
REPO = "Herramienta-integral-de-activos-fijos"
NOMBRE_ASSET = "Instalador_ActivosFijos.exe"
NOMBRE_DESCARGA = "nuevo_instalador.exe"
NOMBRE_BAT = "actualizar_activos_fijos.bat"
# Rastro que deja el .bat de la actualización. Sin esto, un instalador que falla
# no deja NADA: la app se reinicia en la versión vieja y nadie sabe por qué.
NOMBRE_RESULTADO = "actualizador_resultado.txt"
NOMBRE_LOG_INSTALADOR = "actualizador_instalador.log"

# Códigos de salida de Inno Setup, en lo que significan para quien usa la app.
_CODIGOS_INNO = {
    "1": "el instalador no pudo iniciarse",
    "2": "la instalación se canceló",
    "3": "error grave preparando la instalación",
    "4": "error grave durante la instalación",
    "5": ("la instalación se canceló: normalmente porque la aplicación seguía "
          "abierta y no se pudieron reemplazar sus archivos"),
    "6": "la instalación se interrumpió desde fuera (¿antivirus?)",
    "8": "hace falta reiniciar Windows para terminar la instalación",
}

API = "https://api.github.com"
API_VERSION = "2022-11-28"
USER_AGENT = "Quetzaltic-AutoUpdater"


class ErrorActualizacion(Exception):
    """Falla durante la búsqueda, descarga o aplicación de la actualización."""


# Resultado del chequeo que ve el usuario. Se distingue "al día" de "no se pudo
# comprobar" porque confundirlos es exactamente lo que volvía imposible de
# diagnosticar un equipo que no se actualiza: la app decía «Ya tienes la última
# versión» cuando en realidad no había podido ni consultar.
AL_DIA = "al_dia"
DISPONIBLE = "disponible"
SIN_VERIFICAR = "sin_verificar"


class _RedireccionSinAuth(urllib.request.HTTPRedirectHandler):
    """Quita el header Authorization cuando GitHub redirige el asset a otro host.

    La API de assets responde con un 302 hacia una URL firmada (S3) que rechaza
    el header `Authorization`. Hay que dejar de mandarlo al cambiar de dominio."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        nueva = super().redirect_request(req, fp, code, msg, headers, newurl)
        if nueva is not None:
            host_orig = urllib.parse.urlsplit(req.full_url).hostname
            host_nuevo = urllib.parse.urlsplit(newurl).hostname
            if host_orig != host_nuevo:
                for cabecera in list(nueva.headers):
                    if cabecera.lower() == "authorization":
                        del nueva.headers[cabecera]
        return nueva


class AutoUpdater:
    """Comprueba, descarga y aplica actualizaciones desde releases de GitHub."""

    def __init__(
        self,
        token: str | None = None,
        owner: str = OWNER,
        repo: str = REPO,
        version_actual: str = VERSION_ACTUAL,
        nombre_asset: str = NOMBRE_ASSET,
        timeout: int = 30,
    ):
        # Por defecto, el PAT se toma del entorno (QUETZALTIC_GITHUB_PAT).
        token = token or entorno.github_pat(requerido=False)
        if not token:
            raise ErrorActualizacion(
                "Falta el Token PAT para acceder al repo privado. Define la "
                "variable de entorno QUETZALTIC_GITHUB_PAT (o un archivo .env)."
            )
        self.token = token
        self.owner = owner
        self.repo = repo
        self.version_actual = version_actual
        self.nombre_asset = nombre_asset
        self.timeout = timeout
        # La verificación TLS se delega al almacén de Windows: en un equipo con
        # antivirus o proxy que inspecciona HTTPS, la lista propia de Python no
        # conoce al emisor y la actualización moría con CERTIFICATE_VERIFY_FAILED
        # (ver core/certificados.py).
        self._opener = urllib.request.build_opener(
            _RedireccionSinAuth,
            urllib.request.HTTPSHandler(context=certificados.contexto()))

    # ------------------------------------------------------- peticiones
    def _headers(self, accept: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": accept,
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": USER_AGENT,
        }

    def _pedir(self, url: str, accept: str) -> bytes:
        req = urllib.request.Request(url, headers=self._headers(accept))
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            raise ErrorActualizacion(
                f"GitHub respondió {exc.code} al pedir {url}: {exc.reason}"
            ) from exc
        except urllib.error.URLError as exc:
            raise ErrorActualizacion(f"No se pudo conectar a GitHub: {exc.reason}") from exc

    # --------------------------------------------------- chequeo de versión
    def obtener_release_latest(self) -> dict:
        """Devuelve el JSON de la última release del repo."""
        url = f"{API}/repos/{self.owner}/{self.repo}/releases/latest"
        datos = self._pedir(url, "application/vnd.github+json")
        return json.loads(datos.decode("utf-8"))

    def _id_asset(self, release: dict) -> int | None:
        """Busca el id del asset por nombre dentro de la release."""
        for asset in release.get("assets", []):
            if asset.get("name") == self.nombre_asset:
                return asset.get("id")
        return None

    @staticmethod
    def _normalizar(version: str) -> tuple[int, ...]:
        """Convierte 'v1.2.3' / '1.2.3' en (1, 2, 3) para comparar numéricamente."""
        partes = re.findall(r"\d+", version or "")
        return tuple(int(p) for p in partes) or (0,)

    def hay_version_mas_nueva(self, tag_name: str) -> bool:
        """True si `tag_name` (remoto) es mayor que la versión local."""
        remota = self._normalizar(tag_name)
        local = self._normalizar(self.version_actual)
        largo = max(len(remota), len(local))
        remota += (0,) * (largo - len(remota))
        local += (0,) * (largo - len(local))
        return remota > local

    # ------------------------------------------------------ descarga
    def descargar_asset(self, asset_id: int, destino: str | None = None) -> str:
        """Descarga el asset privado como `nuevo_instalador.exe`. Devuelve la ruta."""
        if destino is None:
            destino = os.path.join(self._dir_temporal(), NOMBRE_DESCARGA)
        destino = os.path.abspath(destino)
        url = f"{API}/repos/{self.owner}/{self.repo}/releases/assets/{asset_id}"
        contenido = self._pedir(url, "application/octet-stream")
        try:
            with open(destino, "wb") as fh:
                fh.write(contenido)
        except OSError as exc:
            raise ErrorActualizacion(f"No se pudo guardar el instalador: {exc}") from exc
        return destino

    # ------------------------------------------- aplicar y reiniciar
    def aplicar_y_salir(self, ruta_instalador: str) -> None:
        """Escribe el .bat, lo lanza desacoplado y cierra la app (sys.exit()).

        El .bat espera ~3 s, corre el instalador mostrando su barra de progreso
        (/SILENT), REINICIA la app ya actualizada y se autolimpia."""
        ruta_instalador = os.path.abspath(ruta_instalador)
        ruta_bat = os.path.join(self._dir_temporal(), NOMBRE_BAT)
        # Ruta de la app a reiniciar tras instalar (el mismo .exe en ejecución;
        # la actualización lo sobrescribe en el sitio).
        exe = os.path.abspath(sys.executable)
        dir_exe = os.path.dirname(exe)
        nombre_exe = os.path.basename(exe)
        resultado = os.path.join(rutas.DATOS, NOMBRE_RESULTADO)
        log_inno = os.path.join(rutas.DATOS, NOMBRE_LOG_INSTALADOR)
        # Dos cosas que este .bat aprendió por las malas:
        #
        # 1) ESPERAR A QUE LA APP MUERA DE VERDAD. Antes esperaba 3 s fijos. Si el
        #    proceso seguía vivo, Inno no podía reemplazar el .exe en uso y
        #    abortaba —en silencio, por /SUPPRESSMSGBOXES—; el .bat reiniciaba la
        #    versión VIEJA y borraba el instalador, así que no quedaba ni rastro y
        #    el anti-bucle daba esa release por aplicada para siempre. Ahora sondea
        #    el proceso hasta ~60 s.
        # 2) DEJAR CONSTANCIA. El código de salida del instalador se guarda, y el
        #    log de Inno también: es lo único que permite decir QUÉ pasó en vez de
        #    «no se actualizó».
        #
        # /SILENT (no /VERYSILENT) para que Inno muestre su barra de progreso.
        # 'ping -n' da esperas fiables en un proceso sin consola (a diferencia de
        # 'timeout', que necesita una consola interactiva).
        contenido = (
            "@echo off\r\n"
            "setlocal\r\n"
            "rem Espera a que la aplicacion termine de cerrarse (hasta ~60 s).\r\n"
            "set INTENTOS=0\r\n"
            ":esperar\r\n"
            f'tasklist /FI "IMAGENAME eq {nombre_exe}" 2>nul | '
            f'find /I "{nombre_exe}" >nul\r\n'
            "if errorlevel 1 goto instalar\r\n"
            "set /a INTENTOS+=1\r\n"
            "if %INTENTOS% GEQ 30 goto instalar\r\n"
            "ping 127.0.0.1 -n 3 >nul\r\n"
            "goto esperar\r\n"
            ":instalar\r\n"
            f'"{ruta_instalador}" /SILENT /SUPPRESSMSGBOXES /NORESTART '
            f'/LOG="{log_inno}"\r\n'
            # Entre paréntesis a propósito: 'echo %ERRORLEVEL%> fichero' con un
            # código de UN dígito lo lee cmd como «redirige el flujo 5», y el
            # archivo queda vacío. Con los paréntesis no hay ambigüedad.
            f'(echo %ERRORLEVEL%)> "{resultado}"\r\n'
            "rem Reinicia la app (actualizada si el instalador pudo).\r\n"
            f'start "" /D "{dir_exe}" "{exe}"\r\n'
            f'del "{ruta_instalador}"\r\n'
            'del "%~f0"\r\n'
        )
        try:
            with open(ruta_bat, "w", encoding="ascii") as fh:
                fh.write(contenido)
        except OSError as exc:
            raise ErrorActualizacion(f"No se pudo crear el script de actualización: {exc}") from exc

        flags = 0
        if os.name == "nt":
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(
            ["cmd", "/c", ruta_bat],
            creationflags=flags,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        sys.exit(0)

    # ---------------------------------------------------- orquestación
    def buscar_y_descargar(self, al_iniciar_descarga=None) -> str | None:
        """Comprueba y, si hay versión nueva, descarga el instalador y devuelve su
        ruta — SIN aplicarlo ni cerrar la app. Devuelve None si ya está al día.

        Al no llamar a sys.exit, es seguro ejecutarlo en un hilo (p. ej. con
        asyncio.to_thread) para no congelar la interfaz durante la descarga; luego
        el llamador aplica con aplicar_y_salir() en el hilo principal.

        `al_iniciar_descarga(tag)`: callback opcional justo antes de descargar.
        Lanza ErrorActualizacion ante cualquier problema (red, asset ausente…)."""
        release = self.obtener_release_latest()
        tag = release.get("tag_name", "")
        if not self.hay_version_mas_nueva(tag):
            return None
        # Anti-bucle: si esta MISMA release ya se intentó aplicar y aun así la
        # versión local no avanzó (p. ej. el instalador de la release trae una
        # versión más vieja que su propio tag), no reintentar: se caería en un
        # bucle infinito de "actualizar" en cada arranque.
        if self._tag_ya_aplicado(tag):
            return None
        asset_id = self._id_asset(release)
        if asset_id is None:
            raise ErrorActualizacion(
                f"La release {tag} no incluye el asset '{self.nombre_asset}'."
            )
        if al_iniciar_descarga is not None:
            al_iniciar_descarga(tag)
        ruta = self.descargar_asset(asset_id)
        # Marca la release ANTES de aplicar (aplicar_y_salir no retorna). Si el
        # instalador de esa release trae una versión más vieja que su tag, en el
        # siguiente arranque la versión seguirá sin avanzar; el guard de arriba
        # detectará que este tag ya se intentó y NO volverá a aplicarlo (evita el
        # bucle infinito de actualización).
        self._marcar_tag_aplicado(tag)
        return ruta

    def buscar_y_actualizar(self, al_iniciar_descarga=None) -> bool:
        """Comprueba, y si hay versión nueva descarga y aplica (no retorna: la
        app se cierra y se reinicia sola). Devuelve False si ya está actualizada."""
        ruta = self.buscar_y_descargar(al_iniciar_descarga)
        if ruta is None:
            return False
        self.aplicar_y_salir(ruta)  # no retorna: cierra la app y la reinicia
        return True

    def hay_actualizacion(self) -> str | None:
        """Devuelve el tag de la última release si es MÁS NUEVA que la versión
        instalada (y no se marcó ya como aplicada, para no ofrecer una que
        entraría en bucle); si no, None. Solo CONSULTA, no descarga nada. Útil
        para avisar al usuario y que él decida cuándo aplicarla."""
        release = self.obtener_release_latest()
        tag = release.get("tag_name", "")
        if not self.hay_version_mas_nueva(tag) or self._tag_ya_aplicado(tag):
            return None
        return tag

    # -------------------------------------------------- estado anti-bucle
    @staticmethod
    def _ruta_estado() -> str:
        """Archivo (en datos del usuario, que el instalador NO sobrescribe) donde
        se recuerda el último tag que se intentó aplicar."""
        return os.path.join(rutas.DATOS, "actualizador_estado.json")

    def _tag_ya_aplicado(self, tag: str) -> bool:
        try:
            with open(self._ruta_estado(), encoding="utf-8") as fh:
                return json.load(fh).get("ultimo_tag_aplicado") == tag
        except (OSError, json.JSONDecodeError):
            return False

    def _marcar_tag_aplicado(self, tag: str) -> None:
        try:
            os.makedirs(os.path.dirname(self._ruta_estado()), exist_ok=True)
            with open(self._ruta_estado(), "w", encoding="utf-8") as fh:
                json.dump({"ultimo_tag_aplicado": tag}, fh)
        except OSError:
            pass  # si no se puede escribir, el peor caso es reintentar una vez

    # ------------------------------------------------------- utilidades
    @staticmethod
    def _dir_temporal() -> str:
        """Carpeta escribible para el instalador y el .bat.

        Con `sys.frozen` la app suele vivir en Archivos de Programa (solo lectura
        sin elevación), así que se usa siempre %TEMP%, que es escribible."""
        _ = getattr(sys, "frozen", False)  # ejecutándose como .exe de PyInstaller
        return tempfile.gettempdir()


# Prefijo convencional del commit -> cómo se le dice al usuario. Lo que no está
# aquí (docs, chore, refactor, test…) es trabajo interno y no se lista.
_TIPOS_CAMBIO = {"feat": "Nuevo", "fix": "Corregido", "perf": "Más rápido"}
_RE_PREFIJO = re.compile(r"^(\w+)(?:\([^)]*\))?!?:\s*(.+)$")
# Tope de renglones: el modal es para decidir si actualizar ahora, no un historial.
_MAX_CAMBIOS = 12


def notas_de_version(tag_nuevo: str, version_actual: str = VERSION_ACTUAL) -> list[str]:
    """Qué trae la versión nueva, en renglones listos para mostrar. Nunca lanza.

    Sale de los COMMITS entre la versión instalada y la nueva (API compare de
    GitHub), no de la nota de la release: las notas hasta hoy dicen «ok» o son un
    párrafo, y los commits de este repo ya describen cada cambio en español. Así
    la lista existe aunque nadie la escriba al publicar, y cubre TODAS las
    versiones que el equipo se saltó, no solo la última.

    Se quitan los merges y el trabajo interno, y el prefijo técnico se traduce:
    «fix: la búsqueda borraba…» -> «Corregido: la búsqueda borraba…».
    Devuelve [] si no se puede consultar (sin token, sin red, versión local sin
    tag como la de desarrollo): el modal se muestra igual, solo sin la lista.
    """
    try:
        actualizador = AutoUpdater()
        url = (f"{API}/repos/{actualizador.owner}/{actualizador.repo}/compare/"
               f"{urllib.parse.quote(version_actual)}...{urllib.parse.quote(tag_nuevo)}")
        datos = json.loads(actualizador._pedir(url, "application/vnd.github+json"))
    except Exception:  # noqa: BLE001 — sin lista, pero la actualización sigue
        return []
    renglones, vistos = [], set()
    for c in datos.get("commits", []):
        if len(c.get("parents") or []) > 1:
            continue                        # merge de un PR: no dice qué cambió
        titulo = ((c.get("commit") or {}).get("message") or "").splitlines()
        titulo = titulo[0].strip() if titulo else ""
        m = _RE_PREFIJO.match(titulo)
        if m:
            etiqueta = _TIPOS_CAMBIO.get(m.group(1).lower())
            if etiqueta is None:
                continue                    # trabajo interno
            texto = m.group(2).strip()
            renglon = f"{etiqueta}: {texto[:1].upper()}{texto[1:]}"
        elif titulo:
            renglon = titulo
        else:
            continue
        if renglon not in vistos:
            vistos.add(renglon)
            renglones.append(renglon)
    # Lo más reciente primero: si hay que recortar, que se pierda lo más viejo.
    renglones.reverse()
    if len(renglones) > _MAX_CAMBIOS:
        resto = len(renglones) - _MAX_CAMBIOS
        renglones = renglones[:_MAX_CAMBIOS] + [f"…y {resto} cambio(s) más."]
    return renglones


def _codigo_ultima_instalacion() -> str:
    """Código con que terminó el instalador la última vez ("" si no hay rastro)."""
    try:
        with open(os.path.join(rutas.DATOS, NOMBRE_RESULTADO),
                  encoding="utf-8", errors="ignore") as fh:
            return fh.read().strip().split()[0]
    except (OSError, IndexError):
        return ""


def permitir_reintento() -> None:
    """Olvida el último intento, para que la release bloqueada se pueda instalar.

    El anti-bucle es correcto mientras nadie mire: evita que una release cuyo
    instalador no avanza se reintente sin fin. Pero convertía en definitivo lo que
    casi siempre es temporal —la app seguía abierta, el antivirus se metió— y la
    única salida era borrar un .json a mano. Cuando el usuario pide reintentar
    explícitamente, esa protección sobra: ya hay alguien mirando."""
    for nombre in (AutoUpdater._ruta_estado(),
                   os.path.join(rutas.DATOS, NOMBRE_RESULTADO)):
        try:
            os.remove(nombre)
        except OSError:
            pass    # no existía, o no se puede borrar: se intenta igual


def hay_intento_bloqueado() -> bool:
    """Si hay una release marcada como intentada (la que el botón desbloquearía)."""
    try:
        with open(AutoUpdater._ruta_estado(), encoding="utf-8") as fh:
            return bool(json.load(fh).get("ultimo_tag_aplicado"))
    except (OSError, json.JSONDecodeError):
        return False


# ================================================================ diagnóstico
def comprobar() -> tuple[str, str]:
    """(estado, detalle) del chequeo, SIN tragarse el motivo de un fallo.

    `estado` es AL_DIA, DISPONIBLE (detalle = tag) o SIN_VERIFICAR (detalle = por
    qué no se pudo). Es lo que usa el botón «Buscar actualizaciones».
    """
    if not getattr(sys, "frozen", False):
        return SIN_VERIFICAR, "La actualización solo funciona en la app instalada."
    if not entorno.github_pat(requerido=False):
        return SIN_VERIFICAR, ("La aplicación no encuentra el token "
                               "QUETZALTIC_GITHUB_PAT.")
    try:
        actualizador = AutoUpdater()
        tag = actualizador.obtener_release_latest().get("tag_name", "")
    except Exception as exc:  # noqa: BLE001 — el motivo se devuelve, no se calla
        return SIN_VERIFICAR, str(exc)
    if not actualizador.hay_version_mas_nueva(tag):
        return AL_DIA, ""
    # No se usa `hay_actualizacion()`: devuelve None tanto si está al día como si
    # la release quedó BLOQUEADA por el anti-bucle, y ese segundo caso —el de una
    # instalación duplicada que nunca avanza— es justo el que hay que ver.
    if actualizador._tag_ya_aplicado(tag):
        return SIN_VERIFICAR, (f"La versión {tag} ya se intentó instalar y la "
                               f"instalada sigue siendo {VERSION_ACTUAL}.")
    return DISPONIBLE, tag


def _registrada_en_windows(nombre: str) -> list[str]:
    """Dónde está REGISTRADA la variable en Windows ('usuario' / 'sistema').

    Es distinto de si el proceso la ve: una variable recién registrada solo
    llega a los programas que se abren DESPUÉS de iniciar sesión, porque el
    escritorio de Windows se queda con el entorno que tenía al entrar. Comparar
    las dos cosas es lo que delata ese caso, que es el más común."""
    if os.name != "nt":
        return []
    import winreg

    donde = []
    for etiqueta, raiz, ruta in (
            ("usuario", winreg.HKEY_CURRENT_USER, "Environment"),
            ("sistema", winreg.HKEY_LOCAL_MACHINE,
             r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
        try:
            with winreg.OpenKey(raiz, ruta) as clave:
                valor, _ = winreg.QueryValueEx(clave, nombre)
                if str(valor).strip():
                    donde.append(etiqueta)
        except OSError:
            continue
    return donde


def _enmascarar(token: str) -> str:
    """Tipo y últimos 4 caracteres: suficiente para confirmar QUÉ token es y si
    es clásico (ghp_) o de grano fino (github_pat_), sin exponerlo."""
    t = (token or "").strip()
    tipo = ("de grano fino" if t.startswith("github_pat_")
            else "clásico" if t.startswith("ghp_") else "de formato desconocido")
    return f"token {tipo} terminado en …{t[-4:]}" if len(t) >= 8 else "token muy corto"


def diagnosticar() -> list[tuple[str, str, str]]:
    """Revisa, paso por paso, todo lo que necesita la actualización automática.

    Devuelve [(paso, estado, detalle)] con estado 'ok', 'aviso' o 'error', en el
    orden en que fallaría: no tiene caso probar la conexión si no hay token.
    Nunca lanza. Pensado para correrse en el equipo que no se actualiza y leerse
    ahí mismo, porque desde fuera no hay forma de ver su entorno.
    """
    pasos: list[tuple[str, str, str]] = []
    congelado = getattr(sys, "frozen", False)

    pasos.append(("Versión instalada", "ok", VERSION_ACTUAL))

    # --- 1. ¿Dónde vive la app? -------------------------------------------
    exe = os.path.abspath(sys.executable)
    if not congelado:
        pasos.append(("Instalación", "aviso",
                      "Se está ejecutando desde el código fuente, no instalada: "
                      "la actualización automática no aplica."))
    else:
        local = os.environ.get("LOCALAPPDATA", "")
        por_usuario = os.path.join(local, "Programs") if local else ""
        progs = [os.environ.get(v, "") for v in ("ProgramFiles", "ProgramFiles(x86)")]
        rel = os.path.join("Quetzaltic Solutions", "Herramientas Activos Fijos",
                           "ActivosFijos.exe")
        copias = [c for c in ([os.path.join(por_usuario, rel)] if por_usuario else [])
                  + [os.path.join(pf, rel) for pf in progs if pf]
                  if os.path.exists(c)]
        if len(copias) > 1:
            pasos.append((
                "Instalación", "error",
                "Hay DOS copias instaladas:\n  " + "\n  ".join(copias) +
                f"\nSe está usando: {exe}\n"
                "La actualización se instala por usuario, pero el acceso directo "
                "abre la otra copia, que nunca cambia. Desinstala la de Archivos "
                "de programa y deja solo la de AppData."))
        elif any(pf and exe.lower().startswith(pf.lower()) for pf in progs):
            pasos.append((
                "Instalación", "error",
                f"Instalada en Archivos de programa ({exe}).\n"
                "Se instaló como administrador, y la actualización corre sin "
                "permisos: instala otra copia por usuario que el acceso directo "
                "no abre. Desinstálala y vuelve a instalar SIN «Ejecutar como "
                "administrador»."))
        else:
            pasos.append(("Instalación", "ok", exe))

    # --- 2. ¿La app ve el token? -------------------------------------------
    nombre = entorno.VAR_GITHUB_PAT
    en_archivos = []
    for ruta in entorno._rutas_env():
        try:
            with open(ruta, encoding="utf-8") as fh:
                if any(linea.strip().startswith(nombre + "=") for linea in fh):
                    en_archivos.append(ruta)
        except OSError:
            continue
    registrada = _registrada_en_windows(nombre)
    token = entorno.github_pat(requerido=False)
    if token:
        origen = (f"archivo .env ({en_archivos[0]})" if en_archivos
                  else "variable de entorno de Windows")
        pasos.append(("Token", "ok", f"{_enmascarar(token)}, desde {origen}"))
    elif registrada:
        pasos.append((
            "Token", "error",
            f"La variable {nombre} SÍ está registrada en Windows "
            f"({' y '.join(registrada)}), pero la aplicación NO la ve.\n"
            "Pasa cuando se registra con la sesión abierta: los programas abiertos "
            "desde el escritorio heredan las variables de cuando se inició sesión. "
            "Cierra sesión de Windows (o reinicia) y vuelve a abrir la app."))
    else:
        buscado = "\n  ".join(entorno._rutas_env())
        pasos.append((
            "Token", "error",
            f"No hay token. La variable {nombre} no está registrada en Windows "
            f"y tampoco hay un .env con ella en:\n  {buscado}"))

    # --- 3. Anti-bucle -----------------------------------------------------
    try:
        with open(AutoUpdater._ruta_estado(), encoding="utf-8") as fh:
            ultimo = json.load(fh).get("ultimo_tag_aplicado", "")
    except (OSError, json.JSONDecodeError):
        ultimo = ""
    if ultimo:
        codigo = _codigo_ultima_instalacion()
        if codigo and codigo != "0":
            detalle = (
                f"Se intentó instalar la versión {ultimo} y el instalador terminó "
                f"con el código {codigo}: {_CODIGOS_INNO.get(codigo, 'error no identificado')}.\n"
                f"Detalle completo en:\n  {os.path.join(rutas.DATOS, NOMBRE_LOG_INSTALADOR)}")
            estado_paso = "error"
        elif codigo == "0":
            detalle = (
                f"Se instaló la versión {ultimo} sin errores. Si la versión de "
                "arriba sigue siendo la anterior, se está abriendo otra copia de "
                "la aplicación (revisa el paso «Instalación»).")
            estado_paso = "aviso"
        else:
            detalle = (
                f"Ya se intentó instalar la versión {ultimo}, pero no quedó "
                "constancia de cómo terminó (el intento es anterior a esta "
                "versión de la herramienta).")
            estado_paso = "aviso"
        pasos.append((
            "Último intento", estado_paso,
            detalle + "\nMientras la versión instalada no avance, esa release NO "
            "se vuelve a ofrecer sola (es la protección contra bucles). El botón "
            "«Reintentar instalación» de abajo la desbloquea."))
    else:
        pasos.append(("Último intento", "ok", "Sin intentos previos registrados."))

    # --- 4. Conexión con GitHub y permisos del token -----------------------
    pasos.append(("Certificados", "ok", certificados.origen()))
    if not token:
        pasos.append(("Conexión con GitHub", "aviso",
                      "No se probó: primero hace falta el token."))
        return pasos
    try:
        actualizador = AutoUpdater(token=token)
        release = actualizador.obtener_release_latest()
    except ErrorActualizacion as exc:
        texto = str(exc)
        if " 401 " in texto:
            causa = "El token es inválido o ya expiró. Genera uno nuevo."
        elif " 404 " in texto:
            causa = ("GitHub no muestra el repositorio a este token. El repo es "
                     "PRIVADO: el token necesita acceso de lectura a él (en los "
                     "de grano fino hay que agregarlo explícitamente).")
        elif " 403 " in texto:
            causa = ("GitHub negó el acceso: el token no tiene permiso de lectura "
                     "de contenidos, o se alcanzó el límite de consultas.")
        elif "CERTIFICATE_VERIFY_FAILED" in texto or "SSL" in texto:
            causa = ("Un antivirus o proxy de ese equipo está inspeccionando el "
                     "tráfico HTTPS y Windows no reconoce a quien firma sus "
                     "certificados. Sale a internet, pero no puede comprobar con "
                     "quién habla, y bajar el instalador sin comprobarlo no es "
                     "una opción.\n"
                     "Salidas: instalar el certificado raíz de ese antivirus en el "
                     "almacén de Windows (Entidades de certificación raíz de "
                     "confianza), excluir api.github.com y objects.githubusercontent.com "
                     "de la inspección HTTPS, o apuntar la variable "
                     f"{certificados.VAR_BUNDLE} a un .pem con ese certificado.")
        elif "conectar" in texto:
            causa = ("No hay salida a api.github.com. Revisa proxy, firewall o "
                     "antivirus de ese equipo.")
        else:
            causa = ""
        pasos.append(("Conexión con GitHub", "error",
                      (causa + "\n" if causa else "") + f"Detalle: {texto}"))
        return pasos
    tag = release.get("tag_name", "")
    pasos.append(("Conexión con GitHub", "ok", f"Última release publicada: {tag}"))

    # --- 5. ¿La release trae instalador? -----------------------------------
    if actualizador._id_asset(release) is None:
        pasos.append(("Instalador en la release", "error",
                      f"La release {tag} no incluye «{NOMBRE_ASSET}»."))
        return pasos
    pasos.append(("Instalador en la release", "ok", NOMBRE_ASSET))

    # --- 6. Veredicto -------------------------------------------------------
    if not actualizador.hay_version_mas_nueva(tag):
        pasos.append(("Resultado", "ok",
                      f"Está al día: {VERSION_ACTUAL} ≥ {tag}."))
    elif actualizador._tag_ya_aplicado(tag):
        pasos.append((
            "Resultado", "error",
            f"Hay versión nueva ({tag}) pero está BLOQUEADA: ya se intentó "
            "instalar y la versión no avanzó. Mira el paso «Último intento» para "
            "saber por qué. Cierra la aplicación si tienes otra ventana abierta y "
            "usa «Reintentar instalación»."))
    else:
        pasos.append(("Resultado", "ok",
                      f"Hay versión nueva ({tag}) y se puede instalar."))
    return pasos
