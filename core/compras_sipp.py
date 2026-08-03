"""Bandeja de Compra de Activos Fijos: buscar la entrada de compra de un activo por
su número de serie, descargar su factura y (pendiente) su precio unitario.

Sirve al alta: para un activo con serie VÁLIDA (serie != etiqueta), se localiza la
entrada de compra, se baja la factura (para adjuntarla en el alta) y se obtiene el
precio unitario de compra (sin retención) para el Costo.

Endpoints (confirmados en vivo, app `appBandejaCompraActivos` / cfproxy):
  - Listado por serie -> ConsultaEntradaCompra.ListadoCompraActivosFijos
        {de_SerieInsumo: <serie>}  (el filtro de serie es lo que hace funcionar la
        consulta; los filtros amplios sin serie el SIPP los rechaza).
        Columnas relevantes: DE_SERIEINSUMO, ID_EMPRESA, ID_ORDENDECOMPRA,
        ID_FACTURA, NB_PROVEEDOR, NB_NOMBREINSUMO, FACTURAPDF (directorio),
        DE_NOMBREPDF (archivo), FACTURAXML, SN_PENDIENTEFACTURA.
  - Descarga de la factura -> downloadFile.cfm?d=<FACTURAPDF>&n=<DE_NOMBREPDF>&b=0&a=0
        (mismo patrón que la carta responsiva).

PRECIO: la bandeja NO expone el precio unitario (su grid de precios está comentado
en el controlador). Se toma del XML de la factura (CFDI) que ya devuelve la propia
bandeja: cada Concepto del CFDI trae `ValorUnitario`, que es el precio unitario
ANTES de impuestos (antes de IVA trasladado y de cualquier retención) — justo lo
pedido. Si la factura tiene varios conceptos, se empareja el del activo por serie /
NoIdentificacion / nombre del insumo (ver `precio_unitario_desde_xml`).
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

_RUTA_PROXY = "/componentes/cfproxy.cfc?method=proxy"


class ErrorCompras(Exception):
    """Falla al consultar la bandeja de compras o descargar la factura."""


@dataclass
class EntradaCompra:
    """Una entrada de compra (movimiento) de la bandeja, ligada a una serie."""

    serie: str
    id_empresa: "int | None"
    id_orden_compra: "int | None"
    id_factura: "int | None"
    proveedor: str
    insumo: str
    factura_dir: str          # FACTURAPDF (directorio del PDF)
    factura_pdf: str          # DE_NOMBREPDF (nombre del PDF)
    factura_xml: str          # FACTURAXML (nombre del XML, si aplica)
    pendiente_factura: bool   # SN_PENDIENTEFACTURA

    @property
    def tiene_factura(self) -> bool:
        return bool(self.factura_pdf) and not self.pendiente_factura


def _norm(texto) -> str:
    return " ".join(str(texto or "").strip().upper().split())


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


async def buscar_entradas_por_serie(sesion, serie: str) -> list[EntradaCompra]:
    """Todas las entradas de compra que coinciden con la serie (read-only)."""
    datos = await _invoke(sesion, "ConsultaEntradaCompra", "ListadoCompraActivosFijos",
                          {"de_SerieInsumo": serie})
    if not datos.get("ISOK"):
        return []
    query = datos.get("QUERY", {}) or {}
    idx = {c: i for i, c in enumerate(query.get("COLUMNS") or [])}
    entradas: list[EntradaCompra] = []
    for f in query.get("DATA") or []:
        entradas.append(EntradaCompra(
            serie=_v(f, idx, "DE_SERIEINSUMO") or serie,
            id_empresa=_entier(_v(f, idx, "ID_EMPRESA")),
            id_orden_compra=_entier(_v(f, idx, "ID_ORDENDECOMPRA")),
            id_factura=_entier(_v(f, idx, "ID_FACTURA")),
            proveedor=_v(f, idx, "NB_PROVEEDOR"),
            insumo=_v(f, idx, "NB_NOMBREINSUMO"),
            factura_dir=_v(f, idx, "FACTURAPDF"),
            factura_pdf=_v(f, idx, "DE_NOMBREPDF"),
            factura_xml=_v(f, idx, "FACTURAXML"),
            pendiente_factura=_v(f, idx, "SN_PENDIENTEFACTURA") in ("1", "true", "True")))
    return entradas


def elegir_mejor_entrada(entradas: list[EntradaCompra]) -> "EntradaCompra | None":
    """De varias coincidencias, prefiere una CON factura (no pendiente)."""
    if not entradas:
        return None
    con_factura = [e for e in entradas if e.tiene_factura]
    return con_factura[0] if con_factura else entradas[0]


async def buscar_entrada_por_serie(sesion, serie: str) -> "EntradaCompra | None":
    """La mejor entrada de compra para la serie (con factura si la hay)."""
    return elegir_mejor_entrada(await buscar_entradas_por_serie(sesion, serie))


async def descargar_factura(sesion, entrada: EntradaCompra, carpeta) -> "Path | None":
    """Descarga el PDF de la factura de la entrada a `carpeta`. None si no tiene."""
    if not entrada.factura_pdf:
        return None
    url = (sesion.BASE_URL + "/downloadFile.cfm?d=" + quote(entrada.factura_dir)
           + "&n=" + quote(entrada.factura_pdf) + "&b=0&a=0")
    try:
        resp = await sesion.context.request.get(url)
        contenido = await resp.body()
    except Exception as exc:  # noqa: BLE001
        raise ErrorCompras(
            f"No se pudo descargar la factura «{entrada.factura_pdf}»: {exc}") from exc
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


def _elegir_concepto(conceptos: list[dict], entrada: EntradaCompra) -> "dict | None":
    """Empareja el concepto del CFDI con el activo (por serie/insumo)."""
    if not conceptos:
        return None
    if len(conceptos) == 1:
        return conceptos[0]
    serie, insumo = _norm(entrada.serie), _norm(entrada.insumo)
    # 1) serie exacta en NoIdentificacion; 2) serie contenida en la descripción.
    for c in conceptos:
        if serie and _norm(c.get("NoIdentificacion")) == serie:
            return c
    for c in conceptos:
        if serie and serie in _norm(c.get("Descripcion")):
            return c
    # 3) nombre del insumo contenido en la descripción.
    for c in conceptos:
        if insumo and insumo in _norm(c.get("Descripcion")):
            return c
    # 4) si todos comparten ValorUnitario, es inequívoco.
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


async def _bajar_bytes_factura(sesion, entrada: EntradaCompra,
                               nombre: str) -> "bytes | None":
    """Descarga por downloadFile.cfm un archivo (PDF/XML) del directorio de la
    factura. `nombre` es el archivo (DE_NOMBREPDF o FACTURAXML)."""
    if not nombre:
        return None
    url = (sesion.BASE_URL + "/downloadFile.cfm?d=" + quote(entrada.factura_dir)
           + "&n=" + quote(nombre) + "&b=0&a=0")
    try:
        resp = await sesion.context.request.get(url)
        return await resp.body()
    except Exception as exc:  # noqa: BLE001
        raise ErrorCompras(f"No se pudo descargar «{nombre}»: {exc}") from exc


async def precio_unitario_compra(sesion, entrada: EntradaCompra) -> "float | None":
    """Precio unitario (antes de impuestos/retención) del activo, del XML de la
    factura de su entrada de compra. None si no hay XML o no se identifica."""
    if not entrada.factura_xml:
        return None
    datos = await _bajar_bytes_factura(sesion, entrada, entrada.factura_xml)
    if not datos:
        return None
    return precio_unitario_desde_xml(datos, entrada)
