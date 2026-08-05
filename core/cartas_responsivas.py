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
    ubicacion: str
    departamento: str
    grupo_cc: str
    centro_cc: str
    empresa: str
    sucursal: str
    id_empleado: "int | None"
    empleado: str
    id_empresa: "int | None"
    fecha: "object | None" = None   # FH_MOVIMIENTO (fecha de registro), datetime.date


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


def _entier(texto):
    t = str(texto or "").strip()
    return int(t) if t.isdigit() else None


def _fecha(texto):
    """Parsea la fecha del SIPP ('DD/MM/AAAA' o ISO) a date; None si no se puede."""
    from datetime import datetime
    t = (texto or "").strip()
    if not t:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(t[:10], fmt).date()
        except ValueError:
            continue
    return None


def _activo_desde_listado(f, idx: dict) -> "ActivoCarta | None":
    id_af = _val(f, idx, "ID_ACTIVOFIJO")
    if not id_af:
        return None
    return ActivoCarta(
        id_activo=int(id_af),
        nombre=_val(f, idx, "NB_ACTIVOFIJO"),
        serie=_val(f, idx, "DE_SERIEACTIVO"),
        etiqueta=_val(f, idx, "DE_ETIQUETA"),
        ubicacion=_val(f, idx, "NB_UBICACION"),
        departamento=_val(f, idx, "NB_DEPARTAMENTO"),
        grupo_cc=_val(f, idx, "NB_GRUPOCENTROCOSTO"),
        centro_cc=_val(f, idx, "NB_CENTROCOSTO"),
        empresa=_val(f, idx, "NB_EMPRESA"),
        sucursal=_val(f, idx, "NB_SUCURSAL"),
        id_empleado=_entier(_val(f, idx, "ID_EMPLEADORESGUARDO")),
        empleado=_val(f, idx, "NB_EMPLEADORESGUARDO"),
        id_empresa=_entier(_val(f, idx, "ID_EMPRESA")),
        fecha=_fecha(_val(f, idx, "FH_MOVIMIENTO")))


async def listar_activos_empresa(sesion, id_empresa, fh_inicio=None, fh_fin=None,
                                 id_empleado=None) -> list[ActivoCarta]:
    """Activos dados de alta de la empresa (getListadoActivosFijos, sn_Registro=1),
    opcionalmente filtrados por rango de fecha de REGISTRO (FH_MOVIMIENTO) y/o por
    empleado. Trae empleado, empresa, ubicación y fecha (para agrupar y filtrar)."""
    datos = await _invoke(sesion, "ActivosFijosNuevo", "getListadoActivosFijos",
                          {"id_Empresa": id_empresa, "sn_Registro": 1})
    if not datos.get("ISOK"):
        raise ErrorCartaResponsiva(datos.get("MSG") or "El SIPP rechazó la consulta.")
    query = datos.get("QUERY", {}) or {}
    idx = {c: i for i, c in enumerate(query.get("COLUMNS") or [])}
    activos: list[ActivoCarta] = []
    for f in query.get("DATA") or []:
        a = _activo_desde_listado(f, idx)
        if a is None:
            continue
        if id_empleado is not None and a.id_empleado != id_empleado:
            continue
        if fh_inicio and (a.fecha is None or a.fecha < fh_inicio):
            continue
        if fh_fin and (a.fecha is None or a.fecha > fh_fin):
            continue
        activos.append(a)
    return activos


async def listar_activos_empleado(sesion, id_empresa, id_empleado,
                                  fh_inicio=None, fh_fin=None) -> list[ActivoCarta]:
    """Activos dados de alta del empleado en la empresa (con filtro de fecha opcional)."""
    return await listar_activos_empresa(sesion, id_empresa, fh_inicio, fh_fin,
                                        id_empleado=id_empleado)


async def generar_carta(sesion, ids_activo: list[int], carpeta_destino,
                        id_empresa=None, id_empleado=None, etiquetas=None,
                        id_sucursal="", id_grupo_centro_costo="") -> list[Path]:
    """Genera la carta responsiva de los activos y descarga el/los PDF a
    `carpeta_destino`. **Consume un folio del SIPP.** Devuelve las rutas escritas.

    OJO: el JS del portal envía `ActivosFijos` como ARRAY, pero el backend
    `cartaResponsiva` exige `activosFijos` como STRING (y `id_Empresa`); por eso el
    botón del propio SIPP falla ("Cannot cast Array to string"). Aquí se envía el
    payload que el backend espera (activosFijos string + ar_ActivosFijos array +
    etiquetas), para intentar sortear ese bug del portal.
    """
    if not ids_activo:
        raise ErrorCartaResponsiva("Selecciona al menos un activo fijo.")
    if not id_empresa:
        raise ErrorCartaResponsiva("Falta la empresa para generar la carta.")
    ids = [str(i) for i in ids_activo]
    etqs = [str(e) for e in (etiquetas or []) if str(e).strip()]
    # El backend declara DOS argumentos distintos: ActivosFijos (ARRAY, 1er arg) y
    # activosFijos (STRING, 6º arg). El JS del portal solo manda el array, por eso
    # su propio botón falla. Se envían AMBOS con su nombre/tipo exacto.
    datos = await _invoke(sesion, "ActivosFijosNuevo", "cartaResponsiva",
                          {"ActivosFijos": list(ids_activo),   # array (1er arg)
                           "activosFijos": ",".join(ids),       # string (6º arg)
                           "ar_ActivosFijos": list(ids_activo),
                           "activosFijosEtiquetas": ",".join(etqs),
                           "id_Empresa": id_empresa,
                           "id_Sucursal": id_sucursal or "",
                           "id_Empleado": id_empleado or "",
                           "id_GrupoCentroCosto": id_grupo_centro_costo or "",
                           "id_ActivoFijo": ""})
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
