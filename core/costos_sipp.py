"""Costos de los activos ya dados de alta, desde el microservicio de la plataforma.

El listado del catálogo del SIPP (`core/activos_sipp`) trae el activo completo pero
su costo (`IM_COSTO`) llega vacío o en cero para buena parte del inventario. El
costo REAL vive en el microservicio:

    GET {API_BASE}/api/activos-fijos/listado
        ?empresa=<id_empresa>&sucursal=<id_sucursal>&tipo=&page=N&pageSize=M
        &activo=&etiqueta=&serie=

Cada fila trae `etiqueta` y `precio` (texto con formato de moneda, p. ej.
``"$1,234.56"``), más empresa/sucursal/empleado/nombre. La ETIQUETA es la llave:
es el mismo identificador con el que «Buscar en SIPP» empareja el levantamiento
contra el catálogo, así que basta indexar {etiqueta -> precio} y volcarlo en la
caché de activos (`core/db.fijar_costos_activos_sipp`).

Se descarga EN BLOQUE por empresa durante «Actualizar información del SIPP» (unas
pocas decenas de peticiones para todo el inventario), no una petición por activo:
así «Buscar en SIPP» sigue siendo instantáneo y offline, y el costo aparece solo.

La URL base y el token salen de `core/ajustes_api` vía `core/api` (mismo canal que
el resto de microservicios); no se piden credenciales aparte.

Nota sobre la respuesta: el envoltorio del JSON puede variar (lista suelta,
``{"data": [...]}``, etc.), así que las filas se localizan de forma tolerante en
vez de fijar una forma rígida.
"""

from __future__ import annotations

import re

from . import api, db

# Ruta del microservicio (relativa a la URL base de core/ajustes_api).
RUTA = "/api/activos-fijos/listado"
# Tamaño de página de la descarga en bloque. El inventario completo son decenas de
# miles de filas; páginas grandes reducen las peticiones sin que el JSON crezca de
# más (cada fila son ~10 campos).
PAGE_SIZE = 1000
# Tope de seguridad: evita un bucle infinito si el endpoint deja de paginar.
_MAX_PAGINAS = 200

# Claves donde suele venir la lista de filas dentro del JSON.
_CLAVES_LISTA = ("data", "datos", "items", "results", "rows", "registros",
                 "listado", "activos")

# Todo lo que no sea dígito, signo o separador decimal ($ , espacios, MXN…).
_NO_NUMERICO = re.compile(r"[^0-9.\-]")


class ErrorCostos(Exception):
    """Falla al consultar el microservicio de costos de activos fijos."""


def _filas(payload) -> list[dict]:
    """Localiza la lista de filas dentro de la respuesta, sea cual sea su
    envoltorio (lista suelta, {"data": [...]}, {"data": {"items": [...]}})."""
    if isinstance(payload, list):
        return [f for f in payload if isinstance(f, dict)]
    if not isinstance(payload, dict):
        return []
    for clave in _CLAVES_LISTA:
        valor = payload.get(clave)
        if isinstance(valor, list):
            return [f for f in valor if isinstance(f, dict)]
        if isinstance(valor, dict):
            anidado = _filas(valor)
            if anidado:
                return anidado
    # Último recurso: la primera lista de objetos que aparezca.
    for valor in payload.values():
        if isinstance(valor, list) and any(isinstance(f, dict) for f in valor):
            return [f for f in valor if isinstance(f, dict)]
    return []


def precio(valor) -> float | None:
    """Convierte el precio del microservicio a float. Acepta ``"$1,234.56"``,
    ``"1234.56"`` o un número. Devuelve None si no es interpretable.

    OJO: un precio de 0 se devuelve como 0.0 (es un valor válido); es quien
    consume el dato el que decide si un 0 vale la pena guardarse."""
    if valor is None:
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    limpio = _NO_NUMERICO.sub("", str(valor).strip())
    if not limpio or limpio in ("-", ".", "-."):
        return None
    try:
        return float(limpio)
    except ValueError:
        return None


def _total(payload, filas: list[dict]) -> int | None:
    """Total de filas de la consulta. Puede venir en el envoltorio o repetido en
    cada fila (`total`), que es como responde hoy el endpoint."""
    fuentes = []
    if isinstance(payload, dict):
        fuentes.append(payload)
    if filas:
        fuentes.append(filas[0])
    for fuente in fuentes:
        for clave in ("total", "totalRegistros", "count"):
            try:
                return int(fuente[clave])
            except (KeyError, TypeError, ValueError):
                continue
    return None


