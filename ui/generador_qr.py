"""Pantalla "Generador de códigos QR".

Genera códigos QR (etiquetas imprimibles en PDF) para los activos de una empresa.
Cada QR codifica un enlace `URL_base/etiqueta`; al escanearlo, el PWA/API móvil
resuelve la etiqueta y muestra la información del activo. La URL base se configura
aquí (se guarda como preferencia) para poder apuntarla al PWA cuando esté publicado.

Flujo:
  1. Elegir la empresa y (opcional) fijar la URL base.
  2. "Actualizar información del SIPP": trae del SIPP los activos e insumos de esa
     empresa (y los empleados, global) y los cachea (ver ui/actualizar_sipp).
  3. "Generar etiquetas (PDF)": arma la hoja de etiquetas con QR + datos y la
     exporta a PDF para imprimir y pegar.
"""

from __future__ import annotations

import asyncio

import flet as ft

from core import db, preferencias
from core.empresas import ID_POR_EMPRESA, NOMBRES_EMPRESAS
from ui.comun import GRIS, NARANJA, ROJO, VERDE

_CLAVE_URL = "qr_base_url"
# DropdownM2 muestra el `key` de la opción (no el `text`), así que la opción
# "todas" necesita un key legible; se trata como "sin filtro".
_TODAS = "Todas las sucursales"


