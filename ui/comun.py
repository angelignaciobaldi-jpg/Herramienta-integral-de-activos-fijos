"""Constantes y utilidades compartidas por las pantallas de la interfaz.

Centralizar esto evita duplicación y permite que cada pantalla viva en su propio
archivo (para trabajar en colaboración sin pisarse). Cada pantalla importa de
aquí sus colores, helpers y el catálogo de empresas.
"""

from __future__ import annotations

import re

import flet as ft

# El catálogo de empresas del Grupo Petroil es la fuente única (core/empresas.py).
# Se re-exporta aquí para que las pantallas lo tomen desde un solo lugar.
from core.empresas import EMPRESAS, ID_POR_EMPRESA, NOMBRES_EMPRESAS  # noqa: F401

# --- Validaciones / formatos ---------------------------------------------
RE_EMAIL = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
EXTENSIONES = ["pdf", "png", "jpg", "jpeg", "tif", "tiff", "bmp"]

# --- Colores -------------------------------------------------------------
VERDE = ft.Colors.GREEN_700
ROJO = ft.Colors.RED_700
NARANJA = ft.Colors.ORANGE_800
GRIS = ft.Colors.ON_SURFACE_VARIANT
# ERROR es un ROL de tema (Material lo adapta a un rojo legible en claro y oscuro);
# se usa para el foreground de botones/íconos de acción destructiva.
ROJO_BOTON = ft.Colors.ERROR

CENTRO = ft.Alignment(0, 0)


# --- Helpers de UI -------------------------------------------------------
def celda_centrada(contenido: ft.Control, ancho: int) -> ft.Container:
    return ft.Container(content=contenido, width=ancho, alignment=CENTRO)


def encabezado_col(titulo: str, ancho: int) -> ft.Container:
    return ft.Container(
        content=ft.Text(titulo, weight=ft.FontWeight.BOLD, size=13,
                        text_align=ft.TextAlign.CENTER),
        width=ancho, alignment=CENTRO,
    )


def tarjeta(titulo: str, cuerpo: ft.Control) -> ft.Card:
    return ft.Card(
        content=ft.Container(
            content=ft.Column(
                [ft.Text(titulo, weight=ft.FontWeight.BOLD, size=15), cuerpo],
                spacing=10,
            ),
            padding=16,
        )
    )


def placeholder(titulo: str, descripcion: str, icono) -> ft.Control:
    """Contenido provisional centrado para las pantallas aún sin lógica de negocio.
    Sirve para validar la navegación modular antes de implementar cada módulo."""
    return ft.Container(
        content=ft.Column(
            [
                ft.Icon(icono, size=52, color=GRIS),
                ft.Text(titulo, size=20, weight=ft.FontWeight.BOLD,
                        text_align=ft.TextAlign.CENTER),
                ft.Text(descripcion, size=14, color=GRIS,
                        text_align=ft.TextAlign.CENTER),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=12,
        ),
        alignment=CENTRO, expand=True, padding=24,
    )


# --- Helpers de datos ----------------------------------------------------
def solo_digitos(texto: str | None) -> str:
    return re.sub(r"\D", "", texto or "")


def parse_monto(texto: str | None) -> float | None:
    """Convierte el texto del monto a número. Vacío -> None. Lanza ValueError si
    no es un número válido o es negativo."""
    s = (texto or "").strip().replace("$", "").replace(",", "").replace(" ", "")
    if not s:
        return None
    valor = float(s)
    if valor < 0:
        raise ValueError("El monto no puede ser negativo.")
    return valor


def fmt_monto(monto: float | None) -> str:
    return "" if monto is None else f"{monto:,.2f}"
