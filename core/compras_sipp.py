"""Bandeja de Compra de Activos Fijos: buscar la entrada de compra de un activo por
su número de serie, descargar su factura y (pendiente) su precio unitario.

Sirve al alta: para un activo con serie VÁLIDA (serie != etiqueta), se localiza la
entrada de compra, se baja la factura (para adjuntarla en el alta) y se obtiene el
precio unitario de compra (sin retención) para el Costo.

Endpoints (confirmados en vivo contra PRODUCCIÓN, app `appBandejaCompraActivos`):
  - Listado -> ConsultaEntradaCompra.ListadoCompraActivosFijos. Producción EXIGE
        el objeto de filtros COMPLETO (todas las llaves de `_FILTROS_BASE`) e, igual
        que la pantalla, id_Empresa + rango de fechas (fh_Inicio/fh_Fin); si faltan,
        el SIPP rechaza la consulta. Se filtra además por de_SerieInsumo.
        Columnas: DE_SERIEINSUMO, ID_EMPRESA, ID_ORDENDECOMPRA, ID_FACTURA,
        NB_PROVEEDOR, NB_NOMBREINSUMO, FACTURAPDF (DIRECTORIO), DE_NOMBREPDF
        (archivo PDF), FACTURAXML (directorio), SN_PENDIENTEFACTURA.
  - Descarga de archivos -> Documentos.obtenerArchivo {path:<dir+archivo>,
        sn_Temp:false} -> DATA.URI (URL firmada de Google Cloud Storage) -> se baja
        de esa URI. NO es downloadFile.cfm (esa devuelve vacío para estos archivos).
        El XML del CFDI tiene el MISMO nombre que el PDF con extensión .xml.

PRECIO: se toma del XML del CFDI (Concepto@ValorUnitario = precio unitario ANTES de
impuestos y de cualquier retención). Con varios conceptos se empareja por serie /
NoIdentificacion / nombre del insumo (ver `precio_unitario_desde_xml`).
"""

from __future__ import annotations

import difflib
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date
from pathlib import Path

_RUTA_PROXY = "/componentes/cfproxy.cfc?method=proxy"

# Similitud mínima (0..1) para dar por coincidente la serie del activo con la de
# una entrada de la bandeja: como la captura puede tener errores en CUALQUIERA de
# las dos, no se exige coincidencia exacta sino ≥ este umbral. Ajustable.
UMBRAL_SIMILITUD_SERIE = 0.90

# Llaves que el filtro de la bandeja exige presentes (producción falla si falta
# alguna). Se envían vacías salvo las que se usan (empresa, fechas, serie).
_FILTROS_BASE = {
    "id_Empresa": "", "id_Sucursal": "", "id_Almacen": "", "de_SerieInsumo": "",
    "fl_Movimiento": "", "nb_Proveedor": "", "id_OrdenDeCompra": "",
    "id_FamiliaInsumo": "", "id_SubFamiliaInsumo": "", "nb_NombreInsumo": "",
    "fh_Inicio": "", "fh_Fin": "", "sn_ActivoFijo": "", "sn_InsumoRelevante": "",
    "sn_EntradaPendienteFactura": "",
}
# Años hacia atrás para el rango de fechas de la consulta (la compra puede ser
# antigua respecto al levantamiento).
_ANIOS_ATRAS = 6


class ErrorCompras(Exception):
    """Falla al consultar la bandeja de compras o descargar la factura."""


@dataclass
class EntradaCompra:
    """Una entrada de compra (movimiento) de la bandeja, ligada a una serie."""

    serie: str
    id_empresa: "int | None"
    id_sucursal: "int | None"
    id_proveedor: "int | None"
    id_orden_compra: "int | None"
    id_factura: "int | None"
    proveedor: str
    insumo: str
    factura_dir: str          # FACTURAPDF (DIRECTORIO de la factura)
    factura_pdf: str          # DE_NOMBREPDF (nombre del PDF)
    pendiente_factura: bool   # SN_PENDIENTEFACTURA

    @property
    def tiene_factura(self) -> bool:
        return bool(self.factura_dir and self.factura_pdf) and not self.pendiente_factura

    @property
    def ruta_pdf(self) -> str:
        """Ruta del PDF de la factura (directorio + nombre)."""
        return self.factura_dir + self.factura_pdf

    @property
    def ruta_xml(self) -> str:
        """Ruta del XML del CFDI: mismo nombre que el PDF con extensión .xml."""
        if not self.factura_pdf:
            return ""
        return self.factura_dir + self.factura_pdf.rsplit(".", 1)[0] + ".xml"


