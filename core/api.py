"""Cliente HTTP mínimo para los microservicios (opcional).

Envuelve las peticiones a la API de la plataforma resolviendo la URL base y el
token desde core/ajustes_api.py (preferencia local -> variable de entorno). Usa
solo la librería estándar (urllib) para no añadir dependencias al empaquetado.

Este módulo es un ESQUELETO: agrega aquí los endpoints concretos que consuma la
herramienta de Activos Fijos (p. ej. catálogos, validaciones, sincronización).
"""

from __future__ import annotations

import json
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

from core import ajustes_api, certificados

TIMEOUT = 30
# Un tropiezo de red no debe costar la consulta entera: el DNS de Windows falla
# suelto al volver de suspensión o al cambiar de red, y el barrido del catálogo
# son varias páginas en paralelo, así que basta con que UNA falle para perderlo
# todo. Solo se reintentan las lecturas (GET): repetir una escritura la duplica.
REINTENTOS = 2
ESPERA_REINTENTO = 1.5   # segundos entre intentos


class ErrorAPI(Exception):
    """Falla al llamar a un microservicio (red, autenticación, respuesta no OK)."""


# --- Envoltorio de los procedimientos almacenados -------------------------
def primero(bloque) -> dict:
    """Normaliza un bloque que puede venir como objeto o como lista de uno.

    En T-SQL eso lo decide el `WITHOUT_ARRAY_WRAPPER` de cada subconsulta, y un
    bloque que hoy llega suelto puede llegar envuelto mañana. Aceptar las dos
    formas evita que un `ALTER` a medio aplicar en un entorno deje una parte de
    la pantalla en blanco sin explicación.
    """
    if isinstance(bloque, list):
        bloque = bloque[0] if bloque else None
    return bloque if isinstance(bloque, dict) else {}


def desanidar(respuesta: dict, clave: str) -> dict:
    """Saca el objeto de las DOS capas de JSON con que responden los SP.

    El servicio devuelve `data[0].<clave>` como una CADENA que a su vez contiene
    el JSON con los datos. No es un defecto del serializador —el SP devuelve un
    renglón con una columna `NVARCHAR`, y una columna de texto se serializa como
    texto—, así que desanidarlo es responsabilidad del cliente.
    """
    filas = respuesta.get("data") or []
    if not filas:
        return {}
    crudo = filas[0].get(clave) or ""
    if not crudo:
        return {}
    return primero(json.loads(crudo))


# Cabecera con la que el servicio espera el token. NO es `Authorization: Bearer`:
# va en una cabecera propia y con el token DESNUDO, sin prefijo de esquema. Se
# deja como constante para que se vea que es una decisión del servidor y no un
# descuido que alguien "corrija" de vuelta al estándar (con Bearer responde 401).
CABECERA_TOKEN = "x-auth-token"


def _headers() -> dict[str, str]:
    cabeceras = {"Accept": "application/json", "Content-Type": "application/json"}
    tok = ajustes_api.token()
    if tok:
        cabeceras[CABECERA_TOKEN] = tok
    return cabeceras


def _url(ruta: str, params: dict | None = None) -> str:
    base = ajustes_api.base_url(requerido=True)
    # La ruta se codifica porque alguna lleva acento (`.../inversión`) y urllib
    # solo admite URLs ASCII: sin esto revienta con UnicodeEncodeError antes
    # siquiera de salir a la red. `safe="/"` conserva los separadores.
    url = f"{base}/{urllib.parse.quote(ruta.lstrip('/'), safe='/')}"
    if params:
        # Los None se OMITEN en vez de mandarse vacíos: un filtro sin elegir
        # ("todas las empresas") no debe viajar como `empresa=`, que el servidor
        # tendría que distinguir de un valor legítimo.
        limpios = {k: v for k, v in params.items() if v is not None}
        if limpios:
            url += "?" + urllib.parse.urlencode(limpios)
    return url


def _motivo_red(razon) -> str:
    """Traduce el fallo de red a algo que el usuario pueda accionar.

    `URLError.reason` es el error del sistema operativo tal cual —«[Errno 11001]
    getaddrinfo failed»—, que no le dice a nadie qué hacer. Lo que hay detrás de
    cada caso sí: el nombre no se resolvió (internet/VPN/DNS), el servidor no
    contestó, o el certificado no validó.
    """
    servidor = ajustes_api.base_url() or "la API"
    if isinstance(razon, ssl.SSLError):
        return (f"no se pudo validar el certificado de {servidor}. Si el equipo "
                f"tiene antivirus que inspecciona HTTPS, revisa el diagnóstico "
                f"de actualización.")
    if isinstance(razon, socket.gaierror) or "getaddrinfo" in str(razon):
        return (f"no se pudo resolver el nombre de {servidor}. Suele ser la "
                f"conexión a internet o la VPN: revísala y vuelve a intentar.")
    if isinstance(razon, (TimeoutError, socket.timeout)) or "timed out" in str(razon):
        return f"{servidor} no respondió en {TIMEOUT} segundos."
    if isinstance(razon, ConnectionRefusedError) or "refused" in str(razon).lower():
        return f"{servidor} rechazó la conexión."
    return f"no se pudo conectar con {servidor}: {razon}"


def solicitar(ruta: str, metodo: str = "GET", cuerpo: dict | None = None, *,
              params: dict | None = None) -> dict:
    """Hace una petición a `ruta` (relativa a la URL base) y devuelve el JSON.

    Lanza ErrorAPI ante fallos de red/HTTP. `cuerpo` (si se pasa) se envía como
    JSON en el body (para POST/PUT); `params` va en la cadena de consulta.

    `params` es keyword-only porque los posicionales ya estaban tomados por
    `ruta` y `metodo`: colarlo en medio habría corrido `cuerpo` de sitio."""
    datos = json.dumps(cuerpo).encode("utf-8") if cuerpo is not None else None
    req = urllib.request.Request(_url(ruta, params), data=datos,
                                 headers=_headers(), method=metodo)
    intentos = REINTENTOS + 1 if metodo.upper() == "GET" else 1
    for intento in range(1, intentos + 1):
        try:
            # Mismo contexto TLS que el actualizador: en equipos con antivirus
            # que inspecciona HTTPS, el de Python no valida (ver core/certificados.py).
            with urllib.request.urlopen(req, timeout=TIMEOUT,
                                        context=certificados.contexto()) as resp:
                crudo = resp.read().decode("utf-8")
            return json.loads(crudo) if crudo else {}
        except urllib.error.HTTPError as exc:
            # Un 4xx/5xx es respuesta del servidor, no un tropiezo de red: no se
            # reintenta (con un 401 se repetiría el fallo tres veces).
            raise ErrorAPI(f"La API respondió {exc.code}: {exc.reason}") from exc
        except urllib.error.URLError as exc:
            if intento < intentos:
                time.sleep(ESPERA_REINTENTO)
                continue
            raise ErrorAPI(f"No se pudo conectar con la API: "
                           f"{_motivo_red(exc.reason)}") from exc
        except json.JSONDecodeError as exc:
            raise ErrorAPI(
                "La API devolvió una respuesta que no es JSON válido.") from exc
    raise ErrorAPI("No se pudo conectar con la API.")   # inalcanzable
