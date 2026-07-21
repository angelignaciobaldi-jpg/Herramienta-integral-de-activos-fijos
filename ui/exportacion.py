"""Pantalla "Exportación / Reportes".

Exporta la base de activos (core/db.py) a Excel o CSV mediante core/exportador.py.
Es la base del módulo de reportes; amplíala con plantillas concretas (resguardos
en PDF, reportes filtrados por empresa/categoría, etc.) según lo defina el área.
"""

from __future__ import annotations

import asyncio

import flet as ft

from core import db, exportador
from ui.comun import GRIS, ROJO, VERDE


class SeccionExportacion:
    """Exporta los activos registrados a Excel o CSV."""

    def __init__(self, app):
        self.app = app
        self.page = app.page
        self._construir()

    def _construir(self) -> None:
        self.txt_conteo = ft.Text(size=13, color=GRIS)
        self.progreso = ft.ProgressRing(width=22, height=22, stroke_width=3, visible=False)
        self._actualizar_conteo()

        self.contenido = ft.Column(
            [
                ft.Text("Exportación / Reportes", size=20, weight=ft.FontWeight.BOLD),
                ft.Text("Genera un archivo con todos los activos registrados.",
                        size=13, color=GRIS),
                ft.Divider(),
                self.txt_conteo,
                ft.Row(
                    [
                        ft.FilledButton("Exportar a Excel", icon=ft.Icons.TABLE_VIEW,
                                        on_click=lambda _e: self._exportar("xlsx")),
                        ft.OutlinedButton("Exportar a CSV", icon=ft.Icons.DESCRIPTION,
                                          on_click=lambda _e: self._exportar("csv")),
                        self.progreso,
                    ],
                    spacing=14, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ],
            expand=True, spacing=12,
        )

    def _actualizar_conteo(self) -> None:
        n = len(db.listar())
        self.txt_conteo.value = (
            "No hay activos que exportar todavía." if n == 0
            else f"{n} activo(s) listos para exportar.")

    async def _exportar(self, formato: str) -> None:
        self._actualizar_conteo()
        if not db.listar():
            self.app.avisar("No hay activos que exportar.", ROJO)
            return
        nombre = f"Activos fijos.{formato}"
        destino = await self.app.picker.save_file(
            dialog_title="Guardar exportación",
            file_name=nombre, allowed_extensions=[formato])
        if not destino:
            return
        ruta = destino if destino.lower().endswith("." + formato) else destino + "." + formato

        self.progreso.visible = True
        self._safe_update()
        try:
            fn = exportador.exportar_excel if formato == "xlsx" else exportador.exportar_csv
            filas = await asyncio.to_thread(fn, ruta)
        except Exception as exc:  # noqa: BLE001 — se reporta al usuario
            self.app.avisar(f"No se pudo exportar: {exc}", ROJO)
            return
        finally:
            self.progreso.visible = False
            self._safe_update()
        self.app.avisar(
            f"Exportados {filas} activo(s).", VERDE,
            accion="Abrir", on_accion=lambda _e: self.app.abrir_en_sistema(ruta),
            duracion=6000)

    def _on_resize(self, _e=None) -> None:
        """Contenido fluido; no requiere recomputar. Presente por consistencia."""

    def _safe_update(self) -> None:
        try:
            self.contenido.update()
        except (RuntimeError, AssertionError, AttributeError):
            pass
