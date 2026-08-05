"""Carta responsiva: listar los activos dados de alta de un empleado y generar la
carta (PDF, con folio del SIPP) vía el mismo cfproxy que el resto del módulo.

Flujo confirmado en el DOM del SIPP (app `appActivosNuevo`):
  - LISTAR   -> ActivosFijosNuevo.getActivosFijosPorEmpleado
        {id_Empresa, id_Sucursal, id_Empleado, id_GrupoCentroCosto, id_CentroCosto,
         id_Departamento, sn_Alta:1}  ->  QUERY (activos dados de alta del empleado).
  - GENERAR  -> ActivosFijosNuevo.cartaResponsiva {ActivosFijos:[id_ActivoFijo,…]}
        ->  {ISOK, MSG, JSON:{DE_DIRECTORIO, NB_ARCHIVO} | [ …lo mismo… ]}.
        El PDF se descarga de  downloadFile.cfm?d=<dir>&n=<archivo>&b=0&a=0  con la
        cookie de sesión.  El SIPP asigna el FOLIO al generar: **cada generación
        consume un folio real**, por eso solo se llama al confirmar el usuario.

La selección que espera el SIPP es un arreglo de ID_ACTIVOFIJO
(`$scope.SeleccionActivosFijos.push(row.ID_ACTIVOFIJO)`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

_RUTA_PROXY = "/componentes/cfproxy.cfc?method=proxy"


class ErrorCartaResponsiva(Exception):
    """Falla al listar activos o generar la carta responsiva."""


@dataclass
class ActivoCarta:
    """Un activo dado de alta del empleado, candidato a la carta responsiva."""

    id_activo: int
    nombre: str
    serie: str
    etiqueta: str
    departamento: str
    grupo_cc: str
    centro_cc: str
    empresa: str
    sucursal: str
    empleado: str


async def _invoke(sesion, component: str, metodo: str, args: dict) -> dict:
    """POST al cfproxy; devuelve el JSON completo del SIPP ({ISOK, MSG, QUERY/JSON})."""
    payload = json.dumps({"component": component, "execMethod": metodo,
                          "argumentcollection": args})
    try:
        resp = await sesion.context.request.post(
            sesion.BASE_URL + _RUTA_PROXY, data=payload,
            headers={"Content-Type": "application/json"})
        return await resp.json()
    except Exception as exc:  # noqa: BLE001 — se reporta como ErrorCartaResponsiva
        raise ErrorCartaResponsiva(
            f"No se pudo consultar {component}.{metodo}: {exc}") from exc


def _val(fila, idx: dict, col: str) -> str:
    i = idx.get(col)
    v = fila[i] if i is not None and i < len(fila) else None
    return "" if v is None else str(v)


async def listar_activos_empleado(sesion, id_empresa, id_empleado) -> list[ActivoCarta]:
    """Activos dados de alta (sn_Alta=1) del empleado en la empresa. Read-only."""
    datos = await _invoke(sesion, "ActivosFijosNuevo", "getActivosFijosPorEmpleado",
                          {"id_Empresa": id_empresa, "id_Sucursal": "",
                           "id_Empleado": id_empleado, "id_GrupoCentroCosto": "",
                           "id_CentroCosto": "", "id_Departamento": "", "sn_Alta": 1})
    if not datos.get("ISOK"):
        raise ErrorCartaResponsiva(datos.get("MSG") or "El SIPP rechazó la consulta.")
    query = datos.get("QUERY", {}) or {}
    idx = {c: i for i, c in enumerate(query.get("COLUMNS") or [])}
    activos: list[ActivoCarta] = []
    for f in query.get("DATA") or []:
        id_af = _val(f, idx, "ID_ACTIVOFIJO")
        if not id_af:
            continue
        activos.append(ActivoCarta(
            id_activo=int(id_af),
            nombre=_val(f, idx, "NB_ACTIVOFIJO"),
            serie=_val(f, idx, "DE_SERIEACTIVO"),
            etiqueta=_val(f, idx, "DE_ETIQUETA"),
            departamento=_val(f, idx, "NB_DEPARTAMENTO"),
            grupo_cc=_val(f, idx, "NB_GRUPOCENTROCOSTO"),
            centro_cc=_val(f, idx, "NB_CENTROCOSTO"),
            empresa=_val(f, idx, "NB_EMPRESA"),
            sucursal=_val(f, idx, "NB_SUCURSAL"),
            empleado=_val(f, idx, "NB_EMPLEADORESGUARDO")))
    return activos


async def generar_carta(sesion, ids_activo: list[int], carpeta_destino,
                        id_empresa=None, id_empleado=None,
                        id_sucursal="", id_grupo_centro_costo="") -> list[Path]:
    """Genera la carta responsiva de los activos y descarga el/los PDF a
    `carpeta_destino`. **Consume un folio del SIPP.** Devuelve las rutas escritas.

    El servidor exige `id_Empresa` (el JS del portal lo trae comentado, pero el
    backend lo pide); se envían también empleado/sucursal/grupo como el payload
    original. El SIPP puede devolver un solo archivo o una lista; se descargan todos.
    """
    if not ids_activo:
        raise ErrorCartaResponsiva("Selecciona al menos un activo fijo.")
    if not id_empresa:
        raise ErrorCartaResponsiva("Falta la empresa para generar la carta.")
    datos = await _invoke(sesion, "ActivosFijosNuevo", "cartaResponsiva",
                          {"ActivosFijos": list(ids_activo),
                           "id_Empresa": id_empresa,
                           "id_Sucursal": id_sucursal or "",
                           "id_Empleado": id_empleado or "",
                           "id_GrupoCentroCosto": id_grupo_centro_costo or ""})
    if not datos.get("ISOK"):
        raise ErrorCartaResponsiva(datos.get("MSG") or "El SIPP no generó la carta.")
    archivos = datos.get("JSON")
    if archivos is None:
        raise ErrorCartaResponsiva("El SIPP no devolvió el archivo de la carta.")
    if not isinstance(archivos, list):
        archivos = [archivos]

    carpeta = Path(carpeta_destino)
    carpeta.mkdir(parents=True, exist_ok=True)
    rutas: list[Path] = []
    for item in archivos:
        directorio = item.get("DE_DIRECTORIO", "")
        nombre = item.get("NB_ARCHIVO", "")
        if not nombre:
            continue
        url = (sesion.BASE_URL + "/downloadFile.cfm?d=" + quote(str(directorio))
               + "&n=" + quote(str(nombre)) + "&b=0&a=0")
        try:
            resp = await sesion.context.request.get(url)
            contenido = await resp.body()
        except Exception as exc:  # noqa: BLE001
            raise ErrorCartaResponsiva(
                f"No se pudo descargar la carta «{nombre}»: {exc}") from exc
        destino = carpeta / nombre
        destino.write_bytes(contenido)
        rutas.append(destino)
    if not rutas:
        raise ErrorCartaResponsiva("El SIPP no devolvió ningún archivo descargable.")
    return rutas
