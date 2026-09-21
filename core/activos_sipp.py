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


def _bajar_listado_api(id_empresa: int, progreso=None) -> tuple[list[dict], int, str]:
    """(registros, total, nombre de la empresa) del listado por API. Solo red.

    Separado de la fusión para que el barrido de todas las empresas pueda bajar
    en paralelo y escribir en la base en un solo hilo: SQLite no admite varios
    escritores a la vez y respondería «database is locked»."""
    from . import api

    registros: list[dict] = []
    total = 0
    nombre = ""
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
        if not nombre:
            nombre = (filas[0].get("empresa") or "").strip()
        registros.extend(_fila_api(f) for f in filas)
        if callable(progreso):
            progreso(len(registros), total or len(registros))
        if len(filas) < _TAM_PAGINA or (total and len(registros) >= total):
            break
    return registros, total, nombre


def _candidatos_repetidos(registros: list[dict]) -> dict:
    """{etiqueta -> activos que la comparten}, solo de las repetidas.

    El SIPP permite que DOS activos distintos compartan número de inventario, y
    la caché guarda uno por etiqueta (es su clave), así que uno tapa al otro. No
    se corrige aquí —es dato de origen— pero se reporta CON SUS CANDIDATOS: la
    etiqueta es con lo que el RPA localiza el activo al modificar, así que hay que
    poder enseñarle al usuario entre qué insumos está la ambigüedad."""
    por_etiqueta: dict[str, list[dict]] = {}
    for r in registros:
        por_etiqueta.setdefault(r["etiqueta"], []).append(r)
    return {e: filas for e, filas in por_etiqueta.items() if len(filas) > 1}


def descargar_activos_api(id_empresa: int, empresa_nombre: str = "",
                          progreso=None) -> dict:
    """Descarga por HTTP el listado de activos de una empresa y lo FUSIONA en la
    caché. No abre navegador ni inicia sesión en el portal.

    `id_empresa` va tal cual al parámetro `empresa` del endpoint, que espera el ID
    numérico (por nombre responde 500). `progreso(traidos, total)` es opcional.

    NUNCA borra lo que la API no trae, aunque el listado venga completo. La API y
    el portal NO asignan los activos a la misma empresa: un monitor resguardado en
    Aske y comprado por Abastecedora el portal lo lista en Aske y la API en
    Abastecedora. Con el borrado activado, cada «Buscar en SIPP» refrescaba Aske
    por API y eliminaba de su caché los ~850 activos que la API archiva en otra
    empresa —y esos activos, que SÍ están en el SIPP, pasaban a «no dado de alta»,
    listos para que el RPA los duplicara—. Un activo que ya no existe y sigue en
    caché es un problema menor; uno que existe y desaparece de ella, no.

    Devuelve {guardados, nuevos, eliminados, total, origen, duplicadas, candidatos}.
    """
    registros, total, nombre = _bajar_listado_api(id_empresa, progreso)
    candidatos = _candidatos_repetidos(registros)
    sello = datetime.now().strftime("%Y-%m-%d %H:%M")
    res = db.fusionar_activos_sipp(id_empresa, nombre or empresa_nombre,
                                   registros, sello, eliminar_ausentes=False)
    return {**res, "total": total or len(registros), "origen": "api",
            "duplicadas": sorted(candidatos), "candidatos": candidatos}


# Filas por página del barrido SIN filtro de empresa. Con 10 mil son 7 páginas
# para todo el catálogo (~65 mil activos) y cada una tarda ~2-6 s: holgado frente
# al timeout de 30 s de core/api. Más grandes no ganan tiempo y sí arriesgan.
_TAM_PAGINA_TODO = 10_000


def _normalizar_empresa(nombre: str) -> str:
    """Nombre comparable: la API manda «SERVICIOS  EDUCATIVOS IMAA» con doble
    espacio, y sin esto sus activos se quedarían fuera de la caché."""
    return " ".join((nombre or "").split()).upper()


def refrescar_todas_api(progreso=None, hilos: int = 6) -> dict:
    """Trae por API el catálogo COMPLETO, sin filtro de empresa, y lo fusiona.

    Es lo que hace que la búsqueda en el SIPP encuentre un activo aunque la API lo
    archive en una empresa distinta de la del levantamiento (ver
    `descargar_activos_api`).

    Una sola consulta sin el parámetro `empresa`: el endpoint devuelve entonces
    todo el catálogo, paginado, con la empresa de cada fila. Antes se hacían 58
    consultas —una por empresa— (~15 s), y antes aún el respaldo por el portal
    consultaba etiqueta por etiqueta con un navegador (minutos). Así son ~6 s: la
    primera página da el total y el resto se pide en paralelo. La base se escribe
    en un solo hilo (SQLite no admite varios escritores).

    `progreso(hechas, total)` cuenta páginas y se llama desde el hilo que llama.
    Devuelve {empresas, activos, completo, errores: [motivo], sin_empresa}.
    `completo` es False si alguna página falló o faltaron filas: con eso el
    llamador sabe que todavía le conviene el respaldo por el portal.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from . import api
    from .empresas import ID_POR_EMPRESA

    def pagina(n: int) -> list[dict]:
        try:
            resp = api.solicitar(_RUTA_API, params={
                "page": n, "pageSize": _TAM_PAGINA_TODO})
        except api.ErrorAPI as exc:
            raise ErrorActivosSipp(str(exc)) from exc
        return resp.get("data") or []

    primera = pagina(1)
    if not primera:
        return {"empresas": 0, "activos": 0, "completo": False,
                "errores": ["la API no devolvió activos"], "sin_empresa": 0}
    try:
        total = int(primera[0].get("total") or 0)
    except (TypeError, ValueError):
        total = 0
    paginas = max(1, -(-total // _TAM_PAGINA_TODO)) if total else 1
    filas, errores = list(primera), []
    if callable(progreso):
        progreso(1, paginas)
    if paginas > 1:
        with ThreadPoolExecutor(max_workers=hilos) as ex:
            futuros = [ex.submit(pagina, n) for n in range(2, paginas + 1)]
            for hechas, fut in enumerate(as_completed(futuros), 2):
                try:
                    filas.extend(fut.result())
                except Exception as exc:  # noqa: BLE001 — se reporta, sigue
                    errores.append(str(exc))
                if callable(progreso):
                    progreso(hechas, paginas)

    ids = {_normalizar_empresa(n): i for n, i in ID_POR_EMPRESA.items()}
    por_empresa: dict[int, tuple[str, list[dict]]] = {}
    sin_empresa = 0
    for f in filas:
        nombre = (f.get("empresa") or "").strip()
        idemp = ids.get(_normalizar_empresa(nombre))
        if idemp is None:
            sin_empresa += 1      # empresa que la herramienta no conoce
            continue
        por_empresa.setdefault(idemp, (nombre, []))[1].append(_fila_api(f))

    sello = datetime.now().strftime("%Y-%m-%d %H:%M")
    activos = 0
    for idemp, (nombre, registros) in por_empresa.items():
        db.fusionar_activos_sipp(idemp, nombre, registros, sello,
                                 eliminar_ausentes=False)
        activos += len(registros)
    completo = not errores and (not total or len(filas) >= total)
    return {"empresas": len(por_empresa), "activos": activos,
            "completo": completo, "errores": errores, "sin_empresa": sin_empresa}


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