def _norm(texto) -> str:
    return " ".join(str(texto or "").strip().upper().split())


def _norm_serie(serie) -> str:
    """Normaliza una serie para comparar: mayúsculas y solo alfanuméricos (quita
    espacios, guiones, '/', etc., que suelen variar entre capturas)."""
    return re.sub(r"[^A-Z0-9]", "", str(serie or "").upper())


def similitud_serie(a, b) -> float:
    """Similitud 0..1 entre dos series (normalizadas). 0 si alguna queda vacía."""
    na, nb = _norm_serie(a), _norm_serie(b)
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def serie_valida(serie, etiqueta) -> bool:
    """La serie es utilizable solo si existe y NO coincide con la etiqueta.

    Muchos activos no tienen serie real y traen la etiqueta en ese campo; en ese
    caso se asume que no hay serie y no se busca en la bandeja."""
    s = _norm(serie)
    return bool(s) and s != _norm(etiqueta)


async def _invoke(sesion, component: str, metodo: str, args: dict) -> dict:
    payload = json.dumps({"component": component, "execMethod": metodo,
                          "argumentcollection": args})
    try:
        resp = await sesion.context.request.post(
            sesion.BASE_URL + _RUTA_PROXY, data=payload,
            headers={"Content-Type": "application/json"})
        return await resp.json()
    except Exception as exc:  # noqa: BLE001 — se reporta como ErrorCompras
        raise ErrorCompras(f"No se pudo consultar {component}.{metodo}: {exc}") from exc


def _v(fila, idx, col) -> str:
    i = idx.get(col)
    v = fila[i] if i is not None and i < len(fila) else None
    return "" if v is None else str(v)


def _entier(texto):
    t = str(texto or "").strip()
    return int(t) if t.isdigit() else None


def _rango_fechas() -> "tuple[str, str]":
    """Rango (inicio, fin) 'YYYY-MM-DD' para la consulta: _ANIOS_ATRAS años a hoy."""
    hoy = date.today()
    return date(hoy.year - _ANIOS_ATRAS, 1, 1).strftime("%Y-%m-%d"), hoy.strftime("%Y-%m-%d")


async def _consultar_bandeja(sesion, serie: str, id_empresa) -> list[EntradaCompra]:
    """Consulta cruda de la bandeja (read-only). `serie` vacía = sin filtro de serie
    (trae las entradas de la empresa para emparejar de forma aproximada).

    Producción exige el filtro COMPLETO + empresa + rango de fechas (como la
    pantalla). `id_empresa`: la del activo (numérica)."""
    fh_ini, fh_fin = _rango_fechas()
    filtros = dict(_FILTROS_BASE)
    filtros.update(de_SerieInsumo=serie or "", fh_Inicio=fh_ini, fh_Fin=fh_fin)
    if id_empresa:
        filtros["id_Empresa"] = id_empresa
    datos = await _invoke(sesion, "ConsultaEntradaCompra", "ListadoCompraActivosFijos",
                          filtros)
    if not datos.get("ISOK"):
        return []
    query = datos.get("QUERY", {}) or {}
    idx = {c: i for i, c in enumerate(query.get("COLUMNS") or [])}
    entradas: list[EntradaCompra] = []
    for f in query.get("DATA") or []:
        entradas.append(EntradaCompra(
            serie=_v(f, idx, "DE_SERIEINSUMO"),
            id_empresa=_entier(_v(f, idx, "ID_EMPRESA")),
            id_sucursal=_entier(_v(f, idx, "ID_SUCURSAL")),
            id_proveedor=_entier(_v(f, idx, "ID_PROVEEDOR")),
            id_orden_compra=_entier(_v(f, idx, "ID_ORDENDECOMPRA")),
            id_factura=_entier(_v(f, idx, "ID_FACTURA")),
            proveedor=_v(f, idx, "NB_PROVEEDOR"),
            insumo=_v(f, idx, "NB_NOMBREINSUMO"),
            factura_dir=_v(f, idx, "FACTURAPDF"),
            factura_pdf=_v(f, idx, "DE_NOMBREPDF"),
            pendiente_factura=_v(f, idx, "SN_PENDIENTEFACTURA") in ("1", "true", "True")))
    return entradas


