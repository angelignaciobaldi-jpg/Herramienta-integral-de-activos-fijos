"""Diálogo de CARGA MASIVA de inventarios desde Excel.

Flujo: el usuario elige el archivo, la herramienta lo analiza y muestra qué hojas
puede importar y cuántos activos saldrían de cada una (ya expandidas las
etiquetas múltiples). El usuario marca las hojas y confirma; la importación corre
en un hilo para no congelar la interfaz.

La EMPRESA se toma del selector de la pantalla de Registro (el archivo no la
trae). La SUCURSAL y el DEPARTAMENTO se autollenan por fila desde las columnas
ORIGEN y AREA del archivo cuando existen (si no, se usan los del selector). El
tipo de activo se deja vacío para asignarlo después.
"""

from __future__ import annotations

import asyncio

import flet as ft

from core import importador_excel
from ui.comun import GRIS, NARANJA, ROJO, VERDE
from ui.componentes import boton_herramienta, boton_primario

_ANCHO = 640

# Campo interno detectado -> cómo se llena en el registro (para mostrarlo en el
# diálogo y que se vea qué se autollena desde el archivo). Se deduplican las
# etiquetas repetidas (ORIGEN y UBICACIÓN alimentan ambas la "Ubicación").
# OJO: AREA (departamento del Excel) NO se mapea: no corresponde al departamento
# del SIPP, que se elige del catálogo descargado.
_CAMPO_A_ETIQUETA = {
    "empresa": "Empresa", "sucursal": "Sucursal", "insumo": "Insumo",
    "etiqueta": "Etiqueta", "serie": "Serie", "responsable": "Responsable",
    "origen": "Ubicación", "ubicacion": "Ubicación",
}


