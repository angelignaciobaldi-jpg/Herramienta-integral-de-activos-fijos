"""Pantalla "Generador de códigos QR".

Genera códigos QR (etiquetas imprimibles en PDF) para los activos de una empresa.
Cada QR codifica un enlace `URL_base/etiqueta`; al escanearlo, el PWA/API móvil
resuelve la etiqueta y muestra la información del activo. La URL base se configura
aquí (se guarda como preferencia) para poder apuntarla al PWA cuando esté publicado.

Flujo:
  1. Elegir la empresa y (opcional) fijar la URL base.
  2. "Descargar activos del SIPP": trae los activos de esa empresa (con su
     etiqueta) vía RPA/endpoint y los cachea.
  3. "Generar etiquetas (PDF)": arma la hoja de etiquetas con QR + datos y la
     exporta a PDF para imprimir y pegar.
"""

from __future__ import annotations

import asyncio

import flet as ft

from core import credenciales, db, preferencias
from core.empresas import ID_POR_EMPRESA, NOMBRES_EMPRESAS
from ui.comun import GRIS, NARANJA, ROJO, VERDE

_CLAVE_URL = "qr_base_url"


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
                ft.Row([self.dd_empresa, self.progreso], spacing=14,
                       vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Row(
                    [
                        ft.FilledButton("Descargar activos del SIPP",
                                        icon=ft.Icons.CLOUD_DOWNLOAD,
                                        on_click=self._descargar),
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

    def _actualizar_estado(self) -> None:
        idemp = self._empresa_id()
        if idemp is None:
            self.txt_estado.value = "Elige una empresa."
        else:
            n = len(db.listar_activos_sipp(idemp))
            self.txt_estado.value = (
                f"{n} activo(s) con etiqueta en caché para «{self.dd_empresa.value}»."
                if n else f"Sin activos descargados para «{self.dd_empresa.value}». "
                          "Usa «Descargar activos del SIPP».")
        self._safe_update()

    def _guardar_base(self, _e=None) -> None:
        preferencias.guardar_valor(_CLAVE_URL, (self.tf_base.value or "").strip())

    # ------------------------------------------------ descargar activos (RPA)
    async def _descargar(self, _e=None) -> None:
        creds = credenciales.cargar()
        if not creds or not creds[0]:
            self.app.avisar("Configura primero las credenciales del SIPP (botón ⚙).", ROJO)
            return
        usuario, contrasena = creds
        empresa = self.dd_empresa.value
        idemp = self._empresa_id()
        if idemp is None:
            self.app.avisar("Elige una empresa.", NARANJA)
            return

        from core.rpa_sipp import BucleRpa
        bucle = BucleRpa()
        dlg = ft.AlertDialog(
            modal=True, title=ft.Text("Descargando activos del SIPP"),
            content=ft.Container(
                ft.Row([ft.ProgressRing(width=26, height=26, stroke_width=3),
                        ft.Text(f"Consultando activos de «{empresa}»…\n"
                                "Se abrirá un navegador; no lo cierres.")],
                       spacing=16, tight=True),
                width=420))
        self.page.show_dialog(dlg)
        self.page.update()

        resultado, error = {}, None

        async def flujo() -> None:
            nonlocal resultado, error
            from core import activos_sipp
            from core.rpa_sipp import SesionSipp
            try:
                async with SesionSipp(headless=False) as sipp:
                    await sipp.login(usuario, contrasena)
                    await sipp.preparar_sesion_empresa(empresa)
                    resultado = await activos_sipp.descargar_activos(sipp, idemp, empresa)
            except Exception as exc:  # noqa: BLE001 — se reporta al usuario
                error = str(exc)

        try:
            await asyncio.wrap_future(bucle.enviar(flujo()))
        finally:
            bucle.cerrar()
            self.page.pop_dialog()
            self._actualizar_estado()

        if error:
            self.app.avisar(f"No se pudieron descargar los activos: {error}", ROJO,
                            duracion=9000)
        else:
            n = resultado.get("guardados", 0)
            color = VERDE if n else NARANJA
            self.app.avisar(
                f"{n} activo(s) con etiqueta descargados de «{empresa}»."
                + (" (La empresa no tiene activos con etiqueta.)" if not n else ""),
                color, duracion=7000)

    # ------------------------------------------------ generar PDF
    async def _generar_pdf(self, _e=None) -> None:
        idemp = self._empresa_id()
        if idemp is None:
            self.app.avisar("Elige una empresa.", NARANJA)
            return
        activos = db.listar_activos_sipp(idemp)
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