async def buscar_entradas_por_serie(
        sesion, serie: str, id_empresa=None,
        umbral: float = UMBRAL_SIMILITUD_SERIE) -> list[EntradaCompra]:
    """Entradas de compra cuya serie coincide con `serie` al menos en `umbral`
    (0..1) de similitud, ordenadas de más a menos parecida (read-only).

    Tolera errores de captura en cualquiera de las dos series: primero intenta el
    filtro exacto del SIPP (rápido) y, si no arroja coincidencias aceptables, trae
    las entradas de la empresa y empareja por similitud."""
    def aceptables(entradas):
        califs = [(similitud_serie(e.serie, serie), e) for e in entradas]
        elegidas = [(s, e) for s, e in califs if s >= umbral]
        # más parecidas primero y, a igual parecido, las que tienen factura.
        elegidas.sort(key=lambda se: (se[0], se[1].tiene_factura), reverse=True)
        return [e for _s, e in elegidas]

    exactas = aceptables(await _consultar_bandeja(sesion, serie, id_empresa))
    if exactas:
        return exactas
    # Aproximado: sin filtro de serie, se empareja contra las de la empresa.
    return aceptables(await _consultar_bandeja(sesion, "", id_empresa))


def elegir_mejor_entrada(entradas: list[EntradaCompra]) -> "EntradaCompra | None":
    """De las coincidencias (ya ordenadas por similitud), prefiere una CON factura."""
    if not entradas:
        return None
    con_factura = [e for e in entradas if e.tiene_factura]
    return con_factura[0] if con_factura else entradas[0]


async def buscar_entrada_por_serie(
        sesion, serie: str, id_empresa=None,
        umbral: float = UMBRAL_SIMILITUD_SERIE) -> "EntradaCompra | None":
    """La mejor entrada de compra para la serie (≥ umbral de similitud; con factura
    si la hay)."""
    return elegir_mejor_entrada(
        await buscar_entradas_por_serie(sesion, serie, id_empresa, umbral))


async def _bajar_archivo(sesion, path: str) -> "bytes | None":
    """Bytes de un archivo de Documentos. `Documentos.obtenerArchivo` devuelve una
    URI firmada (Google Cloud Storage) de la que se baja el contenido."""
    if not path:
        return None
    datos = await _invoke(sesion, "Documentos", "obtenerArchivo",
                          {"path": path, "sn_Temp": False})
    if not datos.get("ISOK"):
        return None
    uri = (datos.get("DATA") or {}).get("URI")
    if not uri:
        return None
    try:
        resp = await sesion.context.request.get(uri)
        return await resp.body()
    except Exception as exc:  # noqa: BLE001
        raise ErrorCompras(f"No se pudo descargar «{path}»: {exc}") from exc


async def descargar_factura(sesion, entrada: EntradaCompra, carpeta) -> "Path | None":
    """Descarga el PDF de la factura de la entrada a `carpeta`. None si no tiene."""
    if not entrada.tiene_factura:
        return None
    contenido = await _bajar_archivo(sesion, entrada.ruta_pdf)
    if not contenido:
        return None
    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    destino = carpeta / entrada.factura_pdf
    destino.write_bytes(contenido)
    return destino


def _local(tag: str) -> str:
    """Nombre local de una etiqueta XML (sin el {namespace})."""
    return tag.rsplit("}", 1)[-1]


def _conceptos_cfdi(xml_bytes: bytes) -> list[dict]:
    """Conceptos del CFDI como dicts de atributos (namespace-agnóstico, 3.3/4.0)."""
    raiz = ET.fromstring(xml_bytes)
    return [dict(el.attrib) for el in raiz.iter() if _local(el.tag) == "Concepto"]


_ACENTOS = str.maketrans("ÁÉÍÓÚÜÑ", "AEIOUUN")


def _tokens(texto) -> set:
    """Palabras significativas (>=3 caracteres alfanuméricos), en mayúsculas."""
    up = str(texto or "").upper().translate(_ACENTOS)
    return set(re.findall(r"[A-Z0-9]{3,}", up))


