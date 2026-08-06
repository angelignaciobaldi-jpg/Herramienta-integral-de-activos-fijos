"""Diálogo de CARGA MASIVA de inventarios desde Excel.

El modal abre con dos acciones: DESCARGAR la plantilla (para que el usuario la
llene) y SUBIR el Excel ya lleno. Al subir, la herramienta analiza el archivo,
muestra qué hojas puede importar y cuántos activos saldrían de cada una (ya
expandidas las etiquetas múltiples); el usuario marca las hojas y confirma. La
importación corre en un hilo para no congelar la interfaz.

La EMPRESA se toma del selector de la pantalla de Registro (o de la columna del
archivo). La SUCURSAL se autollena por fila desde el archivo cuando existe. El
TIPO de activo se deja vacío para asignarlo después (y luego correr el RPA de alta).
"""

from __future__ import annotations

import asyncio

import flet as ft

from core import importador_excel
from ui.comun import GRIS, NARANJA, ROJO, VERDE
from ui.componentes import boton_herramienta, boton_primario, boton_secundario

_ANCHO = 640

# Campo interno detectado -> cómo se llena en el registro (para mostrar qué se
# autollena desde el archivo). Se deduplican las etiquetas repetidas.
# OJO: AREA (departamento del Excel) NO se mapea: no corresponde al departamento
# del SIPP, que se elige del catálogo descargado.
_CAMPO_A_ETIQUETA = {
    "empresa": "Empresa", "sucursal": "Sucursal", "insumo": "Insumo",
    "etiqueta": "Etiqueta", "serie": "Serie", "responsable": "Responsable",
    "origen": "Ubicación", "ubicacion": "Ubicación",
    # Campos del alta (plantilla completa).
    "id_TipoActivo": "Tipo", "de_DescripcionActivo": "Descripción",
    "id_Situacion": "Situación", "im_Costo": "Costo", "nb_Factura": "Factura",
    "nb_Proveedor": "Proveedor", "id_EmpresaAgregar": "Empresa compra",
    "id_SucursalAgregar": "Sucursal compra", "id_GrupoCentroCosto": "Grupo CC",
    "id_CentroCosto": "Centro de costo", "id_Departamento": "Departamento",
    "FH_ADQUISICION": "F. adquisición", "FH_GARANTIA": "F. garantía",
    "FH_ASIGNACION": "F. asignación", "marca": "Marca", "modelo": "Modelo",
    "cliente": "Cliente", "placa": "Placa",
}


class DialogoCargaMasiva:
    """Modal de carga masiva: descargar plantilla e importar un Excel de inventario."""

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
        # Controles de la vista de análisis (se rellenan al subir el archivo).
        self._resumen = ft.Text("", size=12, color=GRIS)
        self._lista = ft.Column(spacing=6, scroll=ft.ScrollMode.AUTO, tight=True)
        self._progreso = ft.ProgressBar(visible=False)
        self._btn_importar = boton_primario(
            "Importar", ft.Icons.DOWNLOAD, self._importar, disabled=True)
        self.dialogo = ft.AlertDialog(
            modal=True,
            title=ft.Text("Carga masiva desde Excel", size=20, weight=ft.FontWeight.BOLD),
            content=ft.Container(width=_ANCHO),
            actions=[],
            actions_alignment=ft.MainAxisAlignment.END,
        )

    def _panel_inicio(self) -> ft.Control:
        """Vista inicial del modal: descargar plantilla o subir el Excel lleno."""
        return ft.Container(
            ft.Column(
                [
                    ft.Text("Registra varios activos a la vez desde un Excel.",
                            size=13, weight=ft.FontWeight.W_600),
                    ft.Text("1) Descarga la plantilla y llénala (un activo por fila). "
                            "2) Súbela aquí para registrarlos en la herramienta.",
                            size=12, color=GRIS),
                    ft.Divider(),
                    ft.Row(
                        [boton_secundario("Descargar plantilla", ft.Icons.FILE_DOWNLOAD,
                                          self._descargar_plantilla),
                         boton_primario("Subir Excel lleno", ft.Icons.UPLOAD_FILE,
                                        self._elegir_archivo)],
                        spacing=12, wrap=True),
                    ft.Text("Después de importar, asigna el tipo de activo a cada uno "
                            "y usa «Registrar en SIPP» para el alta automática (RPA).",
                            size=11, color=GRIS),
                ],
                spacing=12, tight=True),
            width=_ANCHO)

    def _mostrar_inicio(self, _e=None) -> None:
        self.dialogo.content = self._panel_inicio()
        self.dialogo.actions = [
            boton_herramienta("Cerrar", on_click=lambda _e: self.page.pop_dialog())]
        self._safe_update()

    def _mostrar_analisis(self) -> None:
        self.dialogo.content = ft.Container(
            ft.Column(
                [self._resumen, ft.Divider(),
                 ft.Container(self._lista, height=300), self._progreso],
                spacing=10, tight=True),
            width=_ANCHO)
        self.dialogo.actions = [
            boton_herramienta("Volver", on_click=self._mostrar_inicio),
            self._btn_importar,
        ]
        self._safe_update()

    # -------------------------------------------------------- apertura
    async def abrir(self, _e=None) -> None:
        """Abre el modal en su vista inicial (descargar plantilla / subir Excel)."""
        self._ruta = None
        self._hojas = []
        self._checks = {}
        self._mostrar_inicio()
        self.page.show_dialog(self.dialogo)
        self.page.update()

    # --------------------------------------------------- descargar plantilla
    async def _descargar_plantilla(self, _e=None) -> None:
        destino = await self.app.picker.save_file(
            dialog_title="Guardar plantilla de carga masiva",
            file_name="Plantilla carga masiva.xlsx", allowed_extensions=["xlsx"])
        if not destino:
            return
        ruta = destino if destino.lower().endswith(".xlsx") else destino + ".xlsx"
        try:
            await asyncio.to_thread(importador_excel.generar_plantilla, ruta)
        except Exception as exc:  # noqa: BLE001 — se reporta al usuario
            self.app.avisar(f"No se pudo generar la plantilla: {exc}", ROJO)
            return
        self.app.avisar(
            "Plantilla descargada. Llénala y súbela aquí.", VERDE, accion="Abrir",
            on_accion=lambda _e, x=ruta: self.app.abrir_en_sistema(x), duracion=8000)

    # ------------------------------------------------------ subir / analizar
    async def _elegir_archivo(self, _e=None) -> None:
        """Pide el Excel lleno, lo analiza y muestra las hojas disponibles."""
        archivos = await self.app.picker.pick_files(
            dialog_title="Selecciona el Excel de inventario",
            allowed_extensions=["xlsx", "xlsm"], allow_multiple=False)
        if not archivos:
            return
        self._ruta = archivos[0].path
        self._resumen.value = f"Analizando «{archivos[0].name}»…"
        self._resumen.color = GRIS
        self._lista.controls = []
        self._checks = {}
        self._btn_importar.disabled = True
        self._mostrar_analisis()

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
