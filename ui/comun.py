"""Constantes y utilidades compartidas por las pantallas de la interfaz.

Centralizar esto evita duplicación y permite que cada pantalla viva en su propio
archivo (para trabajar en colaboración sin pisarse). Cada pantalla importa de
aquí sus colores, helpers y el catálogo de empresas.
"""

from __future__ import annotations

import re
from datetime import datetime

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


# --- Fechas --------------------------------------------------------------
# Formato ÚNICO de fecha en toda la app (México). Si algún día cambia, se
# cambia aquí y en CampoFecha.
FORMATO_FECHA = "%d/%m/%Y"
_FECHA_MIN = datetime(1990, 1, 1)
_FECHA_MAX = datetime(2100, 12, 31)


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


class CampoFecha:
    """Campo de fecha con SELECCIÓN POR CALENDARIO (estándar del proyecto).

    Estándar acordado: TODAS las fechas de la app se capturan con calendario, no
    tecleadas. Este componente encapsula ese comportamiento para reusarlo:

        f = CampoFecha(page, "Fecha de adquisición", valor="01/07/2026")
        columna.controls.append(f.control)     # se coloca en la UI
        ...                                     # más tarde:
        texto = f.value                         # 'DD/MM/AAAA' (o '')

    Visualmente es un campo de solo lectura (no se puede teclear) con un ícono de
    calendario; al pulsarlo abre el DatePicker de Material en español. Expone
    `.value` (str 'DD/MM/AAAA') igual que un TextField, para que el código que lo
    consume no necesite un caso especial.
    """

    def __init__(self, page, label: str, valor: str = "", on_change=None,
                 dense: bool = True):
        self.page = page
        self._on_change = on_change
        self._campo = ft.TextField(
            label=label, value=valor or "", read_only=True, dense=dense,
            hint_text="DD/MM/AAAA",
            suffix=ft.IconButton(
                icon=ft.Icons.CALENDAR_MONTH, icon_size=20,
                tooltip="Elegir fecha", on_click=self._abrir))

    @property
    def control(self) -> ft.Control:
        return self._campo

    @property
    def value(self) -> str:
        return self._campo.value or ""

    @value.setter
    def value(self, v: str) -> None:
        self._campo.value = v or ""

    def _abrir(self, _e=None) -> None:
        selector = ft.DatePicker(
            value=parse_fecha(self._campo.value),
            first_date=_FECHA_MIN, last_date=_FECHA_MAX,
            locale=ft.Locale("es", "MX"),
            help_text="Selecciona la fecha", cancel_text="Cancelar",
            confirm_text="Aceptar", on_change=self._elegido)
        self.page.show_dialog(selector)

    def _elegido(self, e) -> None:
        fecha = getattr(e.control, "value", None)
        if not fecha:
            return
        self._campo.value = fmt_fecha(fecha)
        try:
            self._campo.update()
        except (RuntimeError, AssertionError):
            pass
        if callable(self._on_change):
            self._on_change(self._campo.value)
