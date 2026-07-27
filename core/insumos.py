"""Descarga y caché del catálogo de INSUMOS del SIPP.

Los nombres del levantamiento no coinciden con el catálogo del SIPP, así que el
usuario debe elegir el insumo REAL. Para eso la herramienta descarga el catálogo
de insumos del SIPP y lo guarda localmente (core/db.py, tabla insumos_sipp); la
búsqueda es instantánea y offline, y el RPA usa el ID exacto del insumo elegido.

El catálogo se obtiene del MISMO endpoint que usa el modal "Buscar Insumo" del
portal (descubierto en vivo):

    POST /componentes/cfproxy.cfc?method=proxy
    {"component":"insumos","execMethod":"listar",
     "argumentcollection":{"nb_insumo":"","id_insumo":"","id_subfamilia":"",
                           "sn_activo":"","page":N,"pageSize":M}}

Se pagina (el server rechaza páginas gigantes) con las cookies de una sesión
activa (SesionSipp ya logueada y con empresa seleccionada). El catálogo es POR
EMPRESA: cada fila trae ID_EMPRESA/NB_EMPRESA.
"""

from __future__ import annotations

import json
from datetime import datetime

from . import db

# Ruta del proxy (relativa a la BASE_URL de la sesión).
_RUTA_PROXY = "/componentes/cfproxy.cfc?method=proxy"
# Tamaño de página probado como estable (el server devuelve vacío si es enorme).
_PAGE_SIZE = 5000
# Tope de seguridad de páginas (33k insumos / 5k ≈ 7 páginas; 40 es margen de sobra).
_MAX_PAGINAS = 40

# Columnas de la respuesta que nos interesan (nombre en el QUERY del SIPP).
_COLS = {
    "id_insumo": "ID_INSUMO",
    "empresa_id": "ID_EMPRESA",
    "empresa_nombre": "NB_EMPRESA",
    "nombre": "NB_NOMBREINSUMO",
    "unidad": "NB_UNIDADMEDIDA",
    "familia": "NB_FAMILIAINSUMO",
    "subfamilia": "NB_SUBFAMILIAINSUMO",
    "activo_fijo": "SN_ACTIVOFIJO",
    "seriado": "SN_INSUMOSERIADO",
}


class ErrorInsumos(Exception):
    """Falla al descargar el catálogo de insumos del SIPP."""


def _payload(page: int, size: int) -> str:
    return json.dumps({
        "component": "insumos", "execMethod": "listar",
        "argumentcollection": {
            "nb_insumo": "", "id_insumo": "", "id_subfamilia": "",
            "sn_activo": "", "page": page, "pageSize": size},
    })


async def descargar_catalogo(sesion, progreso=None, solo_activo_fijo: bool = True) -> dict:
    """Descarga el catálogo de insumos con la sesión `sesion` (SesionSipp ya
    logueada y con empresa seleccionada) y lo guarda en la caché local.

    `progreso(hechos, total)`: callback opcional durante la paginación.
    `solo_activo_fijo`: si True (por defecto), solo se guardan los insumos marcados
    como activo fijo (que es lo relevante para este módulo).

    Devuelve {empresa_id, empresa_nombre, guardados, total}. Lanza ErrorInsumos
    ante fallos de red/respuesta.
    """
    url = sesion.BASE_URL + _RUTA_PROXY
    registros: list[dict] = []
    empresa_id = empresa_nombre = None
    total = None

    for page in range(1, _MAX_PAGINAS + 1):
        try:
            resp = await sesion.context.request.post(
                url, data=_payload(page, _PAGE_SIZE),
                headers={"Content-Type": "application/json"})
            datos = await resp.json()
        except Exception as exc:  # noqa: BLE001 — se reporta como ErrorInsumos
            raise ErrorInsumos(f"No se pudo consultar el catálogo de insumos: {exc}") from exc

        query = datos.get("QUERY", datos)
        cols = query.get("COLUMNS") or []
        filas = query.get("DATA") or []
        if not filas:
            break
        idx = {c: i for i, c in enumerate(cols)}
        if total is None and "TOTAL" in idx and filas:
            total = filas[0][idx["TOTAL"]]
        for f in filas:
            r = {clave: f[idx[col]] if col in idx else None for clave, col in _COLS.items()}
            if empresa_id is None and r["empresa_id"] is not None:
                empresa_id, empresa_nombre = r["empresa_id"], r["empresa_nombre"]
            if solo_activo_fijo and not r["activo_fijo"]:
                continue
            registros.append(r)
        if progreso:
            progreso(min(page * _PAGE_SIZE, total or 0), total or 0)
        if total is not None and page * _PAGE_SIZE >= total:
            break

    if empresa_id is None:
        raise ErrorInsumos(
            "El SIPP no devolvió insumos. ¿La sesión tiene empresa seleccionada?")

    guardados = db.reemplazar_insumos(
        empresa_id, empresa_nombre or "", registros,
        actualizado_en=datetime.now().strftime("%Y-%m-%d %H:%M"))
    return {"empresa_id": empresa_id, "empresa_nombre": empresa_nombre,
            "guardados": guardados, "total": total}
