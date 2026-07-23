"""Pantalla "Registro de activos" — flujo de levantamiento físico (Fase 1).

Flujo (según levantamiento de requerimientos):
  1) El usuario sube imágenes (archivos o una carpeta) del levantamiento físico.
     El nombre de cada imagen codifica  NombreInsumo_NoSerie.ext  (la serie va
     después del ÚLTIMO '_'). Por cada imagen se crea un registro.
  2) Tabla del levantamiento con checkbox por fila + checkbox general, acciones
     por fila (ver imagen original, eliminar) y masivas (seleccionar todos /
     eliminar seleccionados).
  3) Botón "Buscar en SIPP": consulta el No. de serie de todos los insumos en el
     catálogo del SIPP a través de una capa abstracta (core/proveedor_activos).
     En Fase 1 usa datos de prueba (ProveedorMock).
  4) Los registros se separan en "Dados de alta" y "No dados de alta", cada uno
     consultable en su pestaña.

Fase 2 (deshabilitado por ahora): "Iniciar registro en SIPP" (RPA de alta con
campos por tipo de activo) y "Realizar modificación en SIPP" (RPA de edición).

Contrato modular: expone `.contenido`, `_on_resize` y `cargar_desde_db`.
"""

from __future__ import annotations

import asyncio
import os

import flet as ft

from core import credenciales, db
from core.proveedor_activos import proveedor_por_defecto
from core.rpa_sipp import BucleRpa, ControlRpa, ErrorSipp, RpaDetenido, SesionSipp
from core.tipos_activo import campos_de_tipo, nombre_tipo
from ui.captura_activo import DialogoCapturaActivo
from ui.comun import GRIS, NARANJA, NOMBRES_EMPRESAS, ROJO, VERDE
from ui.tabla_responsiva import ColumnaTabla, FilaDatos, TablaResponsiva

# Extensiones de imagen aceptadas para el levantamiento (sin PDF: son fotos).
IMG_EXT = ["png", "jpg", "jpeg", "tif", "tiff", "bmp"]

# Etiqueta y color por estatus.
_ESTATUS_UI = {
    db.EST_PENDIENTE: ("Pendiente", GRIS),
    db.EST_DADO_ALTA: ("Dado de alta", VERDE),
    db.EST_NO_DADO_ALTA: ("No dado de alta", NARANJA),
}

# Pestañas: clave interna -> etiqueta base.
_TAB_TODOS = "todos"


def parsear_nombre(nombre_archivo: str) -> tuple[str, str]:
    """Separa 'NombreInsumo_NoSerie.ext' en (nombre_insumo, no_serie).

    La serie es lo que va DESPUÉS del último '_' (sin la extensión). Si no hay '_',
    todo es el nombre del insumo y la serie queda vacía."""
    base = os.path.splitext(os.path.basename(nombre_archivo))[0]
    if "_" in base:
        nombre, serie = base.rsplit("_", 1)
        return nombre.strip(), serie.strip()
    return base.strip(), ""


