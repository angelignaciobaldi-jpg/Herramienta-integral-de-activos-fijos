"""Descarga de catálogos del SIPP para el alta/modificación de activos.

Por empresa descarga, vía el mismo endpoint cfproxy que el resto del módulo:
  - DEPARTAMENTOS (por empresa)            -> Departamentos.listarCombo {}
  - GRUPOS de centro de costo (por SUCURSAL) -> gruposCentrosCostos.listarCombo
        {id_Empresa, id_Sucursal}
  - CENTROS de costo (por GRUPO)           -> CentrosCostos.
        listarComboCentroCostosActivosFijos {id_Empresa, id_Sucursal,
        id_grupoCentroCosto}
  - UBICACIONES de resguardo (por empresa) -> ActivosFijosNuevo.
        listarUbicacionesAF {id_Empresa}
  - SUB-UBICACIONES (por empresa)          -> ActivosFijosNuevo.
        listarSububicacionesAF {id_Empresa}

Grupo y centro dependen de la sucursal; se recorren las sucursales de la empresa
por argumento (sin cambiar la sesión). El grupo guarda su sucursal (nombre) para
poder casar con la del activo en el modal de captura.
"""

from __future__ import annotations

import json

from . import db

_RUTA = "/componentes/cfproxy.cfc?method=proxy"


class ErrorCatalogos(Exception):
    """Falla al descargar los catálogos del SIPP."""


async def _query(sesion, component: str, metodo: str, args: dict):
    """POST al cfproxy; devuelve (indice_columna, filas)."""
    payload = json.dumps({"component": component, "execMethod": metodo,
                          "argumentcollection": args})
    try:
        resp = await sesion.context.request.post(
            sesion.BASE_URL + _RUTA, data=payload,
            headers={"Content-Type": "application/json"})
        datos = await resp.json()
    except Exception as exc:  # noqa: BLE001 — se reporta como ErrorCatalogos
        raise ErrorCatalogos(f"No se pudo consultar {component}.{metodo}: {exc}") from exc
    query = datos.get("QUERY", {}) or {}
    cols = query.get("COLUMNS") or []
    filas = query.get("DATA") or []
    return {c: i for i, c in enumerate(cols)}, filas


def _val(fila, idx: dict, col: str):
    i = idx.get(col)
    return fila[i] if i is not None and i < len(fila) else None