class SeccionGeneradorQR:
    """Descarga activos por empresa y genera etiquetas QR imprimibles."""

    def __init__(self, app):
        self.app = app
        self.page = app.page
        self._construir()

    def _construir(self) -> None:
        self.dd_empresa = ft.DropdownM2(
            label="Empresa", dense=True, width=320,
            options=[ft.dropdownm2.Option(key=n, text=n) for n in NOMBRES_EMPRESAS],
            on_change=lambda _e: (self._recargar_sucursales(), self._actualizar_estado()))
        self.dd_sucursal = ft.DropdownM2(
            label="Sucursal", dense=True, width=280,
            on_change=lambda _e: self._actualizar_estado())
        self.tf_base = ft.TextField(
            label="URL base del QR", dense=True, width=420,
            hint_text="https://activos.petroil.app/a/",
            value=preferencias.cargar_valor(_CLAVE_URL) or "",
            on_change=self._guardar_base)
        self.progreso = ft.ProgressRing(width=22, height=22, stroke_width=3, visible=False)
        self.txt_estado = ft.Text("", size=13, color=GRIS)

        self.contenido = ft.Column(
            [
                ft.Text("Genera etiquetas QR para los activos de una empresa. Cada QR "
                        "abre la ficha del activo en el PWA (URL base + etiqueta).",
                        size=13, color=GRIS),
                ft.Divider(),
                self.tf_base,
                ft.Text("El QR llevará: URL base + la etiqueta del activo.",
                        size=11, color=GRIS),
                ft.Row([self.dd_empresa, self.dd_sucursal, self.progreso], spacing=14,
                       vertical_alignment=ft.CrossAxisAlignment.CENTER, wrap=True),
                ft.Row(
                    [
                        ft.FilledButton("Actualizar información del SIPP",
                                        icon=ft.Icons.SYNC,
                                        tooltip="Descarga del SIPP, para la empresa "
                                                "elegida, sus activos e insumos, más el "
                                                "catálogo de empleados (global)",
                                        on_click=self._actualizar_sipp),
                        ft.OutlinedButton("Generar carpeta por departamento",
                                          icon=ft.Icons.FOLDER_ZIP,
                                          tooltip="Un PNG por activo (QR + etiqueta) en "
                                                  "subcarpetas por departamento",
                                          on_click=self._generar_carpeta),
                        ft.OutlinedButton("Generar etiquetas (PDF)",
                                          icon=ft.Icons.QR_CODE_2,
                                          on_click=self._generar_pdf),
                    ],
                    spacing=12, wrap=True),
                self.txt_estado,
            ],
            expand=True, spacing=14,
        )
        self._actualizar_estado()

    # ------------------------------------------------------ estado
    def _empresa_id(self) -> "int | None":
        return ID_POR_EMPRESA.get(self.dd_empresa.value) if self.dd_empresa.value else None

    def _sucursal_sel(self) -> str:
        """Sucursal elegida ('' = todas)."""
        val = self.dd_sucursal.value or ""
        return "" if val == _TODAS else val

    def _recargar_sucursales(self) -> None:
        """Rellena el combo de sucursal con las presentes en la empresa cacheada."""
        idemp = self._empresa_id()
        sucs = db.sucursales_activos_sipp(idemp) if idemp is not None else []
        # key == lo que muestra DropdownM2; _TODAS se interpreta como "sin filtro".
        self.dd_sucursal.options = (
            [ft.dropdownm2.Option(key=_TODAS, text=_TODAS)]
            + [ft.dropdownm2.Option(key=s, text=s) for s in sucs])
        self.dd_sucursal.value = _TODAS
        self._safe_update()

    def _actualizar_estado(self) -> None:
        idemp = self._empresa_id()
        if idemp is None:
            self.txt_estado.value = "Elige una empresa."
        else:
            n = len(db.listar_activos_sipp(idemp, self._sucursal_sel() or None))
            suf = (f" (sucursal «{self._sucursal_sel()}»)" if self._sucursal_sel()
                   else "")
            self.txt_estado.value = (
                f"{n} activo(s) con etiqueta en caché para «{self.dd_empresa.value}»{suf}."
                if n else f"Sin activos descargados para «{self.dd_empresa.value}». "
                          "Usa «Actualizar información del SIPP».")
        self._safe_update()

    def _guardar_base(self, _e=None) -> None:
        preferencias.guardar_valor(_CLAVE_URL, (self.tf_base.value or "").strip())

    # ------------------------------------------------ actualizar SIPP (RPA)
    async def _actualizar_sipp(self, _e=None) -> None:
        """Actualiza en una sola sesión la información del SIPP de la empresa
        elegida (activos + insumos) y los empleados (global). Al terminar recarga
        las sucursales y el estado (los activos alimentan el combo de sucursal)."""
        from ui.actualizar_sipp import actualizar_info_sipp

        def _tras() -> None:
            self._recargar_sucursales()
            self._actualizar_estado()

        await actualizar_info_sipp(
            self.app, self._empresa_id(), self.dd_empresa.value, al_terminar=_tras)

    # ------------------------------------------------ generar carpeta por depto
    async def _generar_carpeta(self, _e=None) -> None:
        """Genera un PNG por activo (QR + etiqueta) en subcarpetas por departamento."""
        idemp = self._empresa_id()
        if idemp is None:
            self.app.avisar("Elige una empresa.", NARANJA)
            return
        activos = db.listar_activos_sipp(idemp, self._sucursal_sel() or None)
        if not activos:
            self.app.avisar("No hay activos para esa empresa/sucursal. "
                            "Descárgalos primero.", NARANJA)
            return
        carpeta = await self.app.picker.get_directory_path(
            dialog_title="Elige dónde crear las carpetas de etiquetas QR")
        if not carpeta:
            return
        import os
        sub = self._sucursal_sel() or self.dd_empresa.value
        raiz = os.path.join(carpeta, f"Etiquetas QR - {sub}")

        ui_loop = asyncio.get_running_loop()
        txt = ft.Text(f"Generando {len(activos)} etiqueta(s)…", size=13)
        barra = ft.ProgressBar(value=0)
        dlg = ft.AlertDialog(
            modal=True, title=ft.Text("Generando carpeta de etiquetas"),
            content=ft.Container(ft.Column([txt, barra], tight=True, spacing=12),
                                 width=420))
        self.page.show_dialog(dlg)
        self.page.update()

        def avance(hechos: int, total: int) -> None:
            def aplicar() -> None:
                txt.value = f"Generando etiquetas… {hechos}/{total}"
                barra.value = hechos / total if total else None
                try:
                    dlg.update()
                except (RuntimeError, AssertionError):
                    pass
            ui_loop.call_soon_threadsafe(aplicar)

        base = (self.tf_base.value or "").strip()
        from core import qr
        try:
            res = await asyncio.to_thread(
                qr.generar_carpeta_por_departamento, activos, raiz, base, avance)
        except Exception as exc:  # noqa: BLE001 — se reporta al usuario
            self.page.pop_dialog()
            self.app.avisar(f"No se pudo generar: {exc}", ROJO)
            return
        self.page.pop_dialog()
        self.app.avisar(
            f"{res['generados']} etiqueta(s) en {res['departamentos']} carpeta(s) "
            f"por departamento.", VERDE,
            accion="Abrir carpeta", on_accion=lambda _e: self.app.abrir_en_sistema(raiz),
            duracion=8000)

    # ------------------------------------------------ generar PDF
    async def _generar_pdf(self, _e=None) -> None:
        idemp = self._empresa_id()
        if idemp is None:
            self.app.avisar("Elige una empresa.", NARANJA)
            return
        activos = db.listar_activos_sipp(idemp, self._sucursal_sel() or None)
        if not activos:
            self.app.avisar("No hay activos descargados para esa empresa. "
                            "Descárgalos primero.", NARANJA)
            return
        destino = await self.app.picker.save_file(
            dialog_title="Guardar etiquetas QR",
            file_name=f"Etiquetas QR {self.dd_empresa.value}.pdf",
            allowed_extensions=["pdf"])
        if not destino:
            return
        ruta = destino if destino.lower().endswith(".pdf") else destino + ".pdf"

        self.progreso.visible = True
        self._safe_update()
        base = (self.tf_base.value or "").strip()
        # El PDF se genera con Chromium (Playwright), que en Windows necesita el
        # loop Proactor de BucleRpa para lanzar el subproceso del navegador.
        from core import qr
        from core.rpa_sipp import BucleRpa
        bucle = BucleRpa()
        try:
            n = await asyncio.wrap_future(
                bucle.enviar(qr.generar_pdf_etiquetas(activos, ruta, base)))
        except Exception as exc:  # noqa: BLE001 — se reporta al usuario
            self.app.avisar(f"No se pudo generar el PDF: {exc}", ROJO)
            return
        finally:
            bucle.cerrar()
            self.progreso.visible = False
            self._safe_update()
        self.app.avisar(
            f"{n} etiqueta(s) generadas.", VERDE,
            accion="Abrir", on_accion=lambda _e: self.app.abrir_en_sistema(ruta),
            duracion=7000)

    # ------------------------------------------------ utilidades
    def _on_resize(self, _e=None) -> None:
        """Contenido fluido; no requiere recomputar."""

    def _safe_update(self) -> None:
        try:
            self.contenido.update()
        except (RuntimeError, AssertionError, AttributeError):
            pass
