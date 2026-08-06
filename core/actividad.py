"""Bitácora de movimientos (endpoint /api/activos-fijos/actividad-reciente).

Es el detalle que hay detrás de la tarjeta «Actividad reciente» del tablero:
el listado completo de movimientos del ámbito, paginado EN EL SERVIDOR.

A diferencia de `core/dashboard.py` y `core/inversion.py`, este endpoint no
envuelve su resultado en una cadena de JSON: `data` trae directamente las filas.
El armazón del paginado —`num`, `total` y la cuenta de páginas— vive en
`core/paginado.py`, que comparte con los demás listados.

`parsear()` está separada de `obtener()` a propósito: es una función pura sobre
un diccionario, así que se puede ejercitar con una respuesta guardada sin
levantar red ni API.
"""

from __future__ import annotations

from dataclasses import dataclass

from core import api, paginado

RUTA = "api/activos-fijos/actividad-reciente"
TAM_PAGINA = paginado.TAM_PAGINA


@dataclass
class MovimientoReciente:
    """Un movimiento del listado. Los textos llegan listos para pintarse."""

    numero: int = 0            # posición dentro del listado COMPLETO
    empresa: str = ""
    sucursal: str = ""
    empleado: str = ""
    fecha: str = ""            # ya formateada por el servicio (DD/MM/AAAA)
    etiqueta: str = ""
    serie: str = ""
    nombre: str = ""
    observaciones: str = ""
    precio: str = ""           # ya formateado por el servicio ("$0.00")


def _movimiento(fila: dict) -> MovimientoReciente:
    return MovimientoReciente(
        numero=paginado.entero(fila.get("num")),
        empresa=str(fila.get("empresa") or ""),
        sucursal=str(fila.get("sucursal") or ""),
        empleado=str(fila.get("empleado_movimiento") or ""),
        fecha=str(fila.get("fecha_movimiento") or ""),
        etiqueta=str(fila.get("etiqueta") or ""),
        serie=str(fila.get("serie") or ""),
        nombre=str(fila.get("nombre") or ""),
        observaciones=str(fila.get("observaciones") or ""),
        precio=str(fila.get("precio") or ""))


def parsear(respuesta: dict, pagina: int = 1,
            tam_pagina: int = TAM_PAGINA) -> paginado.Pagina:
    """Convierte la respuesta cruda en una página de movimientos."""
    return paginado.parsear(respuesta, pagina, tam_pagina, _movimiento)


def obtener(pagina: int = 1, tam_pagina: int = TAM_PAGINA, *,
            empresa: int | None = None, sucursal: int | None = None,
            tipo: int | None = None) -> paginado.Pagina:
    """Trae una página del listado para el ámbito pedido.

    El parámetro `activo` que el endpoint admite se deja fuera a propósito: en
    la bitácora no hay ningún control que lo mueva, y mandarlo fijo sería
    filtrar por algo que el usuario no eligió.

    Propaga `api.ErrorAPI` y `entorno.FaltaVariableEntorno` sin envolverlos.
    """
    respuesta = api.solicitar(RUTA, params=paginado.params(
        pagina, tam_pagina, empresa, sucursal, tipo))
    return parsear(respuesta, pagina, tam_pagina)
