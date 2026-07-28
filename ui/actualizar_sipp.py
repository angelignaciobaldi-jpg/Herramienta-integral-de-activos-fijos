"""Botón único «Actualizar información del SIPP» (Registro y Generador de QR).

Corre en una sola sesión del RPA (un login, sin ventana) la descarga de insumos +
activos de la empresa seleccionada y del catálogo global de empleados
(core/sync_sipp), con un diálogo de progreso. Ambos módulos operativos lo invocan
con su propia empresa y su callback de refresco.
"""

from __future__ import annotations

import asyncio

import flet as ft

from core import credenciales, preferencias
from core.empresas import ID_POR_EMPRESA, NOMBRES_EMPRESAS
from ui.comun import GRIS, NARANJA, ROJO, VERDE

# Preferencia: última empresa actualizada (para proponerla la próxima vez).
_CLAVE_EMPRESA = "sipp_actualizar_empresa"


class DialogoActualizarSipp:
    """Modal para actualizar la información del SIPP eligiendo la empresa.

    La primera vez (o si se pide) muestra un selector de empresa. Si ya se actualizó
    antes, muestra la empresa actual con la opción de volver a actualizar con ella o
    de elegir otra. `set_empresa(nombre)` refleja la elección en el módulo (p. ej.
    su selector) y `al_terminar()` refresca tras la descarga.
    """

    def __init__(self, app, set_empresa=None, al_terminar=None):
        self.app = app
        self.page = app.page
        self.set_empresa = set_empresa
        self.al_terminar = al_terminar
        self._dd = None
        self._empresa_sel = None

    def abrir(self, _e=None) -> None:
        ultima = preferencias.cargar_valor(_CLAVE_EMPRESA)
        if ultima and ultima in ID_POR_EMPRESA:
            self._mostrar_confirmar(ultima)
        else:
            self._mostrar_seleccionar()

    def _mostrar_confirmar(self, empresa: str) -> None:
        self._empresa_sel = empresa
        dlg = ft.AlertDialog(
            modal=True, title=ft.Text("Actualizar información del SIPP"),
            content=ft.Container(
                ft.Column([
                    ft.Text(f"Empresa actual: «{empresa}».", size=14,
                            weight=ft.FontWeight.W_600),
                    ft.Text("Puedes actualizar de nuevo con esta empresa o elegir otra.",
                            size=12, color=GRIS)], tight=True, spacing=8), width=440),
            actions=[
                ft.TextButton("Cancelar", on_click=lambda _e: self.page.pop_dialog()),
                ft.TextButton("Seleccionar otra empresa",
                              on_click=lambda _e, emp=empresa: self._mostrar_seleccionar(emp)),
                ft.FilledButton(f"Actualizar «{empresa}»", icon=ft.Icons.SYNC,
                                on_click=self._ejecutar_confirmada),
            ], actions_alignment=ft.MainAxisAlignment.END)
        self.page.show_dialog(dlg)
        self.page.update()

    def _mostrar_seleccionar(self, sugerida: str | None = None) -> None:
        self._dd = ft.DropdownM2(
            label="Empresa", dense=True, width=380, value=sugerida or None,
            options=[ft.dropdownm2.Option(key=n, text=n) for n in NOMBRES_EMPRESAS])
        dlg = ft.AlertDialog(
            modal=True, title=ft.Text("Selecciona la empresa a actualizar"),
            content=ft.Container(
                ft.Column([self._dd,
                           ft.Text("Se descargarán sus insumos y activos, más el "
                                   "catálogo de empleados (global).",
                                   size=11, color=GRIS)], tight=True, spacing=10),
                width=420),
            actions=[
                ft.TextButton("Cancelar", on_click=lambda _e: self.page.pop_dialog()),
                ft.FilledButton("Actualizar", icon=ft.Icons.SYNC,
                                on_click=self._ejecutar_seleccion),
            ], actions_alignment=ft.MainAxisAlignment.END)
        self.page.show_dialog(dlg)
        self.page.update()

    async def _ejecutar_confirmada(self, _e=None) -> None:
        await self._correr(self._empresa_sel)

    async def _ejecutar_seleccion(self, _e=None) -> None:
        await self._correr(self._dd.value if self._dd else None)

    async def _correr(self, empresa: str | None) -> None:
        if not empresa or empresa not in ID_POR_EMPRESA:
            self.app.avisar("Elige una empresa.", NARANJA)
            return
        self.page.pop_dialog()  # cierra el modal de selección/confirmación
        preferencias.guardar_valor(_CLAVE_EMPRESA, empresa)
        if callable(self.set_empresa):
            self.set_empresa(empresa)
        await actualizar_info_sipp(self.app, ID_POR_EMPRESA.get(empresa), empresa,
                                   al_terminar=self.al_terminar)


async def actualizar_info_sipp(app, id_empresa, empresa: str, al_terminar=None) -> None:
    """Descarga insumos+activos (de `id_empresa`) y empleados (global) del SIPP.

    `al_terminar()`: callback opcional tras terminar (para refrescar el módulo).
    """
    creds = credenciales.cargar()
    if not creds or not creds[0]:
        app.avisar("Configura primero las credenciales del SIPP (botón ⚙).", ROJO)
        return
    usuario, contrasena = creds
    if id_empresa is None or not empresa:
        app.avisar("Elige una empresa para actualizar su información del SIPP.", NARANJA)
        return

    page = app.page
    ui_loop = asyncio.get_running_loop()
    txt = ft.Text("Conectando al SIPP…", size=13)
    barra = ft.ProgressBar()
    dlg = ft.AlertDialog(
        modal=True, title=ft.Text("Actualizando información del SIPP"),
        content=ft.Container(
            ft.Column([txt, barra,
                       ft.Text(f"Empresa: {empresa}", size=11, color=GRIS)],
                      tight=True, spacing=12), width=440))
    page.show_dialog(dlg)
    page.update()

    def _upd() -> None:
        try:
            dlg.update()
        except (RuntimeError, AssertionError):
            pass

    def avance(hechos: int, total: int) -> None:
        def aplicar() -> None:
            barra.value = (hechos / total) if total else None
            _upd()
        ui_loop.call_soon_threadsafe(aplicar)

    def mensaje(texto: str) -> None:
        def aplicar() -> None:
            txt.value = texto
            barra.value = None
            _upd()
        ui_loop.call_soon_threadsafe(aplicar)

    resultado, error = {}, None

    async def flujo() -> None:
        nonlocal resultado, error
        from core import sync_sipp
        from core.rpa_sipp import SesionSipp
        try:
            async with SesionSipp(headless=True) as sipp:
                await sipp.login(usuario, contrasena)
                resultado = await sync_sipp.actualizar_sipp(
                    sipp, id_empresa, empresa, progreso=avance, mensaje=mensaje)
        except Exception as exc:  # noqa: BLE001 — se reporta al usuario
            error = str(exc)

    from core.rpa_sipp import BucleRpa
    bucle = BucleRpa()
    try:
        await asyncio.wrap_future(bucle.enviar(flujo()))
    finally:
        bucle.cerrar()
        page.pop_dialog()
        if callable(al_terminar):
            al_terminar()

    if error:
        app.avisar(f"No se pudo actualizar la información del SIPP: {error}", ROJO,
                   duracion=9000)
    else:
        app.avisar(
            f"SIPP actualizado para «{empresa}»: {resultado.get('insumos', 0)} insumo(s), "
            f"{resultado.get('activos', 0)} activo(s), "
            f"{resultado.get('empleados', 0)} empleado(s).", VERDE, duracion=8000)
