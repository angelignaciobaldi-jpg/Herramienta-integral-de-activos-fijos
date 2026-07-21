"""Cliente HTTP mínimo para los microservicios (opcional).

Envuelve las peticiones a la API de la plataforma resolviendo la URL base y el
token desde core/ajustes_api.py (preferencia local -> variable de entorno). Usa
solo la librería estándar (urllib) para no añadir dependencias al empaquetado.

Este módulo es un ESQUELETO: agrega aquí los endpoints concretos que consuma la
herramienta de Activos Fijos (p. ej. catálogos, validaciones, sincronización).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from core import ajustes_api

TIMEOUT = 30


class ErrorAPI(Exception):
    """Falla al llamar a un microservicio (red, autenticación, respuesta no OK)."""


def _headers() -> dict[str, str]:
    cabeceras = {"Accept": "application/json", "Content-Type": "application/json"}
    tok = ajustes_api.token()
    if tok:
        cabeceras["Authorization"] = f"Bearer {tok}"
    return cabeceras


def _url(ruta: str) -> str:
    base = ajustes_api.base_url(requerido=True)
    return f"{base}/{ruta.lstrip('/')}"


def solicitar(ruta: str, metodo: str = "GET", cuerpo: dict | None = None) -> dict:
    """Hace una petición a `ruta` (relativa a la URL base) y devuelve el JSON.

    Lanza ErrorAPI ante fallos de red/HTTP. `cuerpo` (si se pasa) se envía como
    JSON en el body (para POST/PUT)."""
    datos = json.dumps(cuerpo).encode("utf-8") if cuerpo is not None else None
    req = urllib.request.Request(_url(ruta), data=datos, headers=_headers(), method=metodo)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            crudo = resp.read().decode("utf-8")
        return json.loads(crudo) if crudo else {}
    except urllib.error.HTTPError as exc:
        raise ErrorAPI(f"La API respondió {exc.code}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise ErrorAPI(f"No se pudo conectar con la API: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise ErrorAPI("La API devolvió una respuesta que no es JSON válido.") from exc
