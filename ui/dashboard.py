"""Pantalla "Dashboard activos fijos".

EJEMPLO de disposición del tablero, adaptado del mockup en `ejemplos/code.html`.
Las tarjetas (ui/tarjetas.py) se colocan en la rejilla bento (ui/rejilla.py)
declarando cuántas columnas y filas abarca cada una.

Estado: los VALORES son de ejemplo (`_EJEMPLO`), no salen de la base. El cableado
a datos reales va en `cargar_desde_db`, que ya está listo para recibirlos.
"""

from __future__ import annotations

import flet as ft

from ui.rejilla import Bloque, Rejilla
from ui.tarjetas import TarjetaKpi, TarjetaMetrica, TarjetaVacia

# Valores de muestra tomados del mockup. Sustituir por consultas a core.db
# (p. ej. db.contar_levantamiento()) al cablear el tablero.
_EJEMPLO = {
    "activos": "1,248",
    "variacion": "+12%",
    "nota": "desde el último mes",
    "valor_total": "$4.2M USD",
    "mantenimientos": "24 activos",
    "operadores": "86 activos",
}


class SeccionDashboard:
    """Tablero de activos fijos."""

    def __init__(self, app):
        self.app = app
        self.page = app.page
        self._construir()

    def _construir(self) -> None:
        encabezado = ft.Column(
            [
                ft.Text("Panel de Control de Activos",
                        theme_style=ft.TextThemeStyle.HEADLINE_LARGE,
                        color=ft.Colors.ON_SURFACE),
                ft.Text("Visión general y métricas operativas.",
                        theme_style=ft.TextThemeStyle.BODY_LARGE,
                        color=ft.Colors.ON_SURFACE_VARIANT),
            ],
            spacing=2, tight=True,
        )

        # --- Tarjetas -----------------------------------------------------
        self.tar_activos = TarjetaMetrica(
            "Activos Registrados", _EJEMPLO["activos"], ft.Icons.INVENTORY_2,
            variacion=_EJEMPLO["variacion"], nota=_EJEMPLO["nota"],
            accion="Ver detalles", on_accion=self._ir_a_registro)

        self.tar_valor = TarjetaKpi(
            "Valor Total Estimado", _EJEMPLO["valor_total"], ft.Icons.PAYMENTS,
            color_icono=ft.Colors.ON_TERTIARY_FIXED_VARIANT,
            fondo_icono=ft.Colors.TERTIARY_FIXED)

        # La MISMA clase con otro par de colores y otra caja: es justamente el
        # punto de que las tarjetas no dependan del tamaño que les toque.
        self.tar_mantenimientos = TarjetaKpi(
            "Mantenimientos Pendientes", _EJEMPLO["mantenimientos"],
            ft.Icons.WARNING, color_icono=ft.Colors.ERROR,
            fondo_icono=ft.Colors.ERROR_CONTAINER)

        self.tar_operadores = TarjetaKpi(
            "Operadores", _EJEMPLO["operadores"], ft.Icons.GROUP,
            color_icono=ft.Colors.ON_SECONDARY_FIXED_VARIANT,
            fondo_icono=ft.Colors.SECONDARY_FIXED)

        # --- Rejilla ------------------------------------------------------
        # 24 columnas: el cuadrante es la MITAD del de 12, para poder afinar
        # tamaños en medios pasos. `alto_fila=28` no es arbitrario: con
        # `espacio=16` el paso vertical queda en 44px, justo la mitad de los 88px
        # que daba `alto_fila=72`, así la subdivisión es exacta en ambos ejes.
        self.rejilla = Rejilla(columnas=24, alto_fila=28, espacio=16)
        self.rejilla.agregar_todos([
            Bloque(self.tar_activos.control,        col=0,  fila=0, ancho=8, alto=4),
            Bloque(self.tar_mantenimientos.control, col=8,  fila=0, ancho=8, alto=2),
            Bloque(self.tar_valor.control,          col=16, fila=0, ancho=8, alto=2),
            Bloque(self.tar_operadores.control,     col=8,  fila=2, ancho=8, alto=2),
        ])

        self.contenido = ft.Column(
            [encabezado, self.rejilla.contenido], expand=True, spacing=24)

    # ------------------------------------------------------------ acciones
    def _ir_a_registro(self, _e=None) -> None:
        """Salta a la pantalla de Registro (índice 1 de la navegación)."""
        seleccionar = getattr(self.app, "_seleccionar_nav", None)
        if callable(seleccionar):
            seleccionar(1)

    # -------------------------------------------------------------- datos
    def cargar_desde_db(self) -> None:
        """Punto de cableado a datos reales (hoy el tablero es un ejemplo)."""

    def _on_resize(self, _e=None) -> None:
        """La rejilla y las tarjetas se remiden solas con `on_size_change`."""

    def _safe_update(self) -> None:
        try:
            self.contenido.update()
        except (RuntimeError, AssertionError, AttributeError):
            pass
