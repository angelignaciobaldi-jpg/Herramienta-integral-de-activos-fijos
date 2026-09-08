"""Identidad de origen de un movimiento: qué equipo, quién y con qué versión.

El historial local (`movimientos_sipp` en core/db.py) ya guardaba QUÉ se envió al
SIPP y CUÁNDO. Lo que faltaba es DE DÓNDE salió, que es lo que permite responder
«¿esta herramienta se está usando, y en qué equipos?» sin ir máquina por máquina.

Este módulo solo RESUELVE esos datos; quien los persiste es `core/db.py`. Están
aparte porque son transversales: el día que se registren otros eventos (apertura
de la app, cargas locales) van a necesitar la misma identidad.

Todo aquí es **best-effort y nunca lanza**: la bitácora es un registro auxiliar y
no puede tumbar —ni frenar— una corrida del RPA que ya tocó el portal. Un dato
que no se pudo leer viaja vacío, que es honesto; inventar un valor sería peor.
"""

from __future__ import annotations

import getpass
import os
import platform
import uuid
from datetime import datetime, timezone

from core import credenciales, version

# La identidad de la máquina no cambia mientras la app vive, y resolverla toca el
# sistema operativo y el disco. Se calcula UNA vez: registrar un lote de 40 altas
# no puede pagar 40 veces esa consulta.
_cache: dict | None = None


def _equipo() -> str:
    """Nombre del equipo. `platform.node()` da el nombre NetBIOS en Windows.

    Se cae a COMPUTERNAME porque en algunos hosts sin resolución de nombre
    `node()` devuelve cadena vacía, y esa variable la fija siempre Windows."""
    try:
        return platform.node() or os.environ.get("COMPUTERNAME", "")
    except Exception:  # noqa: BLE001 — dato auxiliar
        return ""


def _usuario_windows() -> str:
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001 — sin HOME/USERNAME lanza, y no importa
        return ""


def _usuario_sipp() -> str:
    """Usuario con el que se opera el portal. Es el más útil de los tres: el
    equipo puede ser compartido, y el de Windows no dice quién firma en el SIPP."""
    try:
        return credenciales.usuario()
    except Exception:  # noqa: BLE001 — dato auxiliar
        return ""


def identidad(refrescar: bool = False) -> dict:
    """{equipo, usuario_windows, usuario_sipp, version_app}.

    `refrescar` re-lee la caché: el usuario del SIPP SÍ cambia en caliente (se
    captura desde Configuración), así que quien lo cambie debe invalidarla o el
    historial seguiría atribuyendo los movimientos al usuario anterior."""
    global _cache
    if _cache is None or refrescar:
        _cache = {
            "equipo": _equipo(),
            "usuario_windows": _usuario_windows(),
            "usuario_sipp": _usuario_sipp(),
            "version_app": getattr(version, "__version__", ""),
        }
    return dict(_cache)


def nuevo_id() -> str:
    """Identificador único del evento, generado en el cliente.

    Es lo que hace idempotente el envío al microservicio (fase 2): si un reintento
    manda dos veces la misma fila, el servidor la reconoce y no la duplica. Se
    genera AQUÍ, al registrar, porque un id creado en el momento del envío no
    distinguiría un reintento de un evento nuevo."""
    return uuid.uuid4().hex


# Cada cuánto la app confirma que sigue abierta. Es un compromiso: más corto da
# mejor resolución del tiempo de uso, pero escribe más seguido y, sobre todo,
# infla lo que habrá que subir al microservicio. A 5 minutos, una sesión de ocho
# horas cuesta UNA fila (el latido ACTUALIZA la de la sesión, no agrega otra) y el
# error máximo al medir cuándo se cerró la app es de 5 minutos.
INTERVALO_LATIDO_SEG = 300

_FORMATO_UTC = "%Y-%m-%dT%H:%M:%SZ"


def ahora_utc() -> str:
    """Instante del evento en UTC, ISO-8601 con 'Z'.

    Se guarda en UTC además de la fecha local que ya tiene la tabla: la local
    sirve para leer la bitácora aquí, pero al concentrar varios equipos —y al
    cruzar el cambio de horario— solo el UTC ordena y agrupa sin ambigüedad.
    """
    return datetime.now(timezone.utc).strftime(_FORMATO_UTC)


def minutos_desde(ts_utc: str) -> int:
    """Minutos transcurridos desde un instante con el formato de `ahora_utc()`.

    Devuelve 0 ante cualquier valor que no se pueda leer: el tiempo de uso es un
    dato informativo, y un registro corrupto no puede reventar el latido que lo
    está actualizando. Nunca negativo —un reloj que se atrasó (o el cambio de
    horario en una máquina mal configurada) daría una sesión de duración negativa,
    que no significa nada.
    """
    try:
        inicio = datetime.strptime(ts_utc, _FORMATO_UTC).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return 0
    return max(0, int((datetime.now(timezone.utc) - inicio).total_seconds() // 60))
