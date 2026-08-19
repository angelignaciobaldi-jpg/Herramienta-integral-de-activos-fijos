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

# Argumentos del endpoint. CLAVE: sn_Registro=1 (activos con registro finalizado);
# sin él el endpoint devuelve 0. Los demás filtros van vacíos = todos los activos
# de la empresa. (Mismos campos que envía la grid real del portal.)
_ARG_BASE = {
    "id_Empresa": 0, "id_SucursalAsignado": "", "id_InsumoOrigen": "",
    "nb_NombreInsumo": "", "de_SerieActivo": "", "de_Etiqueta": "", "sn_Activo": "",
    "id_GrupoCentroCosto": "", "id_Departamento": "", "id_EmpleadoResguardo": "",
    "id_CentroCosto": "", "id_TipoActivo": "", "id_SituacionActivo": "",
    "sn_Registro": 1, "fh_Inicio": "", "fh_Fin": "", "no_economico": "",
}


class ErrorActivosSipp(Exception):
    """Falla al descargar los activos del SIPP."""


# ------------------------------------------------ vía API REST (sin navegador)
# Endpoint propio del listado. A diferencia del cfproxy de arriba NO necesita
# navegador ni sesión: basta la URL base y el token de core/ajustes_api.
_RUTA_API = "/api/activos-fijos/listado"
# Filas por página. El servicio aceptó 5000; se pide menos para que un corte de
# red no tire una descarga grande y para no cargar de golpe 60 mil registros.
_TAM_PAGINA = 2_000
# Tope de páginas: red de seguridad si `total` viniera mal y el bucle no cerrara.
_MAX_PAGINAS = 200
# El servicio marca "sin empleado" con esta leyenda. Guardarla tal cual haría que
# la comparación viera una diferencia contra un Excel que simplemente va vacío.
_SIN_EMPLEADO = "sin empleado asignado"


def hay_api() -> bool:
    """¿Está configurada la API (URL base + token)? Si no, se usa el portal."""
    from . import ajustes_api
    return bool(ajustes_api.base_url() and ajustes_api.token())


def _fila_api(r: dict) -> dict:
    """Traduce un registro de la API al formato de la caché.

    Solo se mapea lo que el endpoint entrega HOY (etiqueta, insumo, serie,
    empleado, sucursal). Ubicación, departamento, tipo y el detalle (situación,
    costo, centros de costo, fechas) no vienen en él: se dejan FUERA del dict a
    propósito, para que `db.fusionar_activos_sipp` conserve lo que ya hubiera
    descargado el portal en vez de vaciarlo."""
    empleado = (r.get("empleado_resguardo") or "").strip()
    if empleado.lower() == _SIN_EMPLEADO:
        empleado = ""
    return {
        "etiqueta": str(r.get("etiqueta") or "").strip(),
        "insumo": (r.get("nombre") or "").strip(),
        "serie": (r.get("serie") or "").strip(),
        "empleado": empleado,
        "sucursal": (r.get("sucursal") or "").strip(),
    }