class DialogoCargaMasiva:
    """Selección de hojas e importación de un Excel de inventario."""

    def __init__(self, app, contexto, al_terminar=None):
        """`contexto()` devuelve (empresa, sucursal, departamento) al momento de
        importar; `al_terminar()` se llama tras una importación exitosa."""
        self.app = app
        self.page = app.page
        self.contexto = contexto
        self.al_terminar = al_terminar
        self._ruta: str | None = None
        self._hojas: list = []
        self._checks: dict = {}
        self._construir()

    # ------------------------------------------------------------ UI
    def _construir(self) -> None:
        self._resumen = ft.Text("", size=12, color=GRIS)
        self._lista = ft.Column(spacing=6, scroll=ft.ScrollMode.AUTO, tight=True)
        self._progreso = ft.ProgressBar(visible=False)
        self._btn_importar = boton_primario(
            "Importar", ft.Icons.DOWNLOAD, self._importar, disabled=True)
        self.dialogo = ft.AlertDialog(
            modal=True,
            title=ft.Text("Carga masiva desde Excel", size=20, weight=ft.FontWeight.BOLD),
            content=ft.Container(
                ft.Column(
                    [self._resumen, ft.Divider(),
                     ft.Container(self._lista, height=300), self._progreso],
                    spacing=10, tight=True),
                width=_ANCHO),
            actions=[
                boton_herramienta("Cancelar",
                                  on_click=lambda _e: self.page.pop_dialog()),
                self._btn_importar,
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )

    # -------------------------------------------------------- apertura
    async def abrir(self, _e=None) -> None:
        """Pide el archivo, lo analiza y muestra las hojas disponibles."""
        archivos = await self.app.picker.pick_files(
            dialog_title="Selecciona el Excel de inventario",
            allowed_extensions=["xlsx", "xlsm"], allow_multiple=False)
        if not archivos:
            return
        self._ruta = archivos[0].path
        self._resumen.value = f"Analizando «{archivos[0].name}»…"
        self._lista.controls = []
        self._checks = {}
        self._btn_importar.disabled = True
        self.page.show_dialog(self.dialogo)
        self.page.update()

        try:
            self._hojas = await asyncio.to_thread(importador_excel.analizar, self._ruta)
        except Exception as exc:  # noqa: BLE001 — se reporta al usuario
            self._resumen.value = f"No se pudo leer el archivo: {exc}"
            self._resumen.color = ROJO
            self._safe_update()
            return
        self._pintar_hojas(archivos[0].name)

    def _pintar_hojas(self, nombre_archivo: str) -> None:
        importables = [h for h in self._hojas if h.importable]
        total = sum(h.activos_estimados for h in importables)
        self._resumen.value = (
            f"«{nombre_archivo}» — {len(importables)} hoja(s) importable(s), "
            f"{total} activo(s) en total. Marca las que quieras importar.")
        self._resumen.color = GRIS

        controles = []
        for h in self._hojas:
            if h.importable:
                chk = ft.Checkbox(value=True, on_change=lambda _e: self._recalcular())
                self._checks[h.nombre] = chk
                campos = ", ".join(dict.fromkeys(
                    _CAMPO_A_ETIQUETA[c] for c in _CAMPO_A_ETIQUETA if c in h.columnas))
                controles.append(ft.Row(
                    [chk,
                     ft.Column(
                         [ft.Text(h.nombre, size=13, weight=ft.FontWeight.W_500),
                          ft.Text(f"{h.filas_datos} fila(s) → {h.activos_estimados} activo(s)",
                                  size=11, color=GRIS),
                          ft.Text(f"Autollena: {campos}", size=11, color=VERDE)],
                         spacing=0, tight=True)],
                    spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER))
            else:
                controles.append(ft.Row(
                    [ft.Icon(ft.Icons.BLOCK, size=16, color=GRIS),
                     ft.Column(
                         [ft.Text(h.nombre, size=13, color=GRIS),
                          ft.Text(h.motivo, size=11, color=GRIS)],
                         spacing=0, tight=True)],
                    spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER))
        self._lista.controls = controles
        self._btn_importar.disabled = not importables
        self._safe_update()

    def _recalcular(self) -> None:
        """Actualiza el total según las hojas marcadas."""
        total = sum(h.activos_estimados for h in self._hojas
                    if h.importable and self._checks.get(h.nombre)
                    and self._checks[h.nombre].value)
        marcadas = sum(1 for c in self._checks.values() if c.value)
        self._resumen.value = (f"{marcadas} hoja(s) seleccionada(s) — "
                               f"{total} activo(s) a importar.")
        self._btn_importar.disabled = marcadas == 0
        self._safe_update()

    # ------------------------------------------------------- importación
    async def _importar(self, _e=None) -> None:
        seleccionadas = [n for n, c in self._checks.items() if c.value]
        if not seleccionadas or not self._ruta:
            return
        empresa, sucursal, departamento = self.contexto()
        self._progreso.visible = True
        self._btn_importar.disabled = True
        self._resumen.value = "Importando…"
        self._safe_update()
        try:
            res = await asyncio.to_thread(
                importador_excel.importar, self._ruta, seleccionadas,
                empresa, sucursal, departamento)
        except Exception as exc:  # noqa: BLE001 — se reporta al usuario
            self._progreso.visible = False
            self._btn_importar.disabled = False
            self._resumen.value = f"Error al importar: {exc}"
            self._resumen.color = ROJO
            self._safe_update()
            return
        self._progreso.visible = False
        self.page.pop_dialog()
        if callable(self.al_terminar):
            self.al_terminar()

        partes = [f"{res.agregados} activo(s) importado(s)"]
        if res.duplicados:
            partes.append(f"{res.duplicados} ya existían")
        if res.sin_etiqueta:
            partes.append(f"{res.sin_etiqueta} fila(s) sin etiqueta")
        color = VERDE if res.agregados else NARANJA
        self.app.avisar(" · ".join(partes) + ".", color, duracion=8000)

    def _safe_update(self) -> None:
        try:
            self.dialogo.update()
        except (RuntimeError, AssertionError, AttributeError):
            pass
