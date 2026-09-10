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
# Azul de ESTADO, para marcar un ícono como "sí tiene". No se usa el rol
# `PRIMARY` porque en el tema claro es #000666 —casi negro— y junto a un ícono
# apagado no se distingue: la marca tiene que leerse de un vistazo. Es el mismo
# peso que VERDE (700), para que ambos estados se vean de la misma familia.
AZUL = ft.Colors.BLUE_700

CENTRO = ft.Alignment(0, 0)


# --- Eventos -------------------------------------------------------------
def puntero_encima(e) -> bool:
    """Si el evento de `on_hover` es de ENTRADA (True) o de salida (False).

    Existe por una trampa que costó cara: en Flet 0.85 `e.data` de `on_hover` es
    un **booleano** (lo dice la documentación de `Container.on_hover`), pero las
    versiones viejas mandaban la cadena `"true"`, y ese es el idiom que corre por
    todos los tutoriales. Comparar contra `"true"` da SIEMPRE falso, así que el
    manejador se ejecuta, toma la rama de "salió" y no se ve absolutamente nada.

    Se aceptan las dos formas para no atarse a la versión de Flet.
    """
    return e.data is True or e.data == "true"


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


def error_al_guardar(exc: Exception, ruta: str = "") -> str:
    """Mensaje entendible cuando falla escribir un archivo que eligió el usuario.

    El caso común en Windows no es un permiso mal puesto: es que el archivo está
    ABIERTO en Excel (o en el visor de PDF), que lo bloquea en exclusiva. El
    sistema responde 'Permission denied' y el usuario, que además autorizó
    reemplazarlo, se queda sin entender por qué no se guardó.
    """
    import os

    nombre = os.path.basename(ruta) if ruta else "el archivo"
    if isinstance(exc, PermissionError):
        return (f"No se pudo guardar «{nombre}»: el archivo está abierto en otro "
                "programa (Excel, un visor de PDF…) o es de solo lectura. "
                "Ciérralo y vuelve a intentar, o guárdalo con otro nombre.")
    if isinstance(exc, OSError) and getattr(exc, "errno", None) == 28:
        return f"No se pudo guardar «{nombre}»: no hay espacio en el disco."
    return f"No se pudo guardar «{nombre}»: {exc}"