def descargar_activos_api(id_empresa: int, empresa_nombre: str = "",
                          progreso=None) -> dict:
    """Descarga por HTTP el listado de activos de una empresa y lo FUSIONA en la
    caché. No abre navegador ni inicia sesión en el portal.

    `id_empresa` va tal cual al parámetro `empresa` del endpoint, que espera el ID
    numérico (por nombre responde 500). `progreso(traidos, total)` es opcional.

    Devuelve {guardados, nuevos, eliminados, total, origen, duplicadas, candidatos}.
    `candidatos` es {etiqueta -> [activos que la comparten]} y solo trae las
    repetidas; sirve para preguntarle al usuario cuál es el suyo antes de que el
    RPA edite a ciegas la primera coincidencia.
    """
    from . import api

    registros: list[dict] = []
    total = 0
    nombre_final = empresa_nombre
    for pagina in range(1, _MAX_PAGINAS + 1):
        try:
            resp = api.solicitar(_RUTA_API, params={
                "empresa": id_empresa, "page": pagina, "pageSize": _TAM_PAGINA})
        except api.ErrorAPI as exc:
            raise ErrorActivosSipp(str(exc)) from exc
        filas = resp.get("data") or []
        if not filas:
            break
        # El total viaja DENTRO de cada fila (no en la raíz de la respuesta).
        try:
            total = int(filas[0].get("total") or 0)
        except (TypeError, ValueError):
            total = 0
        if not nombre_final:
            nombre_final = (filas[0].get("empresa") or "").strip()
        registros.extend(_fila_api(f) for f in filas)
        if callable(progreso):
            progreso(len(registros), total or len(registros))
        if len(filas) < _TAM_PAGINA or (total and len(registros) >= total):
            break

    # Etiquetas repetidas: el SIPP permite que DOS activos distintos compartan
    # número de inventario, y la caché guarda uno por etiqueta (es su clave), así
    # que uno tapa al otro. No se corrige aquí —es dato de origen— pero se reporta
    # CON SUS CANDIDATOS: la etiqueta es con lo que el RPA localiza el activo al
    # modificar, así que hay que poder enseñarle al usuario entre qué insumos está
    # la ambigüedad antes de tocar nada.
    por_etiqueta: dict[str, list[dict]] = {}
    for r in registros:
        por_etiqueta.setdefault(r["etiqueta"], []).append(r)
    candidatos = {e: filas for e, filas in por_etiqueta.items() if len(filas) > 1}

    sello = datetime.now().strftime("%Y-%m-%d %H:%M")
    # `eliminar_ausentes` solo si de verdad se trajo el listado COMPLETO: con una
    # descarga a medias (red caída a la tercera página) borraría activos buenos.
    completo = bool(registros) and (not total or len(registros) >= total)
    res = db.fusionar_activos_sipp(id_empresa, nombre_final or empresa_nombre,
                                   registros, sello,
                                   eliminar_ausentes=completo)
    return {**res, "total": total or len(registros), "origen": "api",
            "duplicadas": sorted(candidatos), "candidatos": candidatos}


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
    # Mapeo por los nombres REALES de columna del endpoint (confirmados en vivo).
    # OJO: no usar "INSUMO" a secas (haría match con ID_INSUMO, un id numérico).
    i_etq = _elegir_columna(cols, "DE_ETIQUETA")
    i_ins = _elegir_columna(cols, "NB_ACTIVOFIJO", "DE_DESCRIPCION")
    i_ser = _elegir_columna(cols, "DE_SERIEACTIVO")
    i_ubi = _elegir_columna(cols, "NB_UBICACION")
    i_emp = _elegir_columna(cols, "NB_EMPLEADORESGUARDO")
    i_suc = _elegir_columna(cols, "NB_SUCURSAL")
    i_dep = _elegir_columna(cols, "NB_DEPARTAMENTO")
    i_nomemp = _elegir_columna(cols, "NB_EMPRESA")
    # Tipo de activo del SIPP: su id (coincide con core.tipos_activo.TIPOS_ACTIVO)
    # y su nombre, para preseleccionarlo en la captura de los dados de alta.
    i_idtipo = _elegir_columna(cols, "ID_TIPOACTIVOFIJO")
    i_tipo = _elegir_columna(cols, "NB_TIPOACTIVOFIJO")
    # Campos EXTRA del activo (para registrar el detalle del insumo de los dados de
    # alta): descripción, situación, costo, grupo/centro de costo, fechas e ids.
    i_desc = _elegir_columna(cols, "DE_DESCRIPCION")
    i_sit = _elegir_columna(cols, "NB_SITUACIONACTIVOFIJO")
    i_costo = _elegir_columna(cols, "IM_COSTO")
    i_gcc = _elegir_columna(cols, "NB_GRUPOCENTROCOSTO")
    i_cc = _elegir_columna(cols, "NB_CENTROCOSTO")
    i_fadq = _elegir_columna(cols, "FH_ADQUISICION")
    i_fgar = _elegir_columna(cols, "FH_GARANTIA")
    i_fasig = _elegir_columna(cols, "FH_ASIGNACION")
    i_idemp_res = _elegir_columna(cols, "ID_EMPLEADORESGUARDO")
    i_idins = _elegir_columna(cols, "ID_INSUMOORIGEN")

    def val(fila, i):
        return fila[i] if i is not None and i < len(fila) else None

    def fecha(fila, i):
        """Convierte una fecha ISO del SIPP ('2026-01-20T00:00:00') a DD/MM/AAAA."""
        s = str(val(fila, i) or "").strip()
        if len(s) >= 10 and s[4] == "-":
            try:
                return datetime.strptime(s[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
            except ValueError:
                return ""
        return s

    registros = []
    nombre_final = empresa_nombre
    for f in filas:
        if i_nomemp is not None and not nombre_final:
            nombre_final = val(f, i_nomemp)
        registros.append({
            "etiqueta": str(val(f, i_etq) or "").strip(),
            "insumo": val(f, i_ins), "serie": val(f, i_ser),
            "ubicacion": val(f, i_ubi), "empleado": val(f, i_emp),
            "sucursal": val(f, i_suc), "departamento": val(f, i_dep),
            "id_tipo": val(f, i_idtipo), "tipo": val(f, i_tipo),
            # Extra (se guarda como JSON en la caché; ver core/db.reemplazar_activos_sipp).
            "extra": {
                "descripcion": val(f, i_desc),
                "situacion": val(f, i_sit),
                "costo": val(f, i_costo),
                "grupo_centro_costo": val(f, i_gcc),
                "centro_costo": val(f, i_cc),
                "fecha_adquisicion": fecha(f, i_fadq),
                "fecha_garantia": fecha(f, i_fgar),
                "fecha_asignacion": fecha(f, i_fasig),
                "id_empleado_resguardo": val(f, i_idemp_res),
                "id_insumo_origen": val(f, i_idins),
            },
        })
    guardados = db.reemplazar_activos_sipp(
        id_empresa, nombre_final or empresa_nombre or "", registros,
        actualizado_en=datetime.now().strftime("%Y-%m-%d %H:%M"))
    return {"guardados": guardados, "total": len(filas)}
