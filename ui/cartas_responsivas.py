"""Pantalla "Cartas responsivas" (generación LOCAL del PDF).

Dos modos (pestañas):
  - "Por empleado": elegir empresa + empleado, filtrar por rango de fecha de
    registro, marcar activos y generar UNA carta.
  - "Masiva por empresa": elegir empresa (+ rango de fecha), cargar TODOS los
    colaboradores de esa empresa en secciones plegables; en cada sección se ven sus
    activos con casilla (sin marcar por defecto) y un buscador propio. Cada
    colaborador con ≥1 activo marcado entra en la exportación; se genera un PDF por
    colaborador nombrado "Carta responsiva NOMBRE - EMPRESA.pdf".

El folio es un consecutivo LOCAL (el SIPP no lo expone y su carta responsiva está
rota). La carta se arma con core/carta_responsiva_local y se imprime con Chromium.
"""

from __future__ import annotations

import asyncio

import flet as ft

from core import credenciales, preferencias
from core.empresas import ID_POR_EMPRESA
from ui.comun import GRIS, NARANJA, NOMBRES_EMPRESAS, ROJO, VERDE, parse_fecha
from ui.componentes import (CampoFecha, Modal, boton_herramienta, boton_primario,
                            boton_secundario, buscador, campo_opciones, campo_texto,
                            tarjeta_seccion)

_ALTO_FILA = 40
_ALTO_MAX_LISTA = 520
_MAX_COLAB = 120  # tope de colaboradores pintados a la vez (se filtra para acotar)
_CLAVE_FOLIO = "carta_folio_siguiente"


