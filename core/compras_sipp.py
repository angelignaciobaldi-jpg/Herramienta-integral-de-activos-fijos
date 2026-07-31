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
en el controlador). Se decidió tomarlo del DETALLE DE LA ORDEN DE COMPRA
(IM_PRECIOUNITARIO del renglón del insumo). Ese endpoint vive en el módulo de
Órdenes de Compra (aún no capturado); `precio_unitario_compra` queda pendiente de
cablear en cuanto se confirme (ver TODO).
"""

from __future__ import annotations

import json
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


async def precio_unitario_compra(sesion, entrada: EntradaCompra) -> "float | None":
    """Precio unitario de compra (sin retención) del insumo, del DETALLE DE LA OC.

    TODO: cablear con el endpoint del módulo de Órdenes de Compra (aún no
    capturado). Con `entrada.id_empresa` + `entrada.id_orden_compra` se pedirá el
    detalle de la OC y se tomará IM_PRECIOUNITARIO del renglón cuyo insumo coincide
    con `entrada.insumo`. Devuelve None mientras no esté disponible."""
    return None