def _elegir_concepto(conceptos: list[dict], entrada: EntradaCompra) -> "dict | None":
    """Empareja el concepto del CFDI con el activo. Con un solo concepto es directo;
    con varios, intenta por serie y, si no, por los tokens del nombre del insumo
    PONDERADOS por rareza (un token presente en un solo concepto pesa más). Si no hay
    un ganador claro, devuelve None (mejor dejar el costo a captura manual que
    arriesgar un monto equivocado)."""
    if not conceptos:
        return None
    if len(conceptos) == 1:
        return conceptos[0]
    serie = _norm(entrada.serie)
    # 1) serie en NoIdentificacion o dentro de la descripción.
    for c in conceptos:
        if serie and _norm(c.get("NoIdentificacion")) == serie:
            return c
    for c in conceptos:
        if serie and serie in _norm(c.get("Descripcion")):
            return c
    # 2) tokens del nombre del insumo, pesados por rareza (IDF simple): el concepto
    # con mayor puntaje gana, pero solo si es un ganador ÚNICO.
    itk = _tokens(entrada.insumo)
    if itk:
        ctks = [_tokens(c.get("Descripcion")) for c in conceptos]
        df = {tok: sum(1 for ct in ctks if tok in ct) for tok in itk}
        puntajes = [sum(1.0 / df[tok] for tok in itk if df.get(tok) and tok in ct)
                    for ct in ctks]
        mejor = max(range(len(conceptos)), key=lambda i: puntajes[i])
        if puntajes[mejor] > 0 and puntajes.count(puntajes[mejor]) == 1:
            return conceptos[mejor]
    # 3) si todos comparten ValorUnitario, es inequívoco.
    valores = {c.get("ValorUnitario") for c in conceptos}
    return conceptos[0] if len(valores) == 1 else None


def precio_unitario_desde_xml(xml_bytes: bytes,
                              entrada: EntradaCompra) -> "float | None":
    """Precio unitario ANTES de impuestos (CFDI `Concepto@ValorUnitario`) del activo.

    Devuelve None si el XML no es parseable o no se puede identificar el concepto
    del activo con certeza (varios conceptos con distinto precio)."""
    try:
        conceptos = _conceptos_cfdi(xml_bytes)
    except ET.ParseError:
        return None
    concepto = _elegir_concepto(conceptos, entrada)
    if not concepto:
        return None
    try:
        return float(concepto.get("ValorUnitario"))
    except (TypeError, ValueError):
        return None


def folio_desde_xml(xml_bytes: bytes) -> str:
    """Folio de la factura (CFDI `Comprobante@Serie`+`@Folio`), '' si no se puede."""
    try:
        raiz = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return ""
    comp = raiz if _local(raiz.tag) == "Comprobante" else next(
        (e for e in raiz.iter() if _local(e.tag) == "Comprobante"), None)
    if comp is None:
        return ""
    serie = (comp.attrib.get("Serie") or "").strip()
    folio = (comp.attrib.get("Folio") or "").strip()
    return (serie + folio).strip() if (serie or folio) else ""


async def _bajar_xml_factura(sesion, entrada: EntradaCompra) -> "bytes | None":
    """Bytes del XML del CFDI. Vía robusta: `ProveedoresFacturas.generarXML` por
    ID_FACTURA devuelve la ruta real del XML (el nombre no siempre coincide con el
    del PDF). Respaldo: mismo nombre que el PDF con extensión .xml."""
    if entrada.id_factura is not None:
        datos = await _invoke(sesion, "ProveedoresFacturas", "generarXML",
                              {"id_Empresa": entrada.id_empresa,
                               "id_Sucursal": entrada.id_sucursal,
                               "id_Proveedor": entrada.id_proveedor,
                               "id_Factura": entrada.id_factura})
        j = datos.get("JSON") or {}
        ruta = (j.get("DE_RUTA") or "") + (j.get("NB_ARCHIVO") or "")
        if datos.get("ISOK") and ruta:
            contenido = await _bajar_archivo(sesion, ruta)
            if contenido:
                return contenido
    return await _bajar_archivo(sesion, entrada.ruta_xml)


async def datos_factura(sesion, entrada: EntradaCompra) -> dict:
    """Descarga el XML del CFDI UNA vez y devuelve {precio, folio}.

    `precio`: unitario antes de impuestos (None si no se identifica).
    `folio`: folio del CFDI ('' si no está). Si no hay XML, ambos vacíos."""
    contenido = await _bajar_xml_factura(sesion, entrada)
    if not contenido:
        return {"precio": None, "folio": ""}
    return {"precio": precio_unitario_desde_xml(contenido, entrada),
            "folio": folio_desde_xml(contenido)}


async def precio_unitario_compra(sesion, entrada: EntradaCompra) -> "float | None":
    """Precio unitario (antes de impuestos/retención) del activo, del XML de la
    factura de su entrada de compra. None si no hay XML o no se identifica."""
    return (await datos_factura(sesion, entrada)).get("precio")
