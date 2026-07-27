"""Descarga y caché del catálogo de EMPLEADOS del SIPP.

Para dar de alta un activo, el SIPP exige el EMPLEADO de resguardo, que se elige
por el modal "Buscar Empleado". Igual que con los insumos, la herramienta descarga
ese catálogo y lo guarda localmente para búsqueda instantánea; el RPA usa el ID
exacto del empleado elegido.

Endpoint (mismo proxy que el modal de empleados, descubierto en vivo):

    POST /componentes/cfproxy.cfc?method=proxy
    {"component":"ActivosFijosNuevo","execMethod":"listarEmpleadosActivos",
     "argumentcollection":{"id_Empleado":"","nb_empleado":""}}

Con filtro vacío devuelve TODO el catálogo en una sola llamada (no pagina). El
catálogo es GLOBAL (todos los empleados del grupo), no por empresa.
"""

from __future__ import annotations

import json
from datetime import datetime

from . import db

_RUTA_PROXY = "/componentes/cfproxy.cfc?method=proxy"

_COLS = {
    "id_empleado": "ID_EMPLEADO",
    "nombre": "NB_EMPLEADO",
    "puesto": "NB_PUESTO",
    "email": "DE_EMAIL",
}


class ErrorEmpleados(Exception):
    """Falla al descargar el catálogo de empleados del SIPP."""


async def descargar_catalogo(sesion) -> dict:
    """Descarga TODO el catálogo de empleados con la sesión `sesion` (SesionSipp ya
    logueada) y lo guarda en la caché local. Devuelve {guardados}."""
    url = sesion.BASE_URL + _RUTA_PROXY
    payload = json.dumps({
        "component": "ActivosFijosNuevo", "execMethod": "listarEmpleadosActivos",
        "argumentcollection": {"id_Empleado": "", "nb_empleado": ""}})
    try:
        resp = await sesion.context.request.post(
            url, data=payload, headers={"Content-Type": "application/json"})
        datos = await resp.json()
    except Exception as exc:  # noqa: BLE001 — se reporta como ErrorEmpleados
        raise ErrorEmpleados(
            f"No se pudo consultar el catálogo de empleados: {exc}") from exc

    query = datos.get("QUERY", datos)
    cols = query.get("COLUMNS") or []
    filas = query.get("DATA") or []
    if not filas:
        raise ErrorEmpleados("El SIPP no devolvió empleados.")
    idx = {c: i for i, c in enumerate(cols)}
    registros = [
        {clave: f[idx[col]] if col in idx else None for clave, col in _COLS.items()}
        for f in filas]
    guardados = db.reemplazar_empleados(
        registros, actualizado_en=datetime.now().strftime("%Y-%m-%d %H:%M"))
    return {"guardados": guardados}
