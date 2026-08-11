"""Listado de activos fijos (endpoint /api/activos-fijos/listado).

Es el detalle que hay detrás de las dos cifras de la tarjeta «Total de activos
fijos»: el inventario del ámbito, paginado EN EL SERVIDOR. Cuál de las dos se
pulsó lo dice el parámetro `activo`.

El armazón del paginado vive en `core/paginado.py`, compartido con la bitácora.

`parsear()` está separada de `obtener()` a propósito: es una función pura sobre
un diccionario, así que se puede ejercitar con una respuesta guardada sin
levantar red ni API.
"""

from __future__ import annotations

from dataclasses import dataclass

from core import api, paginado

RUTA = "api/activos-fijos/listado"
TAM_PAGINA = paginado.TAM_PAGINA


@dataclass
class ActivoListado:
    """Un activo del listado. Los textos llegan listos para pintarse."""

    numero: int = 0            # posición dentro del listado COMPLETO
    empresa: str = ""
    sucursal: str = ""
    empleado: str = ""         # quien lo tiene en resguardo
    # Puede venir NULA: un activo que nunca se ha movido no tiene fecha. Se
    # deja en cadena vacía y es la pantalla la que decide con qué marcarlo.
    fecha: str = ""
    etiqueta: str = ""
    serie: str = ""
    nombre: str = ""
    precio: str = ""           # ya formateado por el servicio ("$17,000.00")


def _activo(fila: dict) -> ActivoListado:
    return ActivoListado(
        numero=paginado.entero(fila.get("num")),
        empresa=str(fila.get("empresa") or ""),
        sucursal=str(fila.get("sucursal") or ""),
        empleado=str(fila.get("empleado_resguardo") or ""),
        fecha=str(fila.get("fecha_movimiento") or ""),
        etiqueta=str(fila.get("etiqueta") or ""),
        serie=str(fila.get("serie") or ""),
        nombre=str(fila.get("nombre") or ""),
        precio=str(fila.get("precio") or ""))


def parsear(respuesta: dict, pagina: int = 1,
            tam_pagina: int = TAM_PAGINA) -> paginado.Pagina:
    """Convierte la respuesta cruda en una página de activos."""
    return paginado.parsear(respuesta, pagina, tam_pagina, _activo)


def obtener(pagina: int = 1, tam_pagina: int = TAM_PAGINA, *,
            empresa: int | None = None, sucursal: int | None = None,
            tipo: int | None = None, activo: bool | None = None,
            etiqueta: str | None = None,
            serie: str | None = None) -> paginado.Pagina:
    """Trae una página del listado para el ámbito pedido.

    `activo` dice qué mitad del inventario se pide: `True` los vigentes,
    `False` los dados de baja, `None` los dos. Es lo único que distingue el
    detalle de una cifra de la tarjeta del de la otra.

    `etiqueta` y `serie` acotan la búsqueda a un activo concreto. Hoy no los usa
    ninguna pantalla —quedan listos para el buscador del listado—, así que su
    valor normal es `None` y ni siquiera viajan.

    Se normaliza la cadena vacía a `None`: `api._url` solo omite los `None`, y un
    buscador en blanco mandaría `?etiqueta=`, que el servidor tendría que
    distinguir de una búsqueda legítima por cadena vacía.

    Propaga `api.ErrorAPI` y `entorno.FaltaVariableEntorno` sin envolverlos.
    """
    respuesta = api.solicitar(RUTA, params={
        **paginado.params(pagina, tam_pagina, empresa, sucursal, tipo, activo),
        "etiqueta": (etiqueta or "").strip() or None,
        "serie": (serie or "").strip() or None,
    })
    return parsear(respuesta, pagina, tam_pagina)