class SeccionRegistroActivos:
    """Levantamiento: carga de imágenes, tabla, búsqueda y categorización."""

    def __init__(self, app):
        self.app = app
        self.page = app.page
        self.proveedor = proveedor_por_defecto()
        self._tab = _TAB_TODOS
        self._seleccionados: set[int] = set()
        # Formulario dinámico de captura por tipo de activo (prepara el alta en SIPP).
        self.dialogo_captura = DialogoCapturaActivo(app, al_guardar=self._refrescar)
        self._construir()

    # ------------------------------------------------------------ UI
    def _construir(self) -> None:
        # Selectores de contexto: se aplican a las imágenes al SUBIRLAS (un
        # levantamiento suele ser de una empresa/sucursal). Editables por fila.
        self.dd_empresa = ft.DropdownM2(
            label="Empresa", dense=True, width=260,
            options=[ft.dropdownm2.Option(key=n, text=n) for n in NOMBRES_EMPRESAS])
        self.tf_sucursal = ft.TextField(label="Sucursal", dense=True, width=180)
        self.tf_departamento = ft.TextField(label="Departamento", dense=True, width=180)
        contexto = ft.Column(
            [
                ft.Text("Datos del levantamiento (se aplican a las imágenes que subas; "
                        "puedes ajustarlos por fila):", size=12, color=GRIS),
                ft.Row([self.dd_empresa, self.tf_sucursal, self.tf_departamento],
                       spacing=12, wrap=True),
            ],
            spacing=6, tight=True,
        )

        # Barra de carga + búsqueda.
        self.progreso = ft.ProgressRing(width=22, height=22, stroke_width=3, visible=False)
        self.estado = ft.Text("", size=12, color=GRIS)
        barra_acciones = ft.Row(
            [
                ft.FilledButton("Subir archivos", icon=ft.Icons.UPLOAD_FILE,
                                on_click=self._subir_archivos),
                ft.OutlinedButton("Subir carpeta", icon=ft.Icons.FOLDER_OPEN,
                                  on_click=self._subir_carpeta),
                ft.OutlinedButton("Buscar en SIPP", icon=ft.Icons.SEARCH,
                                  on_click=self._buscar),
                self.progreso,
                self.estado,
            ],
            spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER,
            wrap=True,
        )

        # Pestañas (Todos / Dados de alta / No dados de alta).
        self._tab_defs = [
            (_TAB_TODOS, "Todos", ft.Icons.LIST_ALT),
            (db.EST_DADO_ALTA, "Dados de alta", ft.Icons.CHECK_CIRCLE),
            (db.EST_NO_DADO_ALTA, "No dados de alta", ft.Icons.PENDING_ACTIONS),
        ]
        self._tab_items: dict[str, dict] = {}
        controles_tab = []
        for clave, texto, icono in self._tab_defs:
            ico = ft.Icon(icono, size=18)
            txt = ft.Text(texto, size=13, no_wrap=True)
            cont = ft.Container(
                content=ft.Row([ico, txt], spacing=8, tight=True,
                               vertical_alignment=ft.CrossAxisAlignment.CENTER),
                padding=ft.Padding.symmetric(horizontal=16, vertical=10),
                border_radius=8,
                on_click=lambda _e, c=clave: self._cambiar_tab(c),
            )
            self._tab_items[clave] = {"cont": cont, "icono": ico, "texto": txt, "base": texto}
            controles_tab.append(cont)
        fila_tabs = ft.Row(controles_tab, spacing=6)

        # Acciones masivas.
        self.barra_masiva = ft.Row(
            [
                ft.TextButton("Seleccionar todos", icon=ft.Icons.SELECT_ALL,
                              on_click=self._seleccionar_todos),
                ft.TextButton("Eliminar seleccionados", icon=ft.Icons.DELETE_SWEEP,
                              on_click=self._eliminar_seleccionados),
            ],
            spacing=6,
        )

        # Barra contextual de RPA (según la pestaña activa). Fase 2: deshabilitada.
        self._barra_rpa = ft.Container()

        # Tabla responsiva.
        self._chk_general = ft.Checkbox(value=False, on_change=self._on_chk_general)
        columnas = [
            ColumnaTabla("", 4, encabezado_control=self._chk_general, ancho_min_px=40),
            ColumnaTabla("Empresa", 14, ancho_min_px=155),
            ColumnaTabla("Sucursal", 14, ancho_min_px=155),
            ColumnaTabla("Departamento", 14, ancho_min_px=155),
            ColumnaTabla("Nombre insumo", 19, ancho_min_px=140),
            ColumnaTabla("No. de serie", 13, ancho_min_px=110),
            ColumnaTabla("Estatus", 10, ancho_min_px=100),
            ColumnaTabla("Acciones", 12, ancho_min_px=145),
        ]
        self.tabla = TablaResponsiva(self.page, columnas)
        self._area_tabla = ft.Column([self.tabla.control], scroll=ft.ScrollMode.AUTO,
                                     expand=True)

        # Estado vacío.
        self.txt_vacio = ft.Container(
            content=ft.Column(
                [
                    ft.Icon(ft.Icons.INVENTORY_2, size=52, color=GRIS),
                    ft.Text("Aún no hay registros en el levantamiento",
                            size=16, color=GRIS, text_align=ft.TextAlign.CENTER),
                    ft.Text("Sube imágenes con nombre «Insumo_Serie.jpg» para empezar.",
                            size=13, color=GRIS, text_align=ft.TextAlign.CENTER),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=8,
            ),
            alignment=ft.Alignment(0, 0), expand=True, visible=False,
        )

        self.contenido = ft.Column(
            [
                ft.Text("Registro de activos", size=20, weight=ft.FontWeight.BOLD),
                contexto,
                barra_acciones,
                ft.Divider(),
                fila_tabs,
                ft.Row([self.barra_masiva, self._barra_rpa],
                       alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                       vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Stack([self._area_tabla, self.txt_vacio], expand=True),
            ],
            expand=True, spacing=12,
        )
        self._estilo_tabs()
        self._actualizar_barra_rpa()

    # ------------------------------------------------------ pestañas
    def _estilo_tabs(self) -> None:
        for clave, item in self._tab_items.items():
            activo = clave == self._tab
            color = ft.Colors.PRIMARY if activo else ft.Colors.ON_SURFACE_VARIANT
            item["icono"].color = color
            item["texto"].color = color
            item["texto"].weight = ft.FontWeight.BOLD if activo else ft.FontWeight.W_500
            item["cont"].bgcolor = (
                ft.Colors.SECONDARY_CONTAINER if activo else None)

    def _cambiar_tab(self, clave: str) -> None:
        if clave == self._tab:
            return
        self._tab = clave
        self._estilo_tabs()
        self._actualizar_barra_rpa()
        self._refrescar()
        self._safe_update()

    def _actualizar_barra_rpa(self) -> None:
        """Muestra el botón de RPA correspondiente a la pestaña activa."""
        if self._tab == db.EST_NO_DADO_ALTA:
            self._barra_rpa.content = ft.FilledButton(
                "Iniciar registro en SIPP", icon=ft.Icons.SMART_TOY,
                tooltip="Da de alta en el SIPP los activos que ya tienen datos capturados",
                on_click=self._iniciar_registro_sipp)
        elif self._tab == db.EST_DADO_ALTA:
            self._barra_rpa.content = ft.FilledButton(
                "Realizar modificación en SIPP", icon=ft.Icons.EDIT_NOTE, disabled=True,
                tooltip="Disponible en Fase 2 (RPA de modificación)")
        else:
            self._barra_rpa.content = None

    # ------------------------------------------------------ datos / render
    def cargar_desde_db(self) -> None:
        """Carga inicial (la invoca el shell al arrancar) y refresco general."""
        self._refrescar()

    def _filas_actuales(self) -> list["db.Levantamiento"]:
        if self._tab == _TAB_TODOS:
            return db.listar_levantamiento()
        return db.listar_levantamiento_por_estatus(self._tab)

    def _refrescar(self) -> None:
        registros = self._filas_actuales()
        # Limpia de la selección los ids que ya no existen en esta vista.
        ids_vista = {r.id for r in registros}
        self._seleccionados &= ids_vista
        filas = [self._fila(r) for r in registros]
        self.tabla.set_contenido(filas)
        self.txt_vacio.visible = not registros
        self._area_tabla.visible = bool(registros)
        self._actualizar_conteos()
        self._sincronizar_chk_general(registros)
        self._safe_update()

    def _fila(self, r: "db.Levantamiento") -> FilaDatos:
        chk = ft.Checkbox(
            value=r.id in self._seleccionados,
            on_change=lambda e, i=r.id: self._toggle_sel(i, e.control.value))
        # Celdas editables de ubicación (se persisten sin reconstruir la tabla,
        # para no perder el foco ni el scroll mientras se capturan).
        # Los tres controles editables se encierran en un contenedor de tamaño FIJO
        # (mismo ancho y alto) para que queden idénticos y centrados en su columna,
        # alineados con el texto automático (que va centrado). Sin esto, el
        # DropdownM2 se expande al ancho de la columna y se ve distinto a los
        # TextField. El texto de los TextField se centra (text_align).
        _W, _H, _PAD = 145, 38, 8
        emp_ctrl = ft.DropdownM2(
            value=r.empresa or None, dense=True, text_size=12, content_padding=_PAD,
            options=[ft.dropdownm2.Option(key=n, text=n) for n in NOMBRES_EMPRESAS],
            on_change=lambda e, i=r.id: self._set_ubic(i, empresa=e.control.value or ""))
        suc_ctrl = ft.TextField(
            value=r.sucursal or "", dense=True, text_size=12, content_padding=_PAD,
            text_align=ft.TextAlign.CENTER,
            on_blur=lambda e, i=r.id: self._set_ubic(i, sucursal=(e.control.value or "").strip()))
        dep_ctrl = ft.TextField(
            value=r.departamento or "", dense=True, text_size=12, content_padding=_PAD,
            text_align=ft.TextAlign.CENTER,
            on_blur=lambda e, i=r.id: self._set_ubic(i, departamento=(e.control.value or "").strip()))
        emp = ft.Container(emp_ctrl, width=_W, height=_H)
        suc = ft.Container(suc_ctrl, width=_W, height=_H)
        dep = ft.Container(dep_ctrl, width=_W, height=_H)
        etiqueta, color = _ESTATUS_UI.get(r.estatus_registro, ("—", GRIS))
        estatus = ft.Text(etiqueta, size=12, color=color, weight=ft.FontWeight.W_500)
        capturado = r.id_tipo_activo is not None
        acciones = ft.Row(
            [
                ft.IconButton(
                    icon=ft.Icons.ASSIGNMENT, icon_size=20,
                    icon_color=VERDE if capturado else None,
                    tooltip=("Datos capturados — editar" if capturado
                             else "Capturar datos para el alta"),
                    on_click=lambda _e, reg=r: self.dialogo_captura.abrir(reg)),
                ft.IconButton(
                    icon=ft.Icons.IMAGE, tooltip="Ver imagen original", icon_size=20,
                    on_click=lambda _e, ruta=r.ruta_imagen: self._ver_imagen(ruta)),
                ft.IconButton(
                    icon=ft.Icons.DELETE_OUTLINE, tooltip="Eliminar", icon_size=20,
                    icon_color=ft.Colors.ERROR,
                    on_click=lambda _e, i=r.id: self._eliminar_uno(i)),
            ],
            spacing=0, alignment=ft.MainAxisAlignment.CENTER, tight=True,
        )
        return FilaDatos([
            chk,
            emp,
            suc,
            dep,
            r.nombre_insumo,
            r.no_serie or "—",
            estatus,
            acciones,
        ])

    def _set_ubic(self, id_lev: int, empresa: "str | None" = None,
                  sucursal: "str | None" = None, departamento: "str | None" = None) -> None:
        """Persiste la edición de empresa/sucursal/departamento de una fila. No
        reconstruye la tabla (conserva foco y scroll durante la captura)."""
        db.actualizar_ubicacion_levantamiento(
            id_lev, empresa=empresa, sucursal=sucursal, departamento=departamento)

    def _actualizar_conteos(self) -> None:
        n_dado = len(db.listar_levantamiento_por_estatus(db.EST_DADO_ALTA))
        n_no = len(db.listar_levantamiento_por_estatus(db.EST_NO_DADO_ALTA))
        n_todos = len(db.listar_levantamiento())
        conteos = {_TAB_TODOS: n_todos, db.EST_DADO_ALTA: n_dado,
                   db.EST_NO_DADO_ALTA: n_no}
        for clave, item in self._tab_items.items():
            item["texto"].value = f"{item['base']} ({conteos.get(clave, 0)})"

    # ------------------------------------------------------ selección
    def _toggle_sel(self, id_lev: int, valor: bool) -> None:
        if valor:
            self._seleccionados.add(id_lev)
        else:
            self._seleccionados.discard(id_lev)
        self._sincronizar_chk_general(self._filas_actuales())

    def _sincronizar_chk_general(self, registros: list) -> None:
        ids = {r.id for r in registros}
        self._chk_general.value = bool(ids) and ids <= self._seleccionados
        try:
            self._chk_general.update()
        except (RuntimeError, AssertionError):
            pass

    def _on_chk_general(self, e) -> None:
        registros = self._filas_actuales()
        ids = {r.id for r in registros}
        if e.control.value:
            self._seleccionados |= ids
        else:
            self._seleccionados -= ids
        self._refrescar()

    def _seleccionar_todos(self, _e=None) -> None:
        self._seleccionados |= {r.id for r in self._filas_actuales()}
        self._refrescar()

    def _eliminar_seleccionados(self, _e=None) -> None:
        ids = list(self._seleccionados)
        if not ids:
            self.app.avisar("No hay registros seleccionados.", ROJO)
            return

        def eliminar(_e=None) -> None:
            db.eliminar_levantamientos(ids)
            self._seleccionados.clear()
            self.page.pop_dialog()
            self._refrescar()
            self.app.avisar(f"{len(ids)} registro(s) eliminado(s).", VERDE)

        self.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("Eliminar seleccionados"),
                content=ft.Text(f"¿Eliminar {len(ids)} registro(s) del levantamiento? "
                                "Esta acción no se puede deshacer."),
                actions=[
                    ft.TextButton("Cancelar", on_click=lambda _e: self.page.pop_dialog()),
                    ft.FilledButton("Eliminar", icon=ft.Icons.DELETE, on_click=eliminar),
                ],
                actions_alignment=ft.MainAxisAlignment.END,
            )
        )

    # ------------------------------------------------------ acciones por fila
    def _ver_imagen(self, ruta: "str | None") -> None:
        if ruta and os.path.exists(ruta):
            self.app.abrir_en_sistema(ruta)
        else:
            self.app.avisar("No se encontró la imagen original.", ROJO)

    def _eliminar_uno(self, id_lev: int) -> None:
        db.eliminar_levantamiento(id_lev)
        self._seleccionados.discard(id_lev)
        self._refrescar()
        self.app.avisar("Registro eliminado.", VERDE)

    # ------------------------------------------------------ carga de imágenes
    async def _subir_archivos(self, _e=None) -> None:
        archivos = await self.app.picker.pick_files(
            dialog_title="Selecciona las imágenes del levantamiento",
            allowed_extensions=IMG_EXT, allow_multiple=True)
        if not archivos:
            return
        self._registrar_imagenes([(a.name, a.path) for a in archivos])

    async def _subir_carpeta(self, _e=None) -> None:
        carpeta = await self.app.picker.get_directory_path(
            dialog_title="Selecciona la carpeta con las imágenes")
        if not carpeta:
            return
        try:
            entradas = [
                (nombre, os.path.join(carpeta, nombre))
                for nombre in sorted(os.listdir(carpeta))
                if os.path.splitext(nombre)[1].lower().lstrip(".") in IMG_EXT
            ]
        except OSError as exc:
            self.app.avisar(f"No se pudo leer la carpeta: {exc}", ROJO)
            return
        if not entradas:
            self.app.avisar("La carpeta no contiene imágenes compatibles.", NARANJA)
            return
        self._registrar_imagenes(entradas)

    def _registrar_imagenes(self, entradas: list[tuple[str, str]]) -> None:
        """Da de alta un registro por imagen (parseando su nombre) etiquetándolo con
        los selectores de contexto actuales. Cuenta cuántas se agregaron y cuántas se
        omitieron por estar repetidas (misma serie+insumo)."""
        empresa = self.dd_empresa.value or ""
        sucursal = (self.tf_sucursal.value or "").strip()
        departamento = (self.tf_departamento.value or "").strip()
        agregadas, omitidas = 0, 0
        for nombre, ruta in entradas:
            nombre_insumo, no_serie = parsear_nombre(nombre)
            if not nombre_insumo:
                omitidas += 1
                continue
            nuevo = db.guardar_levantamiento(
                nombre_insumo, no_serie, ruta,
                empresa=empresa, sucursal=sucursal, departamento=departamento)
            if nuevo is None:
                omitidas += 1
            else:
                agregadas += 1
        self._refrescar()
        msg = f"{agregadas} imagen(es) agregada(s)."
        if omitidas:
            msg += f" {omitidas} omitida(s) (repetidas o sin nombre válido)."
        self.app.avisar(msg, VERDE if agregadas else NARANJA)

    # ------------------------------------------------------ búsqueda en SIPP
    async def _buscar(self, _e=None) -> None:
        registros = db.listar_levantamiento()
        series = sorted({r.no_serie.strip() for r in registros if r.no_serie.strip()})
        if not series:
            self.app.avisar("No hay números de serie que buscar.", ROJO)
            return
        self._set_cargando(True, f"Buscando {len(series)} serie(s) en el SIPP…")
        try:
            resultados = await asyncio.to_thread(self.proveedor.buscar_por_serie, series)
        except NotImplementedError as exc:
            self._set_cargando(False)
            self.app.avisar(str(exc), ROJO)
            return
        except Exception as exc:  # noqa: BLE001 — se reporta al usuario
            self._set_cargando(False)
            self.app.avisar(f"No se pudo buscar en el SIPP: {exc}", ROJO)
            return
        # Aplica el resultado a cada registro (por su No. de serie).
        for r in registros:
            serie = r.no_serie.strip()
            if not serie:
                continue
            res = resultados.get(serie)
            if res is None:
                continue
            estatus = db.EST_DADO_ALTA if res.dado_de_alta else db.EST_NO_DADO_ALTA
            db.actualizar_estatus_levantamiento(r.id, estatus, res.id_activo_sipp)
        self._set_cargando(False)
        n_dado = len(db.listar_levantamiento_por_estatus(db.EST_DADO_ALTA))
        n_no = len(db.listar_levantamiento_por_estatus(db.EST_NO_DADO_ALTA))
        self._refrescar()
        self.app.avisar(
            f"Búsqueda completada: {n_dado} dado(s) de alta, {n_no} sin dar de alta.",
            VERDE)

    # ------------------------------------------------ RPA: alta en el SIPP
    def _payload_alta(self, r: "db.Levantamiento") -> tuple:
        """Traduce lo capturado en el formulario dinámico a lo que espera el RPA:
        (nombre del tipo, [(ng_model, valor, control)], {etiqueta: valor}).

        Los campos con `detalle=True` son las características del insumo
        (camposDetalle), que el RPA empareja por rótulo y no por ng-model."""
        datos = r.datos()
        campos, detalles = [], {}
        for campo in campos_de_tipo(r.id_tipo_activo):
            if campo.clave == "id_TipoActivo":
                continue  # el tipo se elige aparte (dispara las características)
            valor = (datos.get(campo.clave) or "").strip()
            if not valor:
                continue
            if campo.detalle:
                detalles[campo.etiqueta] = valor
            else:
                campos.append((campo.ng_model, valor, campo.control))
        return nombre_tipo(r.id_tipo_activo), campos, detalles

    async def _iniciar_registro_sipp(self, _e=None) -> None:
        """Da de alta en el SIPP (vía RPA) los activos 'No dados de alta' que ya
        tienen sus datos capturados. Corre en un hilo aparte para no congelar la
        interfaz, con progreso y opción de detener."""
        creds = credenciales.cargar()
        if not creds or not creds[0]:
            self.app.avisar("Configura primero las credenciales del SIPP (botón ⚙).", ROJO)
            return
        usuario, contrasena = creds
        todos = db.listar_levantamiento_por_estatus(db.EST_NO_DADO_ALTA)
        pendientes = [r for r in todos if r.id_tipo_activo is not None]
        if not pendientes:
            self.app.avisar(
                "Ningún activo tiene datos capturados. Usa el botón de captura "
                "(📋) en cada fila para definir el tipo y sus campos.", NARANJA)
            return

        total = len(pendientes)
        bucle = BucleRpa()
        ctrl = ControlRpa(bucle.loop)
        ui_loop = asyncio.get_running_loop()

        txt = ft.Text(f"Preparando… (0/{total})", size=13)
        barra = ft.ProgressBar(value=0)
        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("Registrando activos en el SIPP"),
            content=ft.Container(
                ft.Column([txt, barra,
                           ft.Text("Se abrirá un navegador; no lo cierres.",
                                   size=11, color=GRIS)],
                          tight=True, spacing=12),
                width=420),
            actions=[ft.TextButton("Detener", on_click=lambda _e: ctrl.detener())],
        )
        self.page.show_dialog(dlg)
        self.page.update()

        def avance(i: int, nombre: str) -> None:
            """Actualiza el progreso desde el hilo del RPA (marshalado a la UI)."""
            def aplicar() -> None:
                txt.value = f"({i}/{total}) {nombre}"
                barra.value = i / total
                try:
                    dlg.update()
                except (RuntimeError, AssertionError):
                    pass
            ui_loop.call_soon_threadsafe(aplicar)

        exitosos, fallidos = 0, []

        async def flujo() -> None:
            nonlocal exitosos
            async with SesionSipp(headless=False) as sipp:
                await sipp.login(usuario, contrasena)
                # Contexto de sesión: se toma del primer registro (un levantamiento
                # suele ser de una misma empresa/sucursal).
                primero = pendientes[0]
                if primero.empresa and primero.sucursal:
                    try:
                        await sipp.seleccionar_empresa_sucursal(
                            primero.empresa, primero.sucursal)
                    except ErrorSipp as exc:
                        fallidos.append(f"Selección de empresa/sucursal: {exc}")
                for i, r in enumerate(pendientes, 1):
                    await ctrl.punto_control()
                    avance(i, r.nombre_insumo)
                    tipo, campos, detalles = self._payload_alta(r)
                    try:
                        await sipp.alta_activo(tipo, campos, detalles)
                        db.actualizar_estatus_levantamiento(r.id, db.EST_DADO_ALTA)
                        exitosos += 1
                    except ErrorSipp as exc:
                        fallidos.append(f"{r.nombre_insumo} ({r.no_serie}): {exc}")

        detenido = False
        try:
            await asyncio.wrap_future(bucle.enviar(flujo()))
        except RpaDetenido:
            detenido = True
        except Exception as exc:  # noqa: BLE001 — se reporta al usuario
            fallidos.append(str(exc))
        finally:
            bucle.cerrar()
            self.page.pop_dialog()
            self._refrescar()

        if detenido:
            self.app.avisar(f"Proceso detenido. {exitosos} activo(s) registrado(s).",
                            NARANJA)
        elif fallidos:
            self.app.avisar(
                f"{exitosos} registrado(s), {len(fallidos)} con error: {fallidos[0]}",
                ROJO, duracion=9000)
        else:
            self.app.avisar(f"{exitosos} activo(s) registrado(s) en el SIPP.", VERDE)

    def _set_cargando(self, cargando: bool, texto: str = "") -> None:
        self.progreso.visible = cargando
        self.estado.value = texto
        self.estado.color = GRIS
        self._safe_update()

    # ------------------------------------------------------ utilidades
    def _on_resize(self, _e=None) -> None:
        """La tabla mide su propio ancho; no requiere recomputar aquí."""

    def _safe_update(self) -> None:
        try:
            self.contenido.update()
        except (RuntimeError, AssertionError, AttributeError):
            pass  # aún no montado; se reflejará al renderizar