class SeccionCartasResponsivas:
    """Genera cartas responsivas (PDF) por empleado o de forma masiva por empresa."""

    def __init__(self, app):
        self.app = app
        self.page = app.page
        # Individual
        self._id_empleado = None
        self._nombre_empleado = ""
        self._activos = []
        self._seleccion: set = set()
        # Masiva
        self._m_activos = []                 # todos los ActivoCarta de la empresa
        self._m_por_empleado: dict = {}      # id_empleado -> {"nombre", "activos"}
        self._m_seleccion: dict = {}         # id_empleado -> set(id_activo)
        self._construir()

    # ==================================================================== UI
    def _construir(self) -> None:
        from ui.componentes import Pestanas
        self.tabs = Pestanas(
            [("individual", "Por empleado", ft.Icons.PERSON),
             ("masiva", "Masiva por empresa", ft.Icons.GROUPS)],
            al_cambiar=self._cambiar_tab)
        self._panel_individual = self._construir_individual()
        self._panel_masiva = self._construir_masiva()
        self._panel_masiva.visible = False
        self.contenido = ft.Column(
            [ft.Row([self.tabs.control]), self._panel_individual, self._panel_masiva],
            expand=True, spacing=14, scroll=ft.ScrollMode.AUTO,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
        self._sincronizar_estado()

    def _cambiar_tab(self, clave: str) -> None:
        self._panel_individual.visible = clave == "individual"
        self._panel_masiva.visible = clave == "masiva"
        self._safe_update()

    def _rango_fechas(self, cf_desde, cf_hasta):
        """(desde, hasta) como date | None a partir de dos CampoFecha."""
        return parse_fecha(cf_desde.value), parse_fecha(cf_hasta.value)

    # ---------------------------------------------------- panel INDIVIDUAL
    def _construir_individual(self) -> ft.Control:
        self.blq_empresa, self.dd_empresa = campo_opciones(
            "Empresa", list(NOMBRES_EMPRESAS), width=320,
            on_change=lambda _e: self._reset_activos())
        self.txt_empleado = ft.Text("Ningún empleado elegido.", size=13, color=GRIS,
                                    expand=True, no_wrap=False)
        self.progreso = ft.ProgressRing(width=22, height=22, stroke_width=3, visible=False)
        self.cf_desde = CampoFecha(self.page, "Desde (registro)", flotante=True)
        self.cf_hasta = CampoFecha(self.page, "Hasta (registro)", flotante=True)

        self.buscador_filtro = buscador("Filtrar por insumo, serie o etiqueta…",
                                        expand=True)
        self.buscador_filtro.on_change = lambda _e: self._pintar_lista()
        self.chk_todos = ft.Checkbox(label="Seleccionar todos", value=False,
                                     on_change=self._alternar_todos)
        self.lista = ft.ListView(height=_ALTO_FILA, spacing=2,
                                 padding=ft.Padding.only(right=8))
        self.txt_estado = ft.Text("", size=13, color=GRIS)
        self.txt_conteo = ft.Text("", size=12, color=GRIS)

        cabecera = ft.Column(
            [
                ft.Text("Genera la carta responsiva de un colaborador con los activos "
                        "que tiene dados de alta en el SIPP.", size=13, color=GRIS),
                ft.Divider(),
                ft.Row([self.blq_empresa,
                        boton_secundario("Elegir empleado", ft.Icons.PERSON_SEARCH,
                                         self._elegir_empleado),
                        self.progreso], spacing=14, wrap=True,
                       vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Row([ft.Icon(ft.Icons.BADGE, size=18, color=GRIS), self.txt_empleado],
                       spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Row([self.cf_desde.control, self.cf_hasta.control], spacing=12,
                       wrap=True),
                ft.Row([boton_primario("Buscar activos dados de alta", ft.Icons.SEARCH,
                                       self._buscar_activos),
                        boton_secundario("Actualizar información del SIPP", ft.Icons.SYNC,
                                         self._actualizar_sipp)], spacing=12, wrap=True),
                self.txt_estado,
            ],
            spacing=14, tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH)

        tarjeta_lista = tarjeta_seccion(ft.Column(
            [ft.Row([self.buscador_filtro]),
             ft.Row([self.chk_todos, self.txt_conteo],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
             self._encabezado_tabla(), self.lista],
            spacing=10, tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH))
        barra = ft.Row([boton_primario("Generar carta responsiva (PDF)",
                                       ft.Icons.DESCRIPTION, self._generar_carta)],
                       alignment=ft.MainAxisAlignment.END)
        return ft.Column([tarjeta_seccion(cabecera), tarjeta_lista, barra],
                         spacing=14, tight=True,
                         horizontal_alignment=ft.CrossAxisAlignment.STRETCH)

    def _encabezado_tabla(self) -> ft.Control:
        def th(txt, ancho=None, expand=None):
            return ft.Container(ft.Text(txt, size=12, weight=ft.FontWeight.BOLD,
                                        color=ft.Colors.ON_SURFACE_VARIANT),
                                width=ancho, expand=expand,
                                padding=ft.Padding.symmetric(horizontal=6, vertical=2))
        return ft.Container(
            ft.Row([ft.Container(width=34), th("Insumo", expand=3), th("Serie", expand=2),
                    th("Etiqueta", ancho=90), th("Ubicación", expand=2),
                    th("Centro de costo", expand=2)], spacing=4),
            border=ft.Border(bottom=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)))

    # ---------------------------------------------------- panel MASIVA
    def _construir_masiva(self) -> ft.Control:
        self.m_blq_empresa, self.m_dd_empresa = campo_opciones(
            "Empresa", list(NOMBRES_EMPRESAS), width=320,
            on_change=lambda _e: self._m_reset())
        self.m_cf_desde = CampoFecha(self.page, "Desde (registro)", flotante=True)
        self.m_cf_hasta = CampoFecha(self.page, "Hasta (registro)", flotante=True)
        self.m_progreso = ft.ProgressRing(width=22, height=22, stroke_width=3,
                                          visible=False)
        # Búsqueda por BOTÓN/Enter (no en cada tecla): repintar 120 secciones por
        # pulsación es pesado. Escribe el nombre y pulsa «Buscar».
        self.m_buscar_colab = buscador(
            "Nombre del colaborador… (Enter o «Buscar»)",
            on_submit=self._pintar_colaboradores, expand=True)
        self.m_lista = ft.Column(spacing=6, tight=True,
                                 horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
        self.m_estado = ft.Text("", size=13, color=GRIS)

        cabecera = ft.Column(
            [
                ft.Text("Genera cartas de TODOS los colaboradores de una empresa. En "
                        "cada sección marca los activos que van en su carta; el "
                        "colaborador con al menos uno marcado se incluye.", size=13,
                        color=GRIS),
                ft.Divider(),
                ft.Row([self.m_blq_empresa, self.m_cf_desde.control,
                        self.m_cf_hasta.control, self.m_progreso], spacing=12, wrap=True,
                       vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Row([boton_primario("Cargar colaboradores", ft.Icons.GROUPS,
                                       self._cargar_colaboradores),
                        boton_secundario("Actualizar información del SIPP", ft.Icons.SYNC,
                                         self._actualizar_sipp)], spacing=12, wrap=True),
                self.m_estado,
            ], spacing=14, tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH)

        tarjeta_lista = tarjeta_seccion(ft.Column(
            [ft.Row([self.m_buscar_colab,
                     boton_secundario("Buscar", ft.Icons.SEARCH,
                                      self._pintar_colaboradores)],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER),
             ft.Container(self.m_lista)],
            spacing=10, tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH))
        self.m_barra = ft.Row(
            [boton_primario("Generar cartas masivas (PDF)", ft.Icons.DESCRIPTION,
                            self._generar_masivas)],
            alignment=ft.MainAxisAlignment.END)
        return ft.Column([tarjeta_seccion(cabecera), tarjeta_lista, self.m_barra],
                         spacing=14, tight=True,
                         horizontal_alignment=ft.CrossAxisAlignment.STRETCH)

    # ==================================================== INDIVIDUAL: lógica
    def _empresa_id(self):
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
            self.m_dd_empresa.value = nombre
            self._safe_update()

        DialogoActualizarSipp(self.app, set_empresa=_fijar).abrir()

    async def _buscar_activos(self, _e=None) -> None:
        idemp = self._empresa_id()
        if idemp is None:
            self.app.avisar("Elige una empresa.", NARANJA); return
        if self._id_empleado is None:
            self.app.avisar("Elige un empleado.", NARANJA); return
        creds = credenciales.cargar()
        if not creds or not creds[0]:
            self.app.avisar("Configura primero las credenciales del SIPP (botón ⚙).", ROJO)
            return
        usuario, contrasena = creds
        desde, hasta = self._rango_fechas(self.cf_desde, self.cf_hasta)
        id_empleado = self._id_empleado

        self.progreso.visible = True
        self.txt_estado.value = "Consultando el SIPP…"
        self.txt_estado.color = GRIS
        self._safe_update()

        activos, error = [], None

        async def flujo():
            nonlocal activos, error
            from core import cartas_responsivas as cr
            from core.rpa_sipp import SesionSipp
            try:
                async with SesionSipp(headless=True) as sipp:
                    await sipp.login(usuario, contrasena)
                    activos = await cr.listar_activos_empleado(
                        sipp, idemp, id_empleado, fh_inicio=desde, fh_fin=hasta)
            except Exception as exc:  # noqa: BLE001
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
            self.txt_estado.value = (f"«{self._nombre_empleado}» no tiene activos en "
                                     f"ese criterio.")
            self.txt_estado.color = NARANJA
        else:
            self.txt_estado.value = f"{len(activos)} activo(s) de «{self._nombre_empleado}»."
            self.txt_estado.color = VERDE
        self._pintar_lista()
        self._safe_update()

    def _reset_activos(self) -> None:
        self._activos = []
        self._seleccion = set()
        self.chk_todos.value = False
        self._pintar_lista()
        self._sincronizar_estado()

    def _filtrados(self):
        t = (self.buscador_filtro.value or "").strip().lower()
        if not t:
            return self._activos
        return [a for a in self._activos if t in a.nombre.lower()
                or t in a.serie.lower() or t in a.etiqueta.lower()]

    def _pintar_lista(self, _e=None) -> None:
        activos = self._filtrados()
        self.lista.controls = [self._fila(a) for a in activos]
        self.lista.height = min(max(len(activos), 1) * _ALTO_FILA, _ALTO_MAX_LISTA)
        self.txt_conteo.value = (f"{len(self._seleccion)} seleccionado(s) de "
                                 f"{len(self._activos)} activo(s)")
        self._safe_update()

    def _fila(self, activo) -> ft.Control:
        def td(txt, expand=None, ancho=None):
            return ft.Container(ft.Text(txt or "—", size=12, no_wrap=False,
                                        color=ft.Colors.ON_SURFACE),
                                expand=expand, width=ancho,
                                padding=ft.Padding.symmetric(horizontal=6, vertical=0))
        chk = ft.Checkbox(value=activo.id_activo in self._seleccion,
                          on_change=lambda e, a=activo: self._alternar_uno(a, e.control.value))
        return ft.Container(
            ft.Row([ft.Container(chk, width=34), td(activo.nombre, expand=3),
                    td(activo.serie, expand=2), td(activo.etiqueta, ancho=90),
                    td(activo.ubicacion, expand=2), td(activo.centro_cc, expand=2)],
                   spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.Padding.symmetric(vertical=4),
            border=ft.Border(bottom=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)))

    def _alternar_uno(self, activo, valor) -> None:
        if valor:
            self._seleccion.add(activo.id_activo)
        else:
            self._seleccion.discard(activo.id_activo)
        vis = self._filtrados()
        self.chk_todos.value = bool(vis) and all(a.id_activo in self._seleccion for a in vis)
        self.txt_conteo.value = (f"{len(self._seleccion)} seleccionado(s) de "
                                 f"{len(self._activos)} activo(s)")
        self._safe_update()

    def _alternar_todos(self, _e=None) -> None:
        for a in self._filtrados():
            if self.chk_todos.value:
                self._seleccion.add(a.id_activo)
            else:
                self._seleccion.discard(a.id_activo)
        self._pintar_lista()

    def _folio_siguiente(self) -> str:
        n = preferencias.cargar_valor(_CLAVE_FOLIO, 1)
        try:
            return f"{int(n):06d}"
        except (TypeError, ValueError):
            return "000001"

    async def _generar_carta(self, _e=None) -> None:
        if not self._seleccion:
            self.app.avisar("Selecciona al menos un activo para la carta.", NARANJA)
            return
        creds = credenciales.cargar()
        if not creds or not creds[0]:
            self.app.avisar("Configura primero las credenciales del SIPP (botón ⚙).", ROJO)
            return
        _, self._tf_folio = campo_texto("Folio", valor=self._folio_siguiente(),
                                        flotante=True, hint="Consecutivo (editable)")
        modal = Modal(self.page, "Generar carta responsiva", ancho=480)
        modal.cuerpo.controls = [
            ft.Text(f"Carta de «{self._nombre_empleado}» con {len(self._seleccion)} "
                    f"activo(s).", size=14, weight=ft.FontWeight.W_600),
            self._tf_folio,
            ft.Text("Se arma en la herramienta y se guarda como PDF. Folio local.",
                    size=11, color=GRIS)]

        async def _hacer(_e=None):
            await self._confirmar_generar(modal)

        modal.set_acciones([
            boton_herramienta("Cancelar", on_click=lambda _e: modal.cerrar()),
            boton_primario("Generar", ft.Icons.DESCRIPTION, _hacer)])
        modal.abrir()

    async def _confirmar_generar(self, modal) -> None:
        import os
        import re as _re
        folio = (self._tf_folio.value or "").strip() or self._folio_siguiente()
        modal.cerrar()
        carpeta = await self.app.picker.get_directory_path(
            dialog_title="Elige dónde guardar la carta (PDF)")
        if not carpeta:
            return
        creds = credenciales.cargar()
        usuario, contrasena = creds
        activos_sel = [a for a in self._activos if a.id_activo in self._seleccion]
        id_empresa = self._empresa_id()
        id_empleado = self._id_empleado
        nombre = self._nombre_empleado or (activos_sel[0].empleado if activos_sel else "")
        empresa = activos_sel[0].empresa if activos_sel else (self.dd_empresa.value or "")

        prog = Modal(self.page, "Generando carta responsiva", subtitulo=nombre, ancho=460)
        prog.cuerpo.controls = [ft.Text("Generando la carta…", size=13), ft.ProgressBar()]
        prog.abrir()
        ruta, error = None, None

        async def flujo():
            nonlocal ruta, error
            from core import carta_responsiva_local as crl
            from core.rpa_sipp import SesionSipp
            try:
                async with SesionSipp(headless=True) as sipp:
                    await sipp.login(usuario, contrasena)
                    base = crl.nombre_archivo_carta(nombre, empresa)
                    ruta = await crl.generar_carta_local(
                        sipp, activos_sel, os.path.join(carpeta, base + ".pdf"), folio,
                        nombre_empleado=nombre, id_empleado=id_empleado,
                        id_empresa=id_empresa)
            except Exception as exc:  # noqa: BLE001
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
        try:
            preferencias.guardar_valor(_CLAVE_FOLIO, int(folio) + 1)
        except (TypeError, ValueError):
            pass
        self.app.avisar(f"Carta generada (folio {folio}).", VERDE, accion="Abrir",
                        on_accion=lambda _e, x=str(ruta): self.app.abrir_en_sistema(x),
                        duracion=9000)

    # ==================================================== MASIVA: lógica
    def _m_empresa_id(self):
        return (ID_POR_EMPRESA.get(self.m_dd_empresa.value)
                if self.m_dd_empresa.value else None)

    def _m_reset(self) -> None:
        self._m_activos = []
        self._m_por_empleado = {}
        self._m_seleccion = {}
        self.m_lista.controls = []
        self.m_estado.value = ""
        self._safe_update()

    async def _cargar_colaboradores(self, _e=None) -> None:
        idemp = self._m_empresa_id()
        if idemp is None:
            self.app.avisar("Elige una empresa.", NARANJA); return
        creds = credenciales.cargar()
        if not creds or not creds[0]:
            self.app.avisar("Configura primero las credenciales del SIPP (botón ⚙).", ROJO)
            return
        usuario, contrasena = creds
        desde, hasta = self._rango_fechas(self.m_cf_desde, self.m_cf_hasta)

        self.m_progreso.visible = True
        self.m_estado.value = "Consultando el SIPP…"
        self.m_estado.color = GRIS
        self._safe_update()

        activos, error = [], None

        async def flujo():
            nonlocal activos, error
            from core import cartas_responsivas as cr
            from core.rpa_sipp import SesionSipp
            try:
                async with SesionSipp(headless=True) as sipp:
                    await sipp.login(usuario, contrasena)
                    activos = await cr.listar_activos_empresa(
                        sipp, idemp, fh_inicio=desde, fh_fin=hasta)
            except Exception as exc:  # noqa: BLE001
                error = str(exc)

        from core.rpa_sipp import BucleRpa
        bucle = BucleRpa()
        try:
            await asyncio.wrap_future(bucle.enviar(flujo()))
        finally:
            bucle.cerrar()
            self.m_progreso.visible = False

        if error:
            self.m_estado.value = ""
            self._safe_update()
            self.app.avisar(f"No se pudieron traer los activos: {error}", ROJO,
                           duracion=8000)
            return
        # Agrupar por colaborador (solo los que tienen empleado).
        self._m_activos = activos
        grupos: dict = {}
        for a in activos:
            if a.id_empleado is None:
                continue
            g = grupos.setdefault(a.id_empleado, {"nombre": a.empleado, "activos": []})
            g["activos"].append(a)
        self._m_por_empleado = dict(sorted(grupos.items(),
                                           key=lambda kv: kv[1]["nombre"]))
        self._m_seleccion = {}
        self.m_estado.value = (f"{len(self._m_por_empleado)} colaborador(es), "
                               f"{len(activos)} activo(s). Marca los activos por "
                               f"colaborador; escribe arriba para filtrar.")
        self.m_estado.color = VERDE
        self._pintar_colaboradores()
        self._safe_update()

    def _pintar_colaboradores(self, _e=None) -> None:
        # Coincide si TODAS las palabras escritas están en el nombre (en cualquier
        # orden), así "rendon rodolfo" también encuentra "RODOLFO ... RENDON ...".
        palabras = (self.m_buscar_colab.value or "").strip().lower().split()
        items = [(eid, g) for eid, g in self._m_por_empleado.items()
                 if all(p in g["nombre"].lower() for p in palabras)]
        recortado = items[:_MAX_COLAB]
        self.m_lista.controls = [self._tile_colaborador(eid, g) for eid, g in recortado]
        if len(items) > _MAX_COLAB:
            self.m_lista.controls.append(ft.Text(
                f"…y {len(items) - _MAX_COLAB} colaborador(es) más. Filtra por nombre.",
                size=11, color=GRIS))
        self._safe_update()

    def _sel_colab(self, eid) -> set:
        return self._m_seleccion.setdefault(eid, set())

    def _tile_colaborador(self, eid, grupo) -> ft.Control:
        activos = grupo["activos"]
        sel = self._sel_colab(eid)
        titulo = ft.Text(grupo["nombre"], size=13, weight=ft.FontWeight.W_600,
                         expand=True, no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS)
        conteo = ft.Text(f"{len(sel)}/{len(activos)}", size=12, color=GRIS)
        buscador_sec = buscador(f"Buscar activo de {grupo['nombre']}…", expand=True)
        lista_sec = ft.Column(spacing=0, tight=True)

        def refrescar_conteo():
            conteo.value = f"{len(self._sel_colab(eid))}/{len(activos)}"
            self._safe_update()

        def filtrar():
            q = (buscador_sec.value or "").strip().lower()
            vis = [a for a in activos if not q or q in a.nombre.lower()
                   or q in a.serie.lower() or q in a.etiqueta.lower()]
            lista_sec.controls = [fila(a) for a in vis]
            self._safe_update()

        def alternar(a, v):
            s = self._sel_colab(eid)
            (s.add if v else s.discard)(a.id_activo)
            refrescar_conteo()

        def fila(a):
            chk = ft.Checkbox(value=a.id_activo in self._sel_colab(eid),
                              on_change=lambda e, a=a: alternar(a, e.control.value))
            return ft.Row(
                [ft.Container(chk, width=34),
                 ft.Text(a.nombre or "—", size=12, expand=3, no_wrap=False),
                 ft.Text(a.serie or "—", size=12, expand=2, no_wrap=False),
                 ft.Text(a.etiqueta or "—", size=12, width=90),
                 ft.Text(a.ubicacion or "—", size=12, expand=2, no_wrap=False)],
                spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER)

        def marcar_todos(v):
            s = self._sel_colab(eid)
            for a in activos:
                (s.add if v else s.discard)(a.id_activo)
            refrescar_conteo()
            filtrar()

        def th(txt, expand=None, ancho=None):
            return ft.Container(
                ft.Text(txt, size=11, weight=ft.FontWeight.BOLD,
                        color=ft.Colors.ON_SURFACE_VARIANT),
                expand=expand, width=ancho,
                padding=ft.Padding.symmetric(horizontal=0, vertical=2))

        encabezado = ft.Container(
            ft.Row([ft.Container(width=34), th("Insumo", expand=3),
                    th("Serie", expand=2), th("Etiqueta", ancho=90),
                    th("Ubicación", expand=2)], spacing=4),
            border=ft.Border(bottom=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)))

        buscador_sec.on_change = lambda _e: filtrar()
        filtrar()
        contenido = ft.Column(
            [ft.Row([buscador_sec,
                     ft.Checkbox(label="Todos",
                                 on_change=lambda e: marcar_todos(e.control.value))],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER),
             encabezado,
             ft.Container(lista_sec, padding=ft.Padding.only(left=4))],
            spacing=6, tight=True)
        return ft.ExpansionTile(
            title=ft.Row([titulo, conteo], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            controls=[ft.Container(contenido, padding=ft.Padding.only(
                left=12, right=12, bottom=10))],
            maintain_state=True, affinity=ft.TileAffinity.LEADING)

    async def _generar_masivas(self, _e=None) -> None:
        # Colaboradores con ≥1 activo marcado.
        incluidos = [(eid, self._m_por_empleado[eid]["nombre"])
                     for eid, s in self._m_seleccion.items()
                     if s and eid in self._m_por_empleado]
        if not incluidos:
            self.app.avisar("Marca al menos un activo en algún colaborador.", NARANJA)
            return
        creds = credenciales.cargar()
        if not creds or not creds[0]:
            self.app.avisar("Configura primero las credenciales del SIPP (botón ⚙).", ROJO)
            return
        _, tf_folio = campo_texto("Folio inicial", valor=self._folio_siguiente(),
                                  flotante=True, hint="Consecutivo (editable)")
        modal = Modal(self.page, "Generar cartas masivas", ancho=480)
        modal.cuerpo.controls = [
            ft.Text(f"Se generarán {len(incluidos)} carta(s) (una por colaborador con "
                    f"activos marcados).", size=14, weight=ft.FontWeight.W_600),
            tf_folio,
            ft.Text("Cada PDF se nombra «Carta responsiva NOMBRE - EMPRESA». Folio "
                    "local consecutivo.", size=11, color=GRIS)]

        async def _hacer(_e=None):
            await self._confirmar_masivas(modal, tf_folio, incluidos)

        modal.set_acciones([
            boton_herramienta("Cancelar", on_click=lambda _e: modal.cerrar()),
            boton_primario("Generar", ft.Icons.DESCRIPTION, _hacer)])
        modal.abrir()

    async def _confirmar_masivas(self, modal, tf_folio, incluidos) -> None:
        folio = (tf_folio.value or "").strip() or self._folio_siguiente()
        modal.cerrar()
        carpeta = await self.app.picker.get_directory_path(
            dialog_title="Elige la carpeta para las cartas (PDF)")
        if not carpeta:
            return
        creds = credenciales.cargar()
        usuario, contrasena = creds
        empresa = self.m_dd_empresa.value or ""
        # Armar grupos (nombre, id_empleado, [activos seleccionados]).
        grupos = []
        for eid, _nombre in incluidos:
            g = self._m_por_empleado[eid]
            sel = self._m_seleccion.get(eid, set())
            activos_sel = [a for a in g["activos"] if a.id_activo in sel]
            if activos_sel:
                grupos.append((g["nombre"], eid, activos_sel))

        ui_loop = asyncio.get_running_loop()
        txt = ft.Text(f"Generando {len(grupos)} carta(s)…", size=13)
        barra = ft.ProgressBar(value=0)
        prog = Modal(self.page, "Generando cartas masivas", ancho=460)
        prog.cuerpo.controls = [txt, barra]
        prog.abrir()

        def avance(hechos, total):
            def aplicar():
                txt.value = f"Generando cartas… {hechos}/{total}"
                barra.value = hechos / total if total else None
                prog.refrescar()
            ui_loop.call_soon_threadsafe(aplicar)

        resultados, folio_fin, error = [], None, None

        async def flujo():
            nonlocal resultados, folio_fin, error
            from core import carta_responsiva_local as crl
            from core.rpa_sipp import SesionSipp
            try:
                async with SesionSipp(headless=True) as sipp:
                    await sipp.login(usuario, contrasena)
                    resultados, folio_fin = await crl.generar_cartas_masivas(
                        sipp, grupos, carpeta, folio, empresa, progreso=avance)
            except Exception as exc:  # noqa: BLE001
                error = str(exc)

        from core.rpa_sipp import BucleRpa
        bucle = BucleRpa()
        try:
            await asyncio.wrap_future(bucle.enviar(flujo()))
        finally:
            bucle.cerrar()
            prog.cerrar()

        if error:
            self.app.avisar(f"No se pudieron generar las cartas: {error}", ROJO,
                           duracion=9000)
            return
        if folio_fin is not None:
            preferencias.guardar_valor(_CLAVE_FOLIO, folio_fin)
        ok = sum(1 for r in resultados if r.get("ok"))
        fallidas = [r for r in resultados if not r.get("ok")]
        msg = f"{ok} carta(s) generada(s) en la carpeta."
        if fallidas:
            msg += f" {len(fallidas)} con error."
        self.app.avisar(msg, VERDE if not fallidas else NARANJA, accion="Abrir carpeta",
                        on_accion=lambda _e, x=carpeta: self.app.abrir_en_sistema(x),
                        duracion=9000)

    # ==================================================== utilidades
    def _sincronizar_estado(self) -> None:
        if self._empresa_id() is None:
            self.txt_estado.value = "Elige una empresa y un empleado."
        elif self._id_empleado is None:
            self.txt_estado.value = "Elige un empleado."
        else:
            self.txt_estado.value = "Pulsa «Buscar activos dados de alta»."
        self.txt_estado.color = GRIS
        self._safe_update()

    def _on_resize(self, _e=None) -> None:
        """Contenido fluido."""

    def _safe_update(self) -> None:
        try:
            self.contenido.update()
        except (RuntimeError, AssertionError, AttributeError):
            pass
