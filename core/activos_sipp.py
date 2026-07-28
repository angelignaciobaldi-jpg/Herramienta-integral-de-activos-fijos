"""Descarga de los ACTIVOS del SIPP por empresa (para generar sus QR/etiquetas).

Trae los activos fijos de una empresa desde el mismo endpoint que usa el listado
del catálogo (descubierto en vivo) y los cachea localmente (core/db.py):

    POST /componentes/cfproxy.cfc?method=proxy
    {"component":"ActivosFijosNuevo","execMethod":"getListadoActivosFijos",
     "argumentcollection":{"id_Empresa":<id>, ...filtros vacíos...}}

Devuelve una fila por activo con su ETIQUETA (número de inventario) y datos. La
etiqueta es el ID que llevará el QR.

Nota: las COLUMNAS de la respuesta se mapean por NOMBRE de forma tolerante (el
entorno de pruebas está vacío, así que no se fijan índices rígidos).
"""

from __future__ import annotations

import json
from datetime import datetime

from . import db

_RUTA_PROXY = "/componentes/cfproxy.cfc?method=proxy"

# Argumentos del endpoint (todos los filtros vacíos = todos los activos de la empresa).
_ARG_BASE = {
    "id_Empresa": 0, "id_SucursalAsignado": "", "id_InsumoOrigen": "",
    "nb_NombreInsumo": "", "de_SerieActivo": "", "de_Etiqueta": "", "sn_Activo": 0,
    "id_GrupoCentroCosto": "", "id_Departamento": "", "id_EmpleadoResguardo": "",
    "id_TipoActivo": "", "id_SituacionActivo": "", "sn_Borrado": "", "sn_Registro": "",
    "no_economico": "",
}


class ErrorActivosSipp(Exception):
    """Falla al descargar los activos del SIPP."""


def _elegir_columna(cols: list[str], *claves: str) -> "int | None":
    """Índice de la primera columna cuyo nombre (mayúsculas) contenga alguna clave."""
    for clave in claves:
        for i, c in enumerate(cols):
            if clave in (c or "").upper():
                return i
    return None


async def descargar_activos(sesion, id_empresa: int, empresa_nombre: str = "") -> dict:
    """Descarga los activos de la empresa `id_empresa` con la sesión `sesion`
    (SesionSipp logueada) y los cachea. Devuelve {guardados, total}."""
    url = sesion.BASE_URL + _RUTA_PROXY
    arg = dict(_ARG_BASE, id_Empresa=id_empresa)
    payload = json.dumps({"component": "ActivosFijosNuevo",
                          "execMethod": "getListadoActivosFijos",
                          "argumentcollection": arg})
    try:
        resp = await sesion.context.request.post(
            url, data=payload, headers={"Content-Type": "application/json"})
        datos = await resp.json()
    except Exception as exc:  # noqa: BLE001 — se reporta como ErrorActivosSipp
        raise ErrorActivosSipp(f"No se pudieron consultar los activos: {exc}") from exc

    query = datos.get("QUERY", datos)
    cols = query.get("COLUMNS") or []
    filas = query.get("DATA") or []
    # Mapeo tolerante de columnas por nombre.
    i_etq = _elegir_columna(cols, "ETIQUETA", "DE_ETIQUETA", "NB_ETIQUETA")
    i_ins = _elegir_columna(cols, "NB_NOMBREINSUMO", "NOMBREINSUMO", "INSUMO")
    i_ser = _elegir_columna(cols, "DE_SERIEACTIVO", "SERIEACTIVO", "SERIE")
    i_ubi = _elegir_columna(cols, "UBICACION", "DE_UBICACION")
    i_emp = _elegir_columna(cols, "EMPLEADO", "RESGUARDO", "NB_EMPLEADO")
    i_nomemp = _elegir_columna(cols, "NB_EMPRESA")

    def val(fila, i):
        return fila[i] if i is not None and i < len(fila) else None

    registros = []
    nombre_final = empresa_nombre
    for f in filas:
        if i_nomemp is not None and not nombre_final:
            nombre_final = val(f, i_nomemp)
        registros.append({
            "etiqueta": str(val(f, i_etq) or "").strip(),
            "insumo": val(f, i_ins), "serie": val(f, i_ser),
            "ubicacion": val(f, i_ubi), "empleado": val(f, i_emp),
        })
    guardados = db.reemplazar_activos_sipp(
        id_empresa, nombre_final or empresa_nombre or "", registros,
        actualizado_en=datetime.now().strftime("%Y-%m-%d %H:%M"))
    return {"guardados": guardados, "total": len(filas)}
