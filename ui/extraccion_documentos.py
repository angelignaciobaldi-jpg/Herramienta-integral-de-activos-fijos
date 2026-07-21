"""Pantalla "Extracción de documentos" (OCR).

Carga un documento (factura, resguardo, acta) en PDF o imagen y extrae su texto
con core/ocr.py (estrategia híbrida: capa de texto del PDF o Tesseract). El texto
extraído se muestra para revisión y sirve de base para poblar los campos de un
activo.

De aquí en adelante, agrega los EXTRACTORES de dominio (regex/heurísticas que
saquen núm. de inventario, descripción, importe, fecha, etc.) para autollenar el
registro de activos.
"""

from __future__ import annotations

import asyncio

import flet as ft

from core import ocr
from ui.comun import EXTENSIONES, GRIS, ROJO, VERDE


class SeccionExtraccionDocumentos:
    """Carga un documento, extrae su texto por OCR y lo muestra."""

    def __init__(self, app):
        self.app = app
        self.page = app.page
        self._construir()

    def _construir(self) -> None:
        self.txt_archivo = ft.Text("Ningún documento seleccionado.", size=13, color=GRIS)
        self.progreso = ft.ProgressRing(width=22, height=22, stroke_width=3, visible=False)
        self.salida = ft.TextField(
            label="Texto extraído", multiline=True, min_lines=14, max_lines=14,
            read_only=True, expand=True)

        barra = ft.Row(
            [
                ft.FilledButton("Seleccionar documento", icon=ft.Icons.UPLOAD_FILE,
                                on_click=self._seleccionar),
                self.progreso,
                self.txt_archivo,
            ],
            spacing=14, vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        self.contenido = ft.Column(
            [
                ft.Text("Extracción de documentos", size=20, weight=ft.FontWeight.BOLD),
                ft.Text("Formatos: PDF e imágenes (PNG, JPG, TIFF, BMP). Los "
                        "documentos se procesan localmente; no salen del equipo.",
                        size=13, color=GRIS),
                ft.Divider(),
                barra,
                self.salida,
            ],
            expand=True, spacing=12,
        )

    async def _seleccionar(self, _e=None) -> None:
        archivos = await self.app.picker.pick_files(
            dialog_title="Selecciona un documento a procesar",
            allowed_extensions=EXTENSIONES, allow_multiple=False)
        if not archivos:
            return
        ruta = archivos[0].path
        self.txt_archivo.value = archivos[0].name
        self.txt_archivo.color = GRIS
        self.salida.value = ""
        self.progreso.visible = True
        self._safe_update()
        try:
            texto, uso_ocr = await asyncio.to_thread(ocr.extraer_texto, ruta)
        except ocr.OCRNoDisponible as exc:
            self._error(str(exc))
            return
        except Exception as exc:  # noqa: BLE001 — se reporta al usuario
            self._error(f"No se pudo procesar el documento: {exc}")
            return
        finally:
            self.progreso.visible = False
            self._safe_update()
        self.salida.value = texto
        origen = "OCR (documento escaneado)" if uso_ocr else "capa de texto del PDF"
        self.txt_archivo.value = f"{archivos[0].name} · leído por {origen}"
        self.txt_archivo.color = VERDE
        self._safe_update()
        self.app.avisar("Texto extraído correctamente.", VERDE)

    def _error(self, mensaje: str) -> None:
        self.progreso.visible = False
        self.txt_archivo.value = mensaje
        self.txt_archivo.color = ROJO
        self._safe_update()
        self.app.avisar(mensaje, ROJO)

    def _on_resize(self, _e=None) -> None:
        """Contenido fluido; no requiere recomputar. Presente por consistencia."""

    def _safe_update(self) -> None:
        try:
            self.contenido.update()
        except (RuntimeError, AssertionError, AttributeError):
            pass