def consultar(*, id_empresa: "int | None" = None, id_sucursal: "int | None" = None,
              etiqueta: str = "", serie: str = "", tipo: "int | str | None" = None,
              activo: "str | None" = None, page: int = 1,
              page_size: int = PAGE_SIZE) -> tuple[list[dict], "int | None"]:
    """Una página cruda del endpoint. Devuelve (filas, total).

    Solo se envían los parámetros con valor: el endpoint los interpreta como
    «sin filtro» cuando van vacíos, pero mandarlos vacíos no aporta nada.
    Lanza ErrorCostos si la API falla o responde algo que no es una lista."""
    params: dict[str, object] = {"page": page, "pageSize": page_size}
    if id_empresa is not None:
        params["empresa"] = id_empresa
    if id_sucursal is not None:
        params["sucursal"] = id_sucursal
    if tipo not in (None, ""):
        params["tipo"] = tipo
    if activo not in (None, ""):
        params["activo"] = activo
    if etiqueta:
        params["etiqueta"] = etiqueta
    if serie:
        params["serie"] = serie
    try:
        # Los filtros van por `params`, NUNCA pegados a la ruta: `api._url`
        # percent-codifica la ruta entera (por los acentos de otros endpoints),
        # así que un "?" incrustado viajaba como "%3F" y la API respondía 404.
        payload = api.solicitar(RUTA, params=params)
    except api.ErrorAPI as exc:
        raise ErrorCostos(str(exc)) from exc
    filas = _filas(payload)
    return filas, _total(payload, filas)


def descargar_costos(id_empresa: int, progreso=None,
                     page_size: int = PAGE_SIZE) -> dict[str, float]:
    """Descarga TODOS los costos de una empresa. Devuelve {etiqueta -> precio}.

    `progreso(hechos, total)`: callback opcional durante la paginación.

    Se omiten las filas sin etiqueta y las de precio 0 o no interpretable: un 0
    no es un costo, y guardarlo taparía el `IM_COSTO` que sí traiga el catálogo
    del SIPP."""
    costos: dict[str, float] = {}
    total: int | None = None
    vistas = 0
    for page in range(1, _MAX_PAGINAS + 1):
        filas, tot = consultar(id_empresa=id_empresa, page=page, page_size=page_size)
        if tot is not None and total is None:
            total = tot
        if not filas:
            break
        for f in filas:
            etq = str(f.get("etiqueta") or "").strip()
            p = precio(f.get("precio"))
            if etq and p:      # descarta etiqueta vacía y precio 0/None
                costos[etq] = p
        vistas += len(filas)
        if progreso:
            progreso(vistas, total or vistas)
        # Última página: el endpoint devolvió menos filas de las pedidas, o ya se
        # alcanzó el total anunciado.
        if len(filas) < page_size or (total is not None and vistas >= total):
            break
    return costos


def costo_de_etiqueta(etiqueta: str, id_empresa: "int | None" = None) -> "float | None":
    """Costo de UN activo por su etiqueta (consulta puntual al microservicio).

    Para el flujo normal se usa `actualizar_costos` (en bloque); esto sirve para
    refrescar un activo suelto sin volver a sincronizar todo."""
    etq = (etiqueta or "").strip()
    if not etq:
        return None
    filas, _ = consultar(id_empresa=id_empresa, etiqueta=etq, page=1, page_size=5)
    for f in filas:
        # El filtro del endpoint puede ser «contiene»: se exige la etiqueta exacta.
        if str(f.get("etiqueta") or "").strip() == etq:
            return precio(f.get("precio"))
    return None


def actualizar_costos(id_empresa: int, progreso=None) -> dict:
    """Descarga los costos de la empresa y los vuelca en la caché de activos.

    Devuelve {descargados, aplicados}: cuántas etiquetas trajo el microservicio y
    a cuántos activos cacheados se les fijó el costo (las etiquetas que no están
    en la caché de esa empresa se ignoran).

    Se llama DESPUÉS de `activos_sipp.descargar_activos`, que reescribe la caché
    de la empresa (y con ella borraría cualquier costo previo)."""
    costos = descargar_costos(id_empresa, progreso=progreso)
    aplicados = db.fijar_costos_activos_sipp(id_empresa, costos) if costos else 0
    return {"descargados": len(costos), "aplicados": aplicados}
