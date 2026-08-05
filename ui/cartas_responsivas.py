"""Pantalla "Cartas responsivas".

Genera la carta responsiva (PDF, con folio del SIPP) de los activos dados de alta
a nombre de un colaborador:

  1. Elegir la empresa y el empleado (del catálogo cacheado; usar «Actualizar
     información del SIPP» si el catálogo está vacío).
  2. "Buscar activos dados de alta": trae del SIPP los activos con alta a nombre del
     empleado (core/cartas_responsivas, read-only) y los muestra con casilla.
  3. Seleccionar los activos que van en la carta y "Generar carta responsiva": el
     SIPP asigna el FOLIO y produce el PDF, que se descarga a la carpeta elegida.

El folio lo genera el SIPP al generar; **cada generación consume un folio real**,
por eso se pide confirmación antes de generar.
"""

from __future__ import annotations

import asyncio

import flet as ft

from core import credenciales
from core.empresas import ID_POR_EMPRESA, NOMBRES_EMPRESAS
from ui.comun import GRIS, NARANJA, ROJO, VERDE
from ui.componentes import (Modal, boton_herramienta, boton_primario,
                            boton_secundario, buscador, campo_opciones,
                            tarjeta_seccion)


class SeccionCartasResponsivas:
    """Lista los activos dados de alta de un empleado y genera su carta responsiva."""

    def __init__(self, app):
        self.app = app
        self.page = app.page
        self._id_empleado: "int | None" = None
        self._nombre_empleado: str = ""
        self._activos: list = []          # ActivoCarta cargados
        self._seleccion: set[int] = set()  # id_activo seleccionados
        self._filas: dict[int, ft.Control] = {}
        self._construir()

    def _construir(self) -> None:
        self.blq_empresa, self.dd_empresa = campo_opciones(
            "Empresa", list(NOMBRES_EMPRESAS), width=320,
            on_change=lambda _e: self._reset_activos())
        self.txt_empleado = ft.Text("Ningún empleado elegido.", size=13, color=GRIS,
                                    expand=True, no_wrap=False)
        self.progreso = ft.ProgressRing(width=22, height=22, stroke_width=3, visible=False)

        self.buscador_filtro = buscador(
            "Filtrar por insumo, serie o etiqueta…", expand=True)
        self.buscador_filtro.on_change = lambda _e: self._pintar_lista()
        self.chk_todos = ft.Checkbox(label="Seleccionar todos", value=False,
                                     on_change=self._alternar_todos)
        # Altura generosa y fija: el ListView (virtualizado) desplaza sus filas y el
        # contenido de la pantalla tiene su propio scroll general.
        self.lista = ft.ListView(height=520, spacing=2, padding=ft.Padding.only(right=8))
        self.txt_estado = ft.Text("", size=13, color=GRIS)
        self.txt_conteo = ft.Text("", size=12, color=GRIS)

        cabecera = ft.Column(
            [
                ft.Text("Genera la carta responsiva de un colaborador con los activos "
                        "que tiene dados de alta en el SIPP. El folio lo asigna el "
                        "SIPP al generar.", size=13, color=GRIS),
                ft.Divider(),
                ft.Row([self.blq_empresa,
                        boton_secundario("Elegir empleado", ft.Icons.PERSON_SEARCH,
                                         self._elegir_empleado),
                        self.progreso], spacing=14,
                       vertical_alignment=ft.CrossAxisAlignment.CENTER, wrap=True),
                ft.Row([ft.Icon(ft.Icons.BADGE, size=18, color=GRIS), self.txt_empleado],
                       spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Row(
                    [
                        boton_primario("Buscar activos dados de alta", ft.Icons.SEARCH,
                                       self._buscar_activos),
                        boton_secundario(
                            "Actualizar información del SIPP", ft.Icons.SYNC,
                            self._actualizar_sipp,
                            tooltip="Descarga del SIPP el catálogo de empleados (global) "
                                    "e insumos/activos de la empresa elegida"),
                    ], spacing=12, wrap=True),
                self.txt_estado,
            ],
            spacing=14, tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )

        tarjeta_lista = tarjeta_seccion(
            ft.Column(
                [
                    ft.Row([self.buscador_filtro], spacing=8,
                           vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    ft.Row([self.chk_todos, self.txt_conteo],
                           alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                    self._encabezado_tabla(),
                    self.lista,
                ],
                spacing=10, tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH))

        self.barra_generar = ft.Row(
            [boton_primario("Generar carta responsiva (PDF)", ft.Icons.DESCRIPTION,
                            self._generar_carta)],
            alignment=ft.MainAxisAlignment.END)

        # Scroll GENERAL de la pantalla: la cabecera, la tarjeta de la lista y la
        # barra se desplazan juntas; la lista tiene además su propio scroll interno.
        self.contenido = ft.Column(
            [tarjeta_seccion(cabecera), tarjeta_lista, self.barra_generar],
            expand=True, spacing=14, scroll=ft.ScrollMode.AUTO,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
        self._sincronizar_estado()

    # ------------------------------------------------------ encabezado tabla
    def _encabezado_tabla(self) -> ft.Control:
        def th(txt, ancho=None, expand=None):
            return ft.Container(
                ft.Text(txt, size=12, weight=ft.FontWeight.BOLD,
                        color=ft.Colors.ON_SURFACE_VARIANT),
                width=ancho, expand=expand,
                padding=ft.Padding.symmetric(horizontal=6, vertical=2))
        return ft.Container(
            ft.Row([ft.Container(width=34), th("Insumo", expand=3), th("Serie", expand=2),
                    th("Etiqueta", ancho=90), th("Departamento", expand=2),
                    th("Centro de costo", expand=2)], spacing=4),
            padding=ft.Padding.only(bottom=2),
            border=ft.Border(bottom=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)))

    # ------------------------------------------------------ empleado / empresa
    def _empresa_id(self) -> "int | None":
        return ID_POR_EMPRESA.get(self.dd_empresa.value) if self.dd_empresa.value else None

    def _elegir_empleado(self, _e=None) -> None:
        from ui.selector_empleado import DialogoSelectorEmpleado

        def _al_elegir(id_empleado, nombre):
            self._id_empleado = id_empleado
            self._nombre_empleado = nombre
            self.txt_empleado.value = f"Empleado: {nombre}  (id {id_empleado})"
            self.txt_empleado.color = ft.Colors.ON_SURFACE
            self._reset_activos()

        DialogoSelectorEmpleado(self.app, _al_elegir).abrir()

    def _actualizar_sipp(self, _e=None) -> None:
        from ui.actualizar_sipp import DialogoActualizarSipp

        def _fijar(nombre: str) -> None:
            self.dd_empresa.value = nombre
            self._safe_update()

        DialogoActualizarSipp(self.app, set_empresa=_fijar).abrir()

    # ------------------------------------------------------ buscar activos (RPA)
    async def _buscar_activos(self, _e=None) -> None:
        idemp = self._empresa_id()
        if idemp is None:
            self.app.avisar("Elige una empresa.", NARANJA)
            return
        if self._id_empleado is None:
            self.app.avisar("Elige un empleado.", NARANJA)
            return
        creds = credenciales.cargar()
        if not creds or not creds[0]:
            self.app.avisar("Configura primero las credenciales del SIPP (botón ⚙).", ROJO)
            return
        usuario, contrasena = creds

        self.progreso.visible = True
        self.txt_estado.value = "Consultando el SIPP…"
        self.txt_estado.color = GRIS
        self._safe_update()

        activos, error = [], None
        id_empleado = self._id_empleado

        async def flujo() -> None:
            nonlocal activos, error
            from core import cartas_responsivas as cr
            from core.rpa_sipp import SesionSipp
            try:
                async with SesionSipp(headless=True) as sipp:
                    await sipp.login(usuario, contrasena)
                    activos = await cr.listar_activos_empleado(sipp, idemp, id_empleado)
            except Exception as exc:  # noqa: BLE001 — se reporta al usuario
                error = str(exc)

        from core.rpa_sipp import BucleRpa
        bucle = BucleRpa()
        try:
            await asyncio.wrap_future(bucle.enviar(flujo()))
        finally:
            bucle.cerrar()
            self.progreso.visible = False

        if error:
            self.txt_estado.value = ""
            self._safe_update()
            self.app.avisar(f"No se pudieron traer los activos: {error}", ROJO,
                           duracion=8000)
            return

        self._activos = activos
        self._seleccion = set()
        self.chk_todos.value = False
        if not activos:
            self.txt_estado.value = (f"«{self._nombre_empleado}» no tiene activos dados "
                                     f"de alta en «{self.dd_empresa.value}».")
            self.txt_estado.color = NARANJA
        else:
            self.txt_estado.value = (f"{len(activos)} activo(s) dado(s) de alta de "
                                     f"«{self._nombre_empleado}».")
            self.txt_estado.color = VERDE
        self._pintar_lista()
        self._safe_update()

    def _reset_activos(self) -> None:
        """Limpia la lista (cambió empresa o empleado)."""
        self._activos = []
        self._seleccion = set()
        self.chk_todos.value = False
        self._pintar_lista()
        self._sincronizar_estado()

    # ------------------------------------------------------ lista con selección
    def _filtrados(self) -> list:
        t = (self.buscador_filtro.value or "").strip().lower()
        if not t:
            return self._activos
        return [a for a in self._activos
                if t in a.nombre.lower() or t in a.serie.lower()
                or t in a.etiqueta.lower()]

    def _pintar_lista(self, _e=None) -> None:
        activos = self._filtrados()
        self._filas = {}
        self.lista.controls = [self._fila(a) for a in activos]
        self.txt_conteo.value = (f"{len(self._seleccion)} seleccionado(s) de "
                                 f"{len(self._activos)} activo(s)"
                                 + (f" · {len(activos)} en el filtro"
                                    if len(activos) != len(self._activos) else ""))
        self._safe_update()

    def _fila(self, activo) -> ft.Control:
        def td(txt, expand=None, ancho=None):
            return ft.Container(
                ft.Text(txt or "—", size=12, no_wrap=False,
                        color=ft.Colors.ON_SURFACE),
                expand=expand, width=ancho,
                padding=ft.Padding.symmetric(horizontal=6, vertical=0))
        chk = ft.Checkbox(value=activo.id_activo in self._seleccion,
                          on_change=lambda e, a=activo: self._alternar_uno(a, e.control.value))
        fila = ft.Container(
            ft.Row([ft.Container(chk, width=34), td(activo.nombre, expand=3),
                    td(activo.serie, expand=2), td(activo.etiqueta, ancho=90),
                    td(activo.departamento, expand=2), td(activo.centro_cc, expand=2)],
                   spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.Padding.symmetric(vertical=4),
            border=ft.Border(bottom=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)))
        self._filas[activo.id_activo] = fila
        return fila

    def _alternar_uno(self, activo, valor: bool) -> None:
        if valor:
            self._seleccion.add(activo.id_activo)
        else:
            self._seleccion.discard(activo.id_activo)
        # Mantener coherente la casilla "todos" con lo visible.
        vis = self._filtrados()
        self.chk_todos.value = bool(vis) and all(a.id_activo in self._seleccion for a in vis)
        self.txt_conteo.value = (f"{len(self._seleccion)} seleccionado(s) de "
                                 f"{len(self._activos)} activo(s)")
        self._safe_update()

    def _alternar_todos(self, e=None) -> None:
        marcar = self.chk_todos.value
        for a in self._filtrados():
            if marcar:
                self._seleccion.add(a.id_activo)
            else:
                self._seleccion.discard(a.id_activo)
        self._pintar_lista()

    # ------------------------------------------------------ generar (RPA, folio)
    async def _generar_carta(self, _e=None) -> None:
        if not self._seleccion:
            self.app.avisar("Selecciona al menos un activo para la carta.", NARANJA)
            return
        creds = credenciales.cargar()
        if not creds or not creds[0]:
            self.app.avisar("Configura primero las credenciales del SIPP (botón ⚙).", ROJO)
            return
        # Confirmación: la generación consume un folio del SIPP.
        modal = Modal(self.page, "Generar carta responsiva", ancho=480)
        modal.cuerpo.controls = [
            ft.Text(f"Se generará la carta responsiva de «{self._nombre_empleado}» "
                    f"con {len(self._seleccion)} activo(s).", size=14,
                    weight=ft.FontWeight.W_600),
            ft.Text("El SIPP asignará el folio al generar (consume un folio). "
                    "Después elegirás dónde guardar el PDF.", size=12, color=NARANJA),
        ]
        async def _hacer(_e=None) -> None:
            await self._confirmar_generar(modal)

        modal.set_acciones([
            boton_herramienta("Cancelar", on_click=lambda _e: modal.cerrar()),
            boton_primario("Generar", ft.Icons.DESCRIPTION, _hacer),
        ])
        modal.abrir()

    async def _confirmar_generar(self, modal) -> None:
        modal.cerrar()
        carpeta = await self.app.picker.get_directory_path(
            dialog_title="Elige dónde guardar la carta responsiva (PDF)")
        if not carpeta:
            return
        creds = credenciales.cargar()
        usuario, contrasena = creds
        ids = list(self._seleccion)

        ui_loop = asyncio.get_running_loop()
        txt = ft.Text("Generando la carta en el SIPP…", size=13)
        barra = ft.ProgressBar()
        prog = Modal(self.page, "Generando carta responsiva",
                     subtitulo=self._nombre_empleado, ancho=460)
        prog.cuerpo.controls = [txt, barra]
        prog.abrir()

        rutas, error = [], None

        async def flujo() -> None:
            nonlocal rutas, error
            from core import cartas_responsivas as cr
            from core.rpa_sipp import SesionSipp
            try:
                async with SesionSipp(headless=True) as sipp:
                    await sipp.login(usuario, contrasena)
                    rutas = await cr.generar_carta(sipp, ids, carpeta)
            except Exception as exc:  # noqa: BLE001 — se reporta al usuario
                error = str(exc)

        from core.rpa_sipp import BucleRpa
        bucle = BucleRpa()
        try:
            await asyncio.wrap_future(bucle.enviar(flujo()))
        finally:
            bucle.cerrar()
            prog.cerrar()

        if error:
            self.app.avisar(f"No se pudo generar la carta: {error}", ROJO, duracion=9000)
            return
        primera = str(rutas[0]) if rutas else carpeta
        self.app.avisar(
            f"Carta responsiva generada ({len(rutas)} archivo/s).", VERDE,
            accion="Abrir", on_accion=lambda _e: self.app.abrir_en_sistema(primera),
            duracion=8000)

    # ------------------------------------------------------ utilidades
    def _sincronizar_estado(self) -> None:
        if self._empresa_id() is None:
            self.txt_estado.value = "Elige una empresa y un empleado."
            self.txt_estado.color = GRIS
        elif self._id_empleado is None:
            self.txt_estado.value = "Elige un empleado."
            self.txt_estado.color = GRIS
        else:
            self.txt_estado.value = ("Pulsa «Buscar activos dados de alta».")
            self.txt_estado.color = GRIS
        self._safe_update()

    def _on_resize(self, _e=None) -> None:
        """Contenido fluido; no requiere recomputar."""

    def _safe_update(self) -> None:
        try:
            self.contenido.update()
        except (RuntimeError, AssertionError, AttributeError):
            pass
