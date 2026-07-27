"""Selector de empleado del catálogo del SIPP (búsqueda en la caché local).

El empleado de resguardo se elige aquí (por nombre o id) y el RPA lo selecciona
por su ID exacto en el modal "Buscar Empleado" del alta. Se apoya en
core/db.buscar_empleados (caché descargada con core/empleados).
"""

from __future__ import annotations

import flet as ft

from core import db
from ui.comun import GRIS, NARANJA, VERDE

_ANCHO = 620


class DialogoSelectorEmpleado:
    """Diálogo de búsqueda/selección de un empleado del catálogo cacheado."""

    def __init__(self, app, al_elegir):
        """`al_elegir(id_empleado, nombre)` se llama cuando el usuario elige uno."""
        self.app = app
        self.page = app.page
        self.al_elegir = al_elegir
        self._construir()

    def _construir(self) -> None:
        self.tf = ft.TextField(
            hint_text="Buscar por nombre o id de empleado… (Enter)",
            dense=True, prefix_icon=ft.Icons.SEARCH, autofocus=True,
            on_submit=self._buscar, expand=True)
        self.lista = ft.Column(spacing=4, scroll=ft.ScrollMode.AUTO, tight=True)
        self.estado = ft.Text("", size=12, color=GRIS)
        self.dialogo = ft.AlertDialog(
            modal=True,
            title=ft.Text("Buscar empleado (resguardo)", size=18,
                          weight=ft.FontWeight.BOLD),
            content=ft.Container(
                ft.Column([self.tf, self.estado, ft.Container(self.lista, height=320)],
                          spacing=10, tight=True),
                width=_ANCHO),
            actions=[ft.TextButton("Cerrar", on_click=lambda _e: self.page.pop_dialog())],
        )

    def abrir(self, sugerido: str = "") -> None:
        self.tf.value = sugerido or ""
        if not db.buscar_empleados("", limite=1):
            self.estado.value = ("El catálogo de empleados está vacío. Usa «Actualizar "
                                 "catálogos» para descargarlo del SIPP.")
            self.estado.color = NARANJA
            self.lista.controls = []
        else:
            self._buscar()
        self.page.show_dialog(self.dialogo)

    def _buscar(self, _e=None) -> None:
        texto = (self.tf.value or "").strip()
        resultados = db.buscar_empleados(texto, limite=100)
        self.estado.value = (f"{len(resultados)} resultado(s)"
                             + (" (mostrando 100)" if len(resultados) == 100 else ""))
        self.estado.color = GRIS
        self.lista.controls = [self._fila(e) for e in resultados]
        self._safe_update()

    def _fila(self, emp: "db.Empleado") -> ft.Control:
        return ft.Container(
            content=ft.Row(
                [
                    ft.Container(ft.Text(str(emp.id_empleado), size=12,
                                         weight=ft.FontWeight.BOLD), width=64),
                    ft.Column(
                        [ft.Text(emp.nombre, size=13, no_wrap=True),
                         ft.Text(emp.puesto or "—", size=11, color=GRIS, no_wrap=True)],
                        spacing=0, expand=True, tight=True),
                    ft.FilledTonalButton("Elegir",
                                         on_click=lambda _e, x=emp: self._elegir(x)),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=8),
            padding=ft.Padding.symmetric(horizontal=10, vertical=6),
            border=ft.Border(bottom=ft.BorderSide(
                1, ft.Colors.with_opacity(0.4, ft.Colors.OUTLINE_VARIANT))))

    def _elegir(self, emp: "db.Empleado") -> None:
        self.page.pop_dialog()
        if callable(self.al_elegir):
            self.al_elegir(emp.id_empleado, emp.nombre)
        self.app.avisar(f"Empleado elegido: {emp.nombre}", VERDE)

    def _safe_update(self) -> None:
        try:
            self.dialogo.update()
        except (RuntimeError, AssertionError, AttributeError):
            pass
