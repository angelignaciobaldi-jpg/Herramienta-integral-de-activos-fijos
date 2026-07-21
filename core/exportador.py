"""Exportación de la base de activos a Excel / CSV.

Genera un archivo con los activos registrados (core/db.py). Es la base del módulo
de Exportación / Reportes; amplíalo con plantillas y formatos concretos (PDF de
resguardo, reportes por empresa/categoría, etc.) según lo defina el área.
"""

from __future__ import annotations

import csv
import os
from dataclasses import asdict, fields

from openpyxl import Workbook

from . import db

# Encabezados legibles por columna (orden = orden en el archivo exportado).
_ENCABEZADOS = {
    "id": "ID",
    "num_inventario": "Núm. inventario",
    "descripcion": "Descripción",
    "empresa": "Empresa",
    "categoria": "Categoría",
    "ubicacion": "Ubicación",
    "marca": "Marca",
    "modelo": "Modelo",
    "num_serie": "Núm. serie",
    "fecha_adquisicion": "Fecha adquisición",
    "valor_adquisicion": "Valor adquisición",
    "estatus": "Estatus",
    "ruta_documento": "Documento",
    "creado_en": "Registrado en",
}


def _columnas() -> list[str]:
    """Orden de columnas: el de la dataclass Activo (fuente única del esquema)."""
    return [f.name for f in fields(db.Activo)]


def exportar_excel(ruta_destino: str, activos: "list[db.Activo] | None" = None) -> int:
    """Exporta los activos a un .xlsx. Devuelve cuántas filas se escribieron.
    Si `activos` es None, exporta todos los de la base."""
    activos = db.listar() if activos is None else activos
    cols = _columnas()
    wb = Workbook()
    hoja = wb.active
    hoja.title = "Activos fijos"
    hoja.append([_ENCABEZADOS.get(c, c) for c in cols])
    for a in activos:
        d = asdict(a)
        hoja.append([d.get(c) for c in cols])
    os.makedirs(os.path.dirname(os.path.abspath(ruta_destino)), exist_ok=True)
    wb.save(ruta_destino)
    return len(activos)


def exportar_csv(ruta_destino: str, activos: "list[db.Activo] | None" = None) -> int:
    """Exporta los activos a un .csv (UTF-8 con BOM, compatible con Excel).
    Devuelve cuántas filas se escribieron."""
    activos = db.listar() if activos is None else activos
    cols = _columnas()
    os.makedirs(os.path.dirname(os.path.abspath(ruta_destino)), exist_ok=True)
    with open(ruta_destino, "w", encoding="utf-8-sig", newline="") as fh:
        escritor = csv.writer(fh)
        escritor.writerow([_ENCABEZADOS.get(c, c) for c in cols])
        for a in activos:
            d = asdict(a)
            escritor.writerow([d.get(c) for c in cols])
    return len(activos)
