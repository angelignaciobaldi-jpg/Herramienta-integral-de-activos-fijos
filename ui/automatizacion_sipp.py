"""Pantalla "Automatización SIPP" (RPA).

Reutiliza la sesión base del portal (core/rpa_sipp.SesionSipp): abre el navegador,
inicia sesión con las credenciales guardadas (Configuración ⚙) y selecciona
empresa/sucursal. Sirve de punto de partida y de prueba de conectividad del RPA.

Sobre esta base se agregan los flujos concretos de activos fijos (consultas,
descargas de anexos, capturas), llamando a los métodos de SesionSipp y añadiendo
los pasos específicos de cada pantalla del SIPP.
"""

from __future__ import annotations

import flet as ft

from core import credenciales
from core.rpa_sipp import ErrorSipp, SesionSipp
from ui.comun import GRIS, NOMBRES_EMPRESAS, ROJO, VERDE


class SeccionAutomatizacionSipp:
    """Prueba/base del RPA del SIPP (login + selección de empresa)."""

    def __init__(self, app):
        self.app = app
        self.page = app.page
        self._construir()

    def _construir(self) -> None:
        self.dd_empresa = ft.Dropdown(
            label="Empresa", dense=True, width=360,
            options=[ft.dropdown.Option(n) for n in NOMBRES_EMPRESAS])
        self.tf_sucursal = ft.TextField(
            label="Sucursal", dense=True, width=360, hint_text="Corporativo")
        self.progreso = ft.ProgressRing(width=22, height=22, stroke_width=3, visible=False)
        self.estado = ft.Text("", size=13)

        self.btn = ft.FilledButton(
            "Probar sesión SIPP", icon=ft.Icons.PLAY_ARROW,
            on_click=self._probar_sesion)

        self.contenido = ft.Column(
            [
                ft.Text("Automatización SIPP", size=20, weight=ft.FontWeight.BOLD),
                ft.Text("Abre el navegador, inicia sesión con las credenciales de "
                        "Configuración (⚙) y selecciona la empresa/sucursal. La "
                        "primera vez en la app instalada descarga el navegador "
                        "(Chromium); requiere internet.",
                        size=13, color=GRIS),
                ft.Divider(),
                self.dd_empresa,
                self.tf_sucursal,
                ft.Row([self.btn, self.progreso], spacing=14,
                       vertical_alignment=ft.CrossAxisAlignment.CENTER),
                self.estado,
            ],
            expand=True, spacing=12,
        )

    async def _probar_sesion(self, _e=None) -> None:
        datos = credenciales.cargar()
        if not datos or not datos[0]:
            self._set_estado("Configura primero las credenciales del SIPP (botón ⚙).", ROJO)
            self.app.avisar("Faltan las credenciales del SIPP.", ROJO)
            return
        usuario, contrasena = datos
        empresa = self.dd_empresa.value
        sucursal = (self.tf_sucursal.value or "").strip()

        self.btn.disabled = True
        self.progreso.visible = True
        self._set_estado("Abriendo el navegador e iniciando sesión…", GRIS)
        try:
            async with SesionSipp(headless=False) as sipp:
                await sipp.login(usuario, contrasena)
                if empresa and sucursal:
                    self._set_estado(f"Sesión iniciada. Seleccionando {empresa} / {sucursal}…", GRIS)
                    await sipp.seleccionar_empresa_sucursal(empresa, sucursal)
                self._set_estado("Sesión del SIPP verificada correctamente.", VERDE)
                self.app.avisar("Sesión del SIPP verificada.", VERDE)
        except ErrorSipp as exc:
            self._set_estado(str(exc), ROJO)
            self.app.avisar(str(exc), ROJO)
        except Exception as exc:  # noqa: BLE001 — se reporta al usuario
            self._set_estado(f"Error inesperado en el RPA: {exc}", ROJO)
            self.app.avisar(f"Error inesperado en el RPA: {exc}", ROJO)
        finally:
            self.btn.disabled = False
            self.progreso.visible = False
            self._safe_update()

    def _set_estado(self, mensaje: str, color) -> None:
        self.estado.value = mensaje
        self.estado.color = color
        self._safe_update()

    def _on_resize(self, _e=None) -> None:
        """Contenido fluido; no requiere recomputar. Presente por consistencia."""

    def _safe_update(self) -> None:
        try:
            self.contenido.update()
        except (RuntimeError, AssertionError, AttributeError):
            pass