async def descargar_catalogos(sesion, id_empresa: int, progreso=None,
                              mensaje=None) -> dict:
    """Descarga y cachea departamentos, grupos y centros de costo de `id_empresa`.
    `progreso(hechos, total)` y `mensaje(texto)`: callbacks opcionales. Devuelve
    {departamentos, grupos, centros}."""
    # Departamentos (por empresa).
    if mensaje:
        mensaje("Descargando departamentos…")
    idx, filas = await _query(sesion, "Departamentos", "listarCombo", {})
    deptos = [{"id_departamento": _val(f, idx, "ID_DEPARTAMENTO"),
               "nb_departamento": _val(f, idx, "NB_DEPARTAMENTO")} for f in filas]
    db.reemplazar_departamentos(id_empresa, deptos)

    # Sucursales de la empresa (grupo/centro son por sucursal).
    if mensaje:
        mensaje("Descargando centros de costo…")
    idx, filas = await _query(sesion, "sucursales",
                              "listarSucursalesPorEmpleado", {"id_Empresa": id_empresa})
    sucursales = [(_val(f, idx, "ID_SUCURSAL"), _val(f, idx, "NB_SUCURSAL"))
                  for f in filas if _val(f, idx, "ID_SUCURSAL") is not None]
    db.reemplazar_sucursales_sipp(
        id_empresa, [{"id_sucursal": sid, "nb_sucursal": nb} for sid, nb in sucursales])

    grupos, centros = [], []
    total = len(sucursales)
    for n, (id_suc, nb_suc) in enumerate(sucursales, 1):
        idxg, filasg = await _query(
            sesion, "gruposCentrosCostos", "listarCombo",
            {"id_Empresa": id_empresa, "id_Sucursal": id_suc})
        for g in filasg:
            gid = _val(g, idxg, "ID_GRUPOCENTROCOSTO")
            if gid is None:
                continue
            grupos.append({"id_grupo": gid,
                           "nb_grupo": _val(g, idxg, "NB_GRUPOCENTROCOSTO"),
                           "id_sucursal": id_suc, "sucursal": nb_suc})
            idxc, filasc = await _query(
                sesion, "CentrosCostos", "listarComboCentroCostosActivosFijos",
                {"id_Empresa": id_empresa, "id_Sucursal": id_suc,
                 "id_grupoCentroCosto": gid})
            for c in filasc:
                cid = _val(c, idxc, "ID_CENTROCOSTO")
                if cid is not None:
                    centros.append({"id_grupo": gid, "id_centro": cid,
                                    "nb_centro": _val(c, idxc, "NB_CENTROCOSTO")})
        if progreso:
            progreso(n, total)

    db.reemplazar_grupos_cc(id_empresa, grupos)
    db.reemplazar_centros_cc(id_empresa, centros)

    # Ubicaciones de resguardo: la empresa entera en DOS llamadas, sin recorrer
    # sucursales. El portal las pide con {id_Empresa, id_Sucursal} desde el alta
    # y SIN argumentos desde su módulo de catálogo (donde devuelve todas), así
    # que el filtro es opcional. Se manda el de empresa Y además se filtra aquí
    # por ID_EMPRESA: si el servicio ignorara el argumento, guardaríamos las
    # ubicaciones de TODAS las empresas bajo esta, y el desplegable de la
    # plantilla ofrecería ubicaciones de otra razón social.
    if mensaje:
        mensaje("Descargando ubicaciones…")
    ubicaciones = await _listar_ubicaciones(sesion, id_empresa)
    db.reemplazar_ubicaciones(id_empresa, ubicaciones)
    ids_ubicacion = {u["id_ubicacion"] for u in ubicaciones}
    sububicaciones = await _listar_sububicaciones(sesion, id_empresa, ids_ubicacion)
    db.reemplazar_sububicaciones(id_empresa, sububicaciones)

    return {"departamentos": len(deptos), "sucursales": len(sucursales),
            "grupos": len(grupos), "centros": len(centros),
            "ubicaciones": len(ubicaciones),
            "sububicaciones": len(sububicaciones)}


def _activo(valor) -> bool:
    """Lee la bandera SN_ACTIVO como la lee el portal (`esActivo`): el servicio
    la devuelve indistintamente como booleano, 1 o '1'."""
    return valor in (True, 1, "1", "true", "True")


async def _listar_ubicaciones(sesion, id_empresa: int) -> list[dict]:
    """Ubicaciones de resguardo de una empresa (activas e inactivas)."""
    idx, filas = await _query(sesion, "ActivosFijosNuevo", "listarUbicacionesAF",
                              {"id_Empresa": id_empresa})
    salida = []
    for f in filas:
        if _val(f, idx, "ID_EMPRESA") not in (id_empresa, str(id_empresa)):
            continue
        id_ubic = _val(f, idx, "ID_UBICACIONACTIVOFIJO")
        if id_ubic is None:
            continue
        salida.append({"id_ubicacion": id_ubic,
                       "id_sucursal": _val(f, idx, "ID_SUCURSAL"),
                       "nb_ubicacion": _val(f, idx, "NB_UBICACION"),
                       "activo": _activo(_val(f, idx, "SN_ACTIVO"))})
    return salida


async def _listar_sububicaciones(sesion, id_empresa: int,
                                 ids_ubicacion: set) -> list[dict]:
    """Sub-ubicaciones de una empresa, acotadas a las ubicaciones ya guardadas.

    Se filtra por `ids_ubicacion` además de por empresa porque una sub-ubicación
    huérfana —de una ubicación que no quedó en el catálogo— no se podría ofrecer
    en ningún desplegable y solo ensuciaría la caché."""
    idx, filas = await _query(sesion, "ActivosFijosNuevo", "listarSububicacionesAF",
                              {"id_Empresa": id_empresa})
    salida = []
    for f in filas:
        id_ubic = _val(f, idx, "ID_UBICACIONACTIVOFIJO")
        id_sub = _val(f, idx, "ID_SUBUBICACIONACTIVOFIJO")
        if id_ubic not in ids_ubicacion or id_sub is None:
            continue
        salida.append({"id_ubicacion": id_ubic, "id_sububicacion": id_sub,
                       "nb_sububicacion": _val(f, idx, "NB_SUBUBICACION"),
                       "activo": _activo(_val(f, idx, "SN_ACTIVO"))})
    return salida
