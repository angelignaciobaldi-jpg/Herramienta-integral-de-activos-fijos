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


# --- Elegir el insumo del catálogo -----------------------------------------
def _norm(texto) -> str:
    """Mayúsculas, sin acentos ni espacios de más (para comparar nombres)."""
    import unicodedata

    t = unicodedata.normalize("NFD", str(texto or "").upper())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return " ".join(t.split())


def _clave(texto) -> str:
    """Clave de comparación: además de acentos, ignora la PUNTUACIÓN.

    El mismo insumo aparece en el catálogo como 'NO BREAK', 'NO. BREAK' o
    'Cámara IP': si la única diferencia con lo capturado son un punto, una coma o
    una tilde, es el mismo y debe registrarse sin preguntar."""
    import re

    return " ".join(re.sub(r"[^A-Z0-9 ]+", " ", _norm(texto)).split())


# Índice {clave -> (id, nombre)} de todo el catálogo. Se arma una vez y se
# reutiliza: la búsqueda por LIKE de SQLite es sensible a acentos y puntuación
# ('CAMARA' no encuentra 'Cámara'), así que la coincidencia exacta se resuelve
# aquí, en memoria, y no en el SQL.
_INDICE: dict = {"firma": None, "por_clave": {}}


def _indice_exacto() -> dict:
    from . import db

    firma = db.firma_catalogo_insumos()
    if _INDICE["firma"] != firma:
        por_clave: dict = {}
        for id_insumo, nombre in db.pares_insumos():
            clave = _clave(nombre)
            if not clave:
                continue
            # A igualdad de clave se conserva el id MÁS BAJO: el mismo insumo
            # suele estar dado de alta en varias empresas y el bajo es el original.
            previo = por_clave.get(clave)
            if previo is None or id_insumo < previo[0]:
                por_clave[clave] = (id_insumo, nombre)
        _INDICE["firma"], _INDICE["por_clave"] = firma, por_clave
    return _INDICE["por_clave"]


def resolver(nombre: str, id_empresa: "int | None" = None, limite: int = 200):
    """(insumo, exacto) para `nombre`.

    `exacto` dice si el catálogo tiene una entrada con ESE nombre. Solo entonces
    se puede registrar sin preguntar: cuando no la hay, la mejor candidata sigue
    siendo una variante concreta ('NVR 32 CANALES' para un 'NVR'), y elegirla por
    el usuario sería inventar un dato que después nadie detecta.
    """
    from . import db

    # 1) Coincidencia EXACTA tolerante (acentos y puntuación) contra todo el
    #    catálogo. Va por índice porque el LIKE de SQLite no la encuentra.
    exacto = _indice_exacto().get(_clave(nombre))
    if exacto is not None:
        # Se devuelve el Insumo completo (lo necesita quien muestre el nombre).
        ins = db.obtener_insumo(exacto[0])
        if ins is not None:
            return ins, True
    # 2) Sin exacta: la mejor candidata es solo una SUGERENCIA para el usuario.
    return elegir_mas_general(nombre, id_empresa, limite), False


def elegir_mas_general(nombre: str, id_empresa: "int | None" = None,
                       limite: int = 200):
    """Insumo del catálogo que representa a `nombre` con el MENOR detalle posible.

    El levantamiento nombra el activo en genérico ('NVR', 'NO BREAK') mientras que
    el catálogo del SIPP guarda decenas de variantes con marca, capacidad o
    modelo. Dar de alta con una variante concreta es un error de dato difícil de
    detectar después, así que se prefiere siempre la entrada más general.

    Orden de preferencia:
      1. Nombre EXACTO ('NO BREAK' existe tal cual en el catálogo).
      2. Los que EMPIEZAN por el término, no los que solo lo contienen:
         'FOOTSWITCH' contiene 'SWITCH' y no es un switch.
      3. Entre esos, el nombre más CORTO: cada palabra extra es un detalle
         (capacidad, marca, modelo) que lo vuelve más específico.
      4. A igualdad, el id más bajo: determinista, y las claves bajas suelen ser
         las entradas base del catálogo.

    Devuelve el `db.Insumo` elegido o None si no hay ninguna coincidencia.
    """
    from . import db

    objetivo = _norm(nombre)
    if not objetivo:
        return None
    candidatos = db.buscar_insumos(nombre, empresa_id=id_empresa, limite=limite)
    if not candidatos and id_empresa is not None:
        # Sin resultados en la empresa se prueba el catálogo global: el mismo
        # insumo suele estar dado de alta en otra.
        candidatos = db.buscar_insumos(nombre, limite=limite)
    if not candidatos:
        return None

    exactos = [i for i in candidatos if _norm(i.nombre) == objetivo]
    if exactos:
        return min(exactos, key=lambda i: i.id_insumo)

    prefijo = [i for i in candidatos if _norm(i.nombre).startswith(objetivo)]
    elegibles = prefijo or candidatos
    return min(elegibles, key=lambda i: (len(_norm(i.nombre)), i.id_insumo))
