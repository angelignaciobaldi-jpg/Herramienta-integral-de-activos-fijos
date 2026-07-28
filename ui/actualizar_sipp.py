"""Botón único «Actualizar información del SIPP» (Registro y Generador de QR).

Corre en una sola sesión del RPA (un login, sin ventana) la descarga de insumos +
activos de la empresa seleccionada y del catálogo global de empleados
(core/sync_sipp), con un diálogo de progreso. Ambos módulos operativos lo invocan
con su propia empresa y su callback de refresco.
"""

from __future__ import annotations

import asyncio

import flet as ft

from core import credenciales
from ui.comun import GRIS, NARANJA, ROJO, VERDE


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
