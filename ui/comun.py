"""Constantes y utilidades compartidas por las pantallas de la interfaz.

Aquí viven los colores de ESTADO, el catálogo de empresas y `CampoFecha`. El
estilo de botones, campos y tarjetas NO está aquí: es de ui/componentes.py.
"""

from __future__ import annotations

from datetime import datetime

import flet as ft

# El catálogo de empresas del Grupo Petroil es la fuente única (core/empresas.py).
# Se re-exporta aquí para que las pantallas lo tomen desde un solo lugar.
from core.empresas import EMPRESAS, ID_POR_EMPRESA, NOMBRES_EMPRESAS  # noqa: F401

# --- Colores -------------------------------------------------------------
# Colores semánticos de ESTADO (éxito / error / aviso). El resto del estilo
# —botones, campos, tarjetas— vive en ui/componentes.py y se pide por rol.
VERDE = ft.Colors.GREEN_700
ROJO = ft.Colors.RED_700
NARANJA = ft.Colors.ORANGE_800
GRIS = ft.Colors.ON_SURFACE_VARIANT

CENTRO = ft.Alignment(0, 0)


# --- Fechas --------------------------------------------------------------
# Formato ÚNICO de fecha en toda la app (México). Si algún día cambia, se
# cambia aquí y en CampoFecha.
FORMATO_FECHA = "%d/%m/%Y"


def parse_fecha(texto: str | None) -> "datetime | None":
    """Convierte 'DD/MM/AAAA' a datetime (None si vacío o inválido)."""
    texto = (texto or "").strip()
    if not texto:
        return None
    for fmt in (FORMATO_FECHA, "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(texto, fmt)
        except ValueError:
            continue
    return None


def fmt_fecha(fecha: "datetime | None") -> str:
    return fecha.strftime(FORMATO_FECHA) if fecha else ""
