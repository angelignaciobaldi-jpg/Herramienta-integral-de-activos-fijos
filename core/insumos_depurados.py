"""Catálogo DEPURADO de insumos: los genéricos que sí son activo fijo.

Es un recorte curado a mano del catálogo del SIPP (~34,000 insumos por empresa):
se quitaron servicios y todo lo que trae marca, medida, color o código en el
nombre, porque un catálogo así de específico no sirve para clasificar un activo
levantado en campo. Quedan ~1,700 nombres genéricos.

Va como archivo de datos y no en la base porque **no es caché de nada**: no se
descarga del SIPP ni se refresca solo, es una curaduría que se versiona con el
código. Vive en `Catalogos/insumos_depurados.csv`, que viaja dentro del .exe
(`rutas.BUNDLE`); para actualizarlo se reemplaza el CSV y se publica una versión.

A diferencia de los catálogos de empresa (grupos, centros de costo,
departamentos), este es el MISMO para todas: es la lista de qué cosas son activo
fijo, no de cómo las contabiliza cada empresa.
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass

from core import rutas

RUTA = os.path.join(rutas.BUNDLE, "Catalogos", "insumos_depurados.csv")


@dataclass(frozen=True)
class InsumoDepurado:
    clave: str
    insumo: str
    familia: str
    subfamilia: str
    unidad: str


_cache: list[InsumoDepurado] | None = None


def cargar() -> list[InsumoDepurado]:
    """El catálogo completo, ordenado por nombre. Lista vacía si falta el archivo.

    No lanza si el CSV no está: la plantilla se genera igual, solo que sin el
    desplegable de insumos. Que falte un catálogo de ayuda no es motivo para
    dejar al usuario sin plantilla.
    """
    global _cache
    if _cache is not None:
        return _cache
    filas: list[InsumoDepurado] = []
    try:
        with open(RUTA, encoding="utf-8", newline="") as fh:
            for f in csv.DictReader(fh):
                nombre = (f.get("insumo") or "").strip()
                if nombre:
                    filas.append(InsumoDepurado(
                        (f.get("clave") or "").strip(), nombre,
                        (f.get("familia") or "").strip(),
                        (f.get("subfamilia") or "").strip(),
                        (f.get("unidad") or "").strip()))
    except (OSError, csv.Error, UnicodeDecodeError):
        filas = []
    _cache = filas
    return _cache


def nombres() -> list[str]:
    """Solo los nombres, que es lo que alimenta el desplegable de la plantilla."""
    return [i.insumo for i in cargar()]
