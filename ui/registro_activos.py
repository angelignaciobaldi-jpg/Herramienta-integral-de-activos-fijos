"""Pantalla "Registro de activos" — flujo de levantamiento físico (Fase 1).

Flujo (según levantamiento de requerimientos):
  1) El usuario sube imágenes (archivos o una carpeta) del levantamiento físico.
     El nombre de cada imagen codifica  NombreInsumo_NoSerie.ext  (la serie va
     después del ÚLTIMO '_'). Por cada imagen se crea un registro.
  2) Tabla del levantamiento con checkbox por fila + checkbox general, acciones
     por fila (ver imagen original, eliminar) y masivas (seleccionar todos /
     eliminar seleccionados).
  3) Botón "Buscar en SIPP": compara cada insumo contra los activos REALES ya
     descargados del SIPP (caché activos_sipp, por empresa) vía la capa abstracta
     core/proveedor_activos (ProveedorSipp). Queda "dado de alta" si su etiqueta o
     su número de serie coincide con los de algún activo cacheado.
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

from core import archivos, credenciales, db
from core.rpa_sipp import BucleRpa, ControlRpa, ErrorSipp, RpaDetenido, SesionSipp
from core.tipos_activo import ID_POR_NOMBRE, TIPOS_ACTIVO, campos_de_tipo, nombre_tipo
from ui.captura_activo import DialogoCapturaActivo
from ui.carga_masiva import DialogoCargaMasiva
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

# Tamaños de página disponibles (el primero es el de arranque). Se mantienen
# bajos a propósito: cada fila lleva controles editables (el combo de Empresa
# solo tiene ~58 opciones) y todo eso viaja al cliente en cada repintado.
_POR_PAGINA = [25, 50, 100]


def parsear_nombre(nombre_archivo: str) -> tuple[str, str]:
    """Separa 'NombreInsumo_NoSerie.ext' en (nombre_insumo, no_serie).

    La serie es lo que va DESPUÉS del último '_' (sin la extensión). Si no hay '_',
    todo es el nombre del insumo y la serie queda vacía."""
    base = os.path.splitext(os.path.basename(nombre_archivo))[0]
    if "_" in base:
        nombre, serie = base.rsplit("_", 1)
        return nombre.strip(), serie.strip()
    return base.strip(), ""


def _prefill_desde_sipp(info: dict) -> dict:
    """Traduce los datos del SIPP (info_sipp) a las claves del formulario de captura
    (datos_json), para registrar el detalle del insumo de un activo dado de alta.
    Solo incluye lo que trae valor."""
    from core.tipos_activo import SITUACIONES
    m = {
        "nb_NombreInsumo": info.get("insumo"),
        "nu_Serie": info.get("serie"),
        "de_DescripcionActivo": info.get("descripcion"),
        "im_Costo": info.get("costo"),
        "id_GrupoCentroCosto": info.get("grupo_centro_costo"),
        "id_CentroCosto": info.get("centro_costo"),
        "id_Departamento": info.get("departamento"),
        "de_Ubicacion": info.get("ubicacion"),
        "FH_ADQUISICION": info.get("fecha_adquisicion"),
        "FH_GARANTIA": info.get("fecha_garantia"),
        "FH_ASIGNACION": info.get("fecha_asignacion"),
        "nb_Empleado": info.get("empleado"),
        "id_EmpleadoResguardo": info.get("id_empleado_resguardo"),
        "id_InsumoOrigen": info.get("id_insumo_origen"),
    }
    # La situación es un combo con opciones fijas: solo se precarga si el nombre
    # del SIPP coincide con una del catálogo (si no, quedaría en blanco).
    sit = str(info.get("situacion") or "").strip()
    if sit in set(SITUACIONES.values()):
        m["id_Situacion"] = sit
    return {k: str(v).strip() for k, v in m.items()
            if v not in (None, "") and str(v).strip()}


class SeccionRegistroActivos:
    """Levantamiento: carga de imágenes, tabla, búsqueda y categorización."""

    def __init__(self, app):
        self.app = app
        self.page = app.page
        self._tab = _TAB_TODOS
        self._seleccionados: set[int] = set()
        # Paginación: un inventario completo son miles de activos y cada fila
        # lleva controles editables; pintarlos todos vuelve la pantalla inusable.
        self._pagina = 0
        self._por_pagina = _POR_PAGINA[0]
        # Formulario dinámico de captura por tipo de activo (prepara el alta en SIPP).
        self.dialogo_captura = DialogoCapturaActivo(app, al_guardar=self._tras_importar)
        # Carga masiva desde Excel: toma el contexto de los selectores de arriba.
        self.dialogo_carga = DialogoCargaMasiva(
            app, contexto=self._contexto_actual, al_terminar=self._tras_importar)
        self._construir()

    def _contexto_actual(self) -> tuple:
        """(empresa, sucursal, departamento) de los selectores, para etiquetar lo
        que se cargue (el Excel no trae esos datos)."""
        return (self.dd_empresa.value or "",
                (self.tf_sucursal.value or "").strip(),
                (self.tf_departamento.value or "").strip())

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
                ft.OutlinedButton("Subir ZIP", icon=ft.Icons.FOLDER_ZIP,
                                  tooltip="Carpeta comprimida del levantamiento: "
                                          "se extrae y se procesa igual",
                                  on_click=self._subir_zip),
                ft.OutlinedButton("Carga masiva (Excel)", icon=ft.Icons.TABLE_VIEW,
                                  tooltip="Importa un inventario completo desde Excel",
                                  on_click=self.dialogo_carga.abrir),
                ft.OutlinedButton("Buscar en SIPP", icon=ft.Icons.SEARCH,
                                  on_click=self._buscar),
                ft.FilledTonalButton("Actualizar información del SIPP",
                                     icon=ft.Icons.SYNC,
                                     tooltip="Descarga del SIPP, para la empresa de "
                                             "arriba, sus insumos y activos, más el "
                                             "catálogo de empleados (global)",
                                     on_click=self._actualizar_sipp),
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

        # Buscador: con miles de activos importados es la forma práctica de
        # aislar un grupo (p. ej. todas las LAPTOP) y clasificarlo de golpe.
        # Filtra al pulsar Enter (no en cada tecla: repintar la tabla cuesta).
        self.tf_buscar = ft.TextField(
            hint_text="Buscar insumo, etiqueta, serie o ubicación… (Enter)",
            dense=True, width=340, height=40, content_padding=8,
            prefix_icon=ft.Icons.SEARCH, on_submit=self._aplicar_filtro)
        self._filtro = ""
        self._btn_limpiar = ft.IconButton(
            icon=ft.Icons.CLOSE, icon_size=18, tooltip="Limpiar búsqueda",
            visible=False, on_click=self._limpiar_filtro)

        # Acciones masivas.
        self.barra_masiva = ft.Row(
            [
                self.tf_buscar, self._btn_limpiar,
                ft.TextButton("Seleccionar todos", icon=ft.Icons.SELECT_ALL,
                              on_click=self._seleccionar_todos),
                ft.TextButton("Asignar tipo", icon=ft.Icons.CATEGORY,
                              tooltip="Asigna el tipo de activo a los seleccionados",
                              on_click=self._abrir_asignar_tipo),
                ft.TextButton("Eliminar seleccionados", icon=ft.Icons.DELETE_SWEEP,
                              on_click=self._eliminar_seleccionados),
            ],
            spacing=6, wrap=True, vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        # --- Filtros por columna (estilo Excel) -------------------------------
        # Categóricos (empresa/sucursal/departamento) como desplegable de valores
        # distintos; texto (insumo/etiqueta/serie) como "contiene". Se combinan
        # entre sí y con el buscador global; el filtrado lo hace SQLite.
        self._filtros_col: dict = {}
        # Rótulo corto para la opción "sin filtro" (así no se corta en el combo).
        self._TODOS = {"empresa": "Todas", "sucursal": "Todas",
                       "departamento": "Todos"}
        # Mismas medidas para TODOS los controles del filtro (altura, ancho, fuente
        # y padding) para que queden alineados y parejos.
        _WF, _HF, _TS = 172, 46, 12
        _PAD = ft.Padding.symmetric(horizontal=10, vertical=8)

        def _mk_dd(col, etiqueta):
            return ft.DropdownM2(
                label=etiqueta, dense=True, width=_WF, height=_HF, text_size=_TS,
                content_padding=_PAD, border_radius=8, value=self._TODOS[col],
                options=[ft.dropdownm2.Option(key=self._TODOS[col], text=self._TODOS[col])],
                on_change=lambda e, c=col: self._set_filtro_col(c, e.control.value))

        def _mk_tf(col, etiqueta):
            return ft.TextField(
                label=etiqueta, dense=True, width=_WF, height=_HF, text_size=_TS,
                content_padding=_PAD, border_radius=8,
                text_align=ft.TextAlign.CENTER,
                on_submit=lambda e, c=col: self._set_filtro_col(c, e.control.value),
                on_blur=lambda e, c=col: self._set_filtro_col(c, e.control.value))

        self.dd_f_empresa = _mk_dd("empresa", "Empresa")
        self.dd_f_sucursal = _mk_dd("sucursal", "Sucursal")
        self.dd_f_departamento = _mk_dd("departamento", "Departamento")
        self.tf_f_insumo = _mk_tf("nombre_insumo", "Nombre insumo")
        self.tf_f_etiqueta = _mk_tf("etiqueta", "Etiqueta")
        self.tf_f_serie = _mk_tf("no_serie", "No. de serie")
        self._btn_limpiar_filtros = ft.TextButton(
            "Limpiar filtros", icon=ft.Icons.FILTER_ALT_OFF, height=_HF,
            on_click=self._limpiar_filtros_col)
        self.barra_filtros = ft.Row(
            [ft.Icon(ft.Icons.FILTER_LIST, size=18, color=GRIS),
             self.dd_f_empresa, self.dd_f_sucursal, self.dd_f_departamento,
             self.tf_f_insumo, self.tf_f_etiqueta, self.tf_f_serie,
             self._btn_limpiar_filtros],
            spacing=10, run_spacing=10, wrap=True,
            alignment=ft.MainAxisAlignment.CENTER,
            vertical_alignment=ft.CrossAxisAlignment.CENTER)

        # Barra contextual de RPA (según la pestaña activa).
        self._barra_rpa = ft.Container()

        # Paginación (imprescindible con inventarios de miles de activos).
        self._lbl_pagina = ft.Text("", size=12, color=GRIS)
        self._btn_prev = ft.IconButton(icon=ft.Icons.CHEVRON_LEFT, icon_size=20,
                                       tooltip="Página anterior",
                                       on_click=lambda _e: self._mover_pagina(-1))
        self._btn_next = ft.IconButton(icon=ft.Icons.CHEVRON_RIGHT, icon_size=20,
                                       tooltip="Página siguiente",
                                       on_click=lambda _e: self._mover_pagina(1))
        self._dd_por_pagina = ft.DropdownM2(
            value=str(self._por_pagina), dense=True, width=90, text_size=12,
            options=[ft.dropdownm2.Option(key=str(n), text=str(n)) for n in _POR_PAGINA],
            on_change=self._cambiar_por_pagina)
        self.barra_paginacion = ft.Row(
            [self._lbl_pagina, self._btn_prev, self._btn_next,
             ft.Text("por página:", size=12, color=GRIS), self._dd_por_pagina],
            spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER)

        # Tabla responsiva.
        self._chk_general = ft.Checkbox(value=False, on_change=self._on_chk_general)
        columnas = [
            ColumnaTabla("", 4, encabezado_control=self._chk_general, ancho_min_px=40),
            ColumnaTabla("Empresa", 13, ancho_min_px=155),
            ColumnaTabla("Sucursal", 13, ancho_min_px=155),
            ColumnaTabla("Departamento", 13, ancho_min_px=155),
            ColumnaTabla("Nombre insumo", 17, ancho_min_px=140),
            ColumnaTabla("Etiqueta", 10, ancho_min_px=100),
            ColumnaTabla("No. de serie", 10, ancho_min_px=100),
            ColumnaTabla("Estatus", 9, ancho_min_px=95),
            ColumnaTabla("Acciones", 11, ancho_min_px=145),
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
                contexto,
                barra_acciones,
                ft.Divider(),
                fila_tabs,
                ft.Row([self.barra_masiva, self._barra_rpa],
                       alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                       vertical_alignment=ft.CrossAxisAlignment.CENTER),
                self.barra_filtros,
                ft.Stack([self._area_tabla, self.txt_vacio], expand=True),
                self.barra_paginacion,
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
        self._pagina = 0  # cada pestaña arranca en su primera página
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
                "Realizar modificación en SIPP", icon=ft.Icons.EDIT_NOTE,
                tooltip="Reenvía al SIPP los activos que editaste después de darlos de alta",
                on_click=self._modificar_en_sipp)
        else:
            self._barra_rpa.content = None

    # ------------------------------------------------------ datos / render
    def cargar_desde_db(self) -> None:
        """Carga inicial (la invoca el shell al arrancar) y refresco general."""
        self._recargar_filtros()
        self._refrescar()

    def _estatus_tab(self) -> "str | None":
        """Estatus por el que filtra la pestaña activa (None = todas)."""
        return None if self._tab == _TAB_TODOS else self._tab

    def _ids_actuales(self) -> list[int]:
        """Ids de TODO lo que cumple pestaña + filtro (sin traer las filas)."""
        return db.ids_levantamiento(self._estatus_tab(), self._filtro, self._filtros_col)

    def _aplicar_filtro(self, _e=None) -> None:
        self._filtro = (self.tf_buscar.value or "").strip().lower()
        self._btn_limpiar.visible = bool(self._filtro)
        self._pagina = 0
        self._refrescar()

    def _limpiar_filtro(self, _e=None) -> None:
        self.tf_buscar.value = ""
        self._aplicar_filtro()

    # ------------------------------------------------ filtros por columna
    def _set_filtro_col(self, columna: str, valor: str) -> None:
        """Aplica/actualiza el filtro de una columna y repinta desde la página 1."""
        valor = (valor or "").strip()
        if columna in self._TODOS and valor == self._TODOS[columna]:
            valor = ""  # opción "Todas/Todos" = sin filtro
        if valor:
            self._filtros_col[columna] = valor
        else:
            self._filtros_col.pop(columna, None)
        self._pagina = 0
        self._refrescar()

    def _limpiar_filtros_col(self, _e=None) -> None:
        """Quita todos los filtros por columna y reinicia los controles."""
        self._filtros_col = {}
        self.tf_f_insumo.value = self.tf_f_etiqueta.value = self.tf_f_serie.value = ""
        self.dd_f_empresa.value = self._TODOS["empresa"]
        self.dd_f_sucursal.value = self._TODOS["sucursal"]
        self.dd_f_departamento.value = self._TODOS["departamento"]
        self._pagina = 0
        self._refrescar()

    def _tras_importar(self) -> None:
        """Tras importar: aparecen empresas/sucursales/departamentos nuevos, así que
        se recargan los desplegables de filtro además de repintar."""
        self._recargar_filtros()
        self._refrescar()

    def _recargar_filtros(self) -> None:
        """Rellena los desplegables de filtro con los valores distintos actuales
        (se llama al cargar y tras importar/buscar, cuando cambian los datos)."""
        mapa = {"empresa": self.dd_f_empresa, "sucursal": self.dd_f_sucursal,
                "departamento": self.dd_f_departamento}
        for col, dd in mapa.items():
            vals = db.valores_distintos_levantamiento(col)
            dd.options = ([ft.dropdownm2.Option(key=self._TODOS[col], text=self._TODOS[col])]
                          + [ft.dropdownm2.Option(key=v, text=v) for v in vals])
            # Si el valor filtrado ya no existe (p. ej. tras borrar), se resetea.
            if dd.value not in [self._TODOS[col], *vals]:
                dd.value = self._TODOS[col]
                self._filtros_col.pop(col, None)
        self._safe_update()

    def _refrescar(self) -> None:
        """Repinta SOLO la página actual, pidiéndosela ya recortada a SQLite."""
        estatus, filtro = self._estatus_tab(), self._filtro
        total = db.contar_levantamiento(estatus, filtro, self._filtros_col)
        # Ajusta la página si quedó fuera de rango (p. ej. tras filtrar o borrar).
        ultima = max(0, (total - 1) // self._por_pagina) if total else 0
        self._pagina = min(max(0, self._pagina), ultima)
        pagina = db.listar_levantamiento_pagina(
            estatus, filtro, self._por_pagina, self._pagina * self._por_pagina,
            self._filtros_col)
        # Ids visibles: evita re-consultar la tabla en cada clic de checkbox.
        self._ids_pagina = [r.id for r in pagina]
        self.tabla.set_contenido([self._fila(r) for r in pagina])
        self.txt_vacio.visible = total == 0
        self._area_tabla.visible = total > 0
        self._actualizar_conteos()
        self._actualizar_paginacion(total)
        self._sincronizar_chk_general()
        self._safe_update()

    def _actualizar_paginacion(self, total: int) -> None:
        if not total:
            self._lbl_pagina.value = ""
            self._btn_prev.disabled = self._btn_next.disabled = True
            self.barra_paginacion.visible = False
            return
        self.barra_paginacion.visible = True
        ini = self._pagina * self._por_pagina + 1
        fin = min(total, (self._pagina + 1) * self._por_pagina)
        paginas = max(1, (total + self._por_pagina - 1) // self._por_pagina)
        self._lbl_pagina.value = (
            f"{ini}–{fin} de {total}   (página {self._pagina + 1} de {paginas})")
        self._btn_prev.disabled = self._pagina == 0
        self._btn_next.disabled = self._pagina >= paginas - 1

    def _mover_pagina(self, delta: int) -> None:
        self._pagina += delta
        self._refrescar()

    def _cambiar_por_pagina(self, e) -> None:
        try:
            self._por_pagina = int(e.control.value)
        except (TypeError, ValueError):
            self._por_pagina = _POR_PAGINA[0]
        self._pagina = 0
        self._refrescar()

    def _fila(self, r: "db.Levantamiento") -> FilaDatos:
        # Empresa/sucursal/departamento se muestran como TEXTO (rápido de pintar) y
        # se editan con el lápiz de acciones (un diálogo). Antes eran controles
        # editables por fila —un DropdownM2 de ~58 empresas por fila—, lo que hacía
        # lentísimo cada cambio de pestaña/página.
        chk = ft.Checkbox(
            value=r.id in self._seleccionados,
            on_change=lambda e, i=r.id: self._toggle_sel(i, e.control.value))
        etiqueta, color = _ESTATUS_UI.get(r.estatus_registro, ("—", GRIS))
        estatus = ft.Text(etiqueta, size=12, color=color, weight=ft.FontWeight.W_500)
        capturado = r.id_tipo_activo is not None
        controles_accion = []
        # Solo si está dado de alta: consultar la información registrada en el SIPP.
        if r.estatus_registro == db.EST_DADO_ALTA and r.info_sipp():
            controles_accion.append(ft.IconButton(
                icon=ft.Icons.INFO_OUTLINE, icon_size=20, icon_color=VERDE,
                tooltip="Ver información registrada en el SIPP",
                on_click=lambda _e, reg=r: self._ver_info_sipp(reg)))
        controles_accion += [
            ft.IconButton(
                icon=ft.Icons.ASSIGNMENT, icon_size=20,
                icon_color=VERDE if capturado else None,
                tooltip=("Editar datos del activo (tipo, ubicación, resguardo…)"
                         if capturado
                         else "Capturar datos del activo (tipo, ubicación, resguardo…)"),
                on_click=lambda _e, reg=r: self.dialogo_captura.abrir(reg)),
            ft.IconButton(
                icon=ft.Icons.IMAGE, tooltip="Ver imagen original", icon_size=20,
                on_click=lambda _e, ruta=r.ruta_imagen: self._ver_imagen(ruta)),
            ft.IconButton(
                icon=ft.Icons.DELETE_OUTLINE, tooltip="Eliminar", icon_size=20,
                icon_color=ft.Colors.ERROR,
                on_click=lambda _e, i=r.id: self._eliminar_uno(i)),
        ]
        acciones = ft.Row(controles_accion, spacing=0,
                          alignment=ft.MainAxisAlignment.CENTER, tight=True)
        return FilaDatos([
            chk,
            r.empresa or "—",
            r.sucursal or "—",
            r.departamento or "—",
            r.nombre_insumo,
            r.etiqueta or "—",
            r.no_serie or "—",
            estatus,
            acciones,
        ])

    def _actualizar_conteos(self) -> None:
        """Conteos por pestaña con UNA consulta agregada (no listando la tabla)."""
        c = db.contar_levantamiento_por_estatus()
        conteos = {_TAB_TODOS: c.get("total", 0),
                   db.EST_DADO_ALTA: c.get(db.EST_DADO_ALTA, 0),
                   db.EST_NO_DADO_ALTA: c.get(db.EST_NO_DADO_ALTA, 0)}
        for clave, item in self._tab_items.items():
            item["texto"].value = f"{item['base']} ({conteos.get(clave, 0)})"

    # ------------------------------------------------------ selección
    def _toggle_sel(self, id_lev: int, valor: bool) -> None:
        if valor:
            self._seleccionados.add(id_lev)
        else:
            self._seleccionados.discard(id_lev)
        # Se usan los ids ya conocidos de la página (sin re-consultar la tabla).
        self._sincronizar_chk_general()

    def _sincronizar_chk_general(self, registros: list | None = None) -> None:
        """Marca el check del encabezado si TODA la página está seleccionada."""
        ids = ({r.id for r in registros} if registros is not None
               else set(getattr(self, "_ids_pagina", [])))
        self._chk_general.value = bool(ids) and ids <= self._seleccionados
        try:
            self._chk_general.update()
        except (RuntimeError, AssertionError):
            pass

    def _on_chk_general(self, e) -> None:
        """El check del encabezado marca/desmarca solo lo visible en la página."""
        ids = set(getattr(self, "_ids_pagina", []))
        if e.control.value:
            self._seleccionados |= ids
        else:
            self._seleccionados -= ids
        self._refrescar()

    def _seleccionar_todos(self, _e=None) -> None:
        """Selecciona TODO lo que cumple la pestaña + el filtro (no solo la
        página). Solo trae ids, no las filas completas."""
        ids = self._ids_actuales()
        self._seleccionados |= set(ids)
        self._refrescar()
        self.app.avisar(f"{len(ids)} registro(s) seleccionado(s).", VERDE)

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

    # ------------------------------------------- asignación masiva de tipo
    def _abrir_asignar_tipo(self, _e=None) -> None:
        """Asigna un tipo de activo a TODOS los registros seleccionados.

        El inventario importado llega sin tipo y el alta en el SIPP lo exige;
        clasificarlos uno por uno sería inviable con miles de activos. Lo práctico
        es filtrar un grupo (p. ej. «laptop»), seleccionarlo y clasificarlo aquí."""
        ids = list(self._seleccionados)
        if not ids:
            self.app.avisar(
                "Selecciona primero los activos a clasificar (puedes filtrar y "
                "usar «Seleccionar todos»).", NARANJA)
            return

        dd = ft.DropdownM2(
            label="Tipo de activo", dense=True, width=340,
            options=[ft.dropdownm2.Option(key=n, text=n) for n in TIPOS_ACTIVO.values()])

        def aplicar(_e=None) -> None:
            nombre = dd.value
            if not nombre:
                self.app.avisar("Elige un tipo de activo.", ROJO)
                return
            n = db.actualizar_tipo_lote(ids, ID_POR_NOMBRE.get(nombre))
            self.page.pop_dialog()
            self._refrescar()
            self.app.avisar(f"{n} activo(s) clasificados como «{nombre}».", VERDE)

        self.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("Asignar tipo de activo"),
                content=ft.Container(
                    ft.Column(
                        [ft.Text(f"Se aplicará a {len(ids)} activo(s) seleccionado(s).",
                                 size=13),
                         dd,
                         ft.Text("Los campos particulares de cada tipo se capturan "
                                 "después, en el formulario de cada activo.",
                                 size=11, color=GRIS)],
                        spacing=12, tight=True),
                    width=380),
                actions=[
                    ft.TextButton("Cancelar", on_click=lambda _e: self.page.pop_dialog()),
                    ft.FilledButton("Asignar", icon=ft.Icons.CHECK, on_click=aplicar),
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

    def _ver_info_sipp(self, reg: "db.Levantamiento") -> None:
        """Muestra los datos REALES del activo en el SIPP (los que trae el
        catálogo) para consultar la información ya registrada."""
        info = reg.info_sipp()
        if not info:
            self.app.avisar("Este registro no tiene datos del SIPP. Corre "
                            "«Buscar en SIPP» de nuevo.", NARANJA)
            return
        # Campo del SIPP -> etiqueta legible, en el orden en que se muestran.
        campos = [
            ("etiqueta", "Etiqueta"), ("insumo", "Insumo"),
            ("descripcion", "Descripción"), ("serie", "No. de serie"),
            ("tipo", "Tipo de activo"), ("situacion", "Situación"),
            ("costo", "Costo"),
            ("empresa", "Empresa"), ("sucursal", "Sucursal"),
            ("departamento", "Departamento"),
            ("grupo_centro_costo", "Grupo centro de costo"),
            ("centro_costo", "Centro de costo"),
            ("ubicacion", "Ubicación"), ("empleado", "Empleado resguardo"),
            ("fecha_adquisicion", "Fecha de adquisición"),
            ("fecha_garantia", "Fecha de garantía"),
            ("fecha_asignacion", "Fecha de asignación"),
        ]
        filas = []
        for clave, etq in campos:
            valor = str(info.get(clave) or "—").strip() or "—"
            filas.append(ft.Row(
                [ft.Text(f"{etq}:", size=13, weight=ft.FontWeight.W_600,
                         width=150, color=GRIS),
                 ft.Text(valor, size=13, selectable=True, expand=True)],
                vertical_alignment=ft.CrossAxisAlignment.START))
        dlg = ft.AlertDialog(
            title=ft.Row([ft.Icon(ft.Icons.INVENTORY_2, color=VERDE),
                          ft.Text("Información registrada en el SIPP")], spacing=10),
            content=ft.Container(ft.Column(filas, spacing=10, tight=True,
                                           scroll=ft.ScrollMode.AUTO), width=460),
            actions=[ft.TextButton("Cerrar", on_click=lambda _e: self.page.pop_dialog())],
        )
        self.page.show_dialog(dlg)
        self.page.update()

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
        """Carga una carpeta de imágenes, incluyendo sus SUBCARPETAS (los
        levantamientos suelen venir organizados por área)."""
        carpeta = await self.app.picker.get_directory_path(
            dialog_title="Selecciona la carpeta con las imágenes")
        if not carpeta:
            return
        try:
            entradas = archivos.listar_imagenes(carpeta)
        except OSError as exc:
            self.app.avisar(f"No se pudo leer la carpeta: {exc}", ROJO)
            return
        if not entradas:
            self.app.avisar("La carpeta no contiene imágenes compatibles.", NARANJA)
            return
        self._registrar_imagenes(entradas)

    async def _subir_zip(self, _e=None) -> None:
        """Carga un levantamiento comprimido (.zip): lo extrae y sigue el proceso
        normal. Las imágenes se guardan en la carpeta de datos de la app para que
        se puedan seguir abriendo desde la tabla."""
        seleccion = await self.app.picker.pick_files(
            dialog_title="Selecciona el ZIP del levantamiento",
            allowed_extensions=["zip"], allow_multiple=False)
        if not seleccion:
            return
        self._set_cargando(True, f"Extrayendo «{seleccion[0].name}»…")
        try:
            carpeta, extraidas = await asyncio.to_thread(
                archivos.extraer_zip, seleccion[0].path)
        except archivos.ErrorArchivo as exc:
            self._set_cargando(False)
            self.app.avisar(str(exc), ROJO)
            return
        except Exception as exc:  # noqa: BLE001 — se reporta al usuario
            self._set_cargando(False)
            self.app.avisar(f"No se pudo procesar el ZIP: {exc}", ROJO)
            return
        self._set_cargando(False)
        if not extraidas:
            self.app.avisar("El ZIP no contiene imágenes compatibles.", NARANJA)
            return
        self._registrar_imagenes(archivos.listar_imagenes(carpeta))

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
        """Compara cada activo del levantamiento contra los activos REALES ya
        descargados del SIPP (caché por empresa): dado de alta si su etiqueta O
        su número de serie coincide con los de algún activo cacheado."""
        registros = [r for r in db.listar_levantamiento()
                     if (r.etiqueta or "").strip() or (r.no_serie or "").strip()]
        if not registros:
            self.app.avisar("No hay etiquetas ni números de serie que buscar.", ROJO)
            return
        # El caché es por empresa: se agrupan los registros por su empresa.
        from collections import defaultdict

        from core.empresas import ID_POR_EMPRESA
        por_empresa: dict[str, list] = defaultdict(list)
        sin_empresa = 0
        for r in registros:
            idemp = ID_POR_EMPRESA.get((r.empresa or "").strip())
            if idemp is None:
                sin_empresa += 1
            else:
                por_empresa[r.empresa].append(r)
        if not por_empresa:
            self.app.avisar("Los activos no tienen una empresa válida asignada. "
                            "Asigna la empresa (columna Empresa) y reintenta.", ROJO)
            return

        self._set_cargando(True, f"Buscando {len(registros)} activo(s) en el SIPP…")
        try:
            hechos, sin_cache = await asyncio.to_thread(
                self._buscar_por_empresa, por_empresa)
        except Exception as exc:  # noqa: BLE001 — se reporta al usuario
            self._set_cargando(False)
            self.app.avisar(f"No se pudo buscar en el SIPP: {exc}", ROJO)
            return
        self._set_cargando(False)

        n_dado = len(db.listar_levantamiento_por_estatus(db.EST_DADO_ALTA))
        n_no = len(db.listar_levantamiento_por_estatus(db.EST_NO_DADO_ALTA))
        self._refrescar()
        if hechos == 0 and sin_cache:
            self.app.avisar(
                "Descarga primero los activos del SIPP de: "
                + ", ".join(sin_cache)
                + " (módulo «Generador de códigos QR»).", NARANJA, duracion=9000)
            return
        msg = f"Búsqueda completada: {n_dado} dado(s) de alta, {n_no} sin dar de alta."
        extras = []
        if sin_cache:
            extras.append("sin caché (descárgalos): " + ", ".join(sin_cache))
        if sin_empresa:
            extras.append(f"{sin_empresa} sin empresa asignada")
        if extras:
            msg += " · " + " · ".join(extras)
        self.app.avisar(msg, VERDE if not extras else NARANJA,
                        duracion=9000 if extras else 6000)

    def _buscar_por_empresa(self, por_empresa: dict) -> tuple[int, list[str]]:
        """(hilo) Recorre cada empresa, usa su caché del SIPP y actualiza el
        estatus de sus registros. Devuelve (registros_procesados, empresas_sin_caché)."""
        from core.empresas import ID_POR_EMPRESA
        from core.proveedor_activos import ProveedorSipp, SinCacheActivos
        hechos = 0
        sin_cache: list[str] = []
        for empresa, regs in por_empresa.items():
            proveedor = ProveedorSipp(ID_POR_EMPRESA[empresa])
            # Se consultan ambos campos de cada registro (etiqueta y serie); el
            # match por cualquiera cuenta como dado de alta.
            claves = sorted({v for r in regs
                             for v in ((r.etiqueta or "").strip(),
                                       (r.no_serie or "").strip()) if v})
            try:
                resultados = proveedor.buscar_por_serie(claves)
            except SinCacheActivos:
                sin_cache.append(empresa)
                continue
            for r in regs:
                dado, id_sipp, datos_sipp = False, None, None
                for campo in ((r.etiqueta or "").strip(), (r.no_serie or "").strip()):
                    res = resultados.get(campo) if campo else None
                    if res and res.dado_de_alta:
                        dado, id_sipp, datos_sipp = True, res.id_activo_sipp, res.datos
                        break
                estatus = db.EST_DADO_ALTA if dado else db.EST_NO_DADO_ALTA
                # Al dar de alta se guardan los datos reales del SIPP para
                # consultarlos; si no, se limpian (None) para no dejar rastros.
                db.actualizar_estatus_levantamiento(r.id, estatus, id_sipp, datos_sipp)
                # El SIPP conoce el tipo y el detalle del activo: se preseleccionan
                # en la captura, SIN pisar lo que el usuario ya haya capturado.
                if dado and datos_sipp:
                    try:
                        idt = int(datos_sipp.get("id_tipo"))
                    except (TypeError, ValueError):
                        idt = None
                    id_tipo_nuevo = (idt if idt in TIPOS_ACTIVO
                                     and r.id_tipo_activo is None else None)
                    # Prefill del detalle del insumo solo si aún no hay captura.
                    prefill = _prefill_desde_sipp(datos_sipp) if not r.datos() else None
                    if id_tipo_nuevo is not None or prefill:
                        db.actualizar_datos_levantamiento(
                            r.id, id_tipo_activo=id_tipo_nuevo, datos=prefill)
                hechos += 1
        return hechos, sin_cache

    # ------------------------------------------------ RPA: alta en el SIPP
    def _payload_alta(self, r: "db.Levantamiento") -> tuple:
        """Traduce lo capturado en el formulario dinámico a lo que espera el RPA:
        (tipo, [(ng_model, valor, control)], {etiqueta: valor}, insumo_id).

        Los campos con `detalle=True` son las características del insumo
        (camposDetalle), que el RPA empareja por rótulo. El insumo NO va como campo
        de texto (es de solo lectura): se selecciona por su ID en el modal."""
        datos = r.datos()
        campos, detalles = [], {}
        for campo in campos_de_tipo(r.id_tipo_activo):
            # tipo, insumo y empleado se eligen aparte (por combo/modales del SIPP).
            if campo.clave in ("id_TipoActivo", "nb_NombreInsumo", "nb_Empleado"):
                continue
            valor = (datos.get(campo.clave) or "").strip()
            if not valor:
                continue
            if campo.detalle:
                detalles[campo.etiqueta] = valor
            else:
                campos.append((campo.ng_model, valor, campo.control))
        insumo_id = (datos.get("id_InsumoOrigen") or "").strip()
        empleado_id = (datos.get("id_EmpleadoResguardo") or "").strip()
        return nombre_tipo(r.id_tipo_activo), campos, detalles, insumo_id, empleado_id

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
                    tipo, campos, detalles, insumo_id, empleado_id = self._payload_alta(r)
                    try:
                        await sipp.alta_activo(tipo, campos, detalles, insumo_id, empleado_id)
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

    # --------------------------------------------- RPA: modificación en SIPP
    @staticmethod
    def _a_ng_model_edicion(ng_model: str) -> str:
        """Traduce el localizador del ALTA al del formulario de EDICIÓN del SIPP:
        filtrosAgregar.X -> filtrosEditar.X  y  FH_X -> FH_X_EDITAR."""
        if ng_model.startswith("filtrosAgregar."):
            return ng_model.replace("filtrosAgregar.", "filtrosEditar.", 1)
        # Fechas: FH_X / dt_FH_X -> ..._EDITAR
        if ("FH_" in ng_model) and not ng_model.endswith("_EDITAR"):
            return ng_model + "_EDITAR"
        return ng_model

    def _payload_modificacion(self, r: "db.Levantamiento") -> tuple:
        """Igual que _payload_alta pero con los localizadores del formulario de
        edición. (La modificación no cambia el insumo, así que su id no se usa.)"""
        tipo, campos, detalles, _insumo_id, _empleado_id = self._payload_alta(r)
        campos_edicion = [(self._a_ng_model_edicion(ng), v, c) for ng, v, c in campos]
        return tipo, campos_edicion, detalles

    async def _modificar_en_sipp(self, _e=None) -> None:
        """Reenvía al SIPP (vía RPA) los activos dados de alta que fueron EDITADOS
        en la herramienta (marca `modificado`)."""
        creds = credenciales.cargar()
        if not creds or not creds[0]:
            self.app.avisar("Configura primero las credenciales del SIPP (botón ⚙).", ROJO)
            return
        usuario, contrasena = creds
        pendientes = [r for r in db.listar_levantamiento_por_estatus(db.EST_DADO_ALTA)
                      if r.modificado and r.id_tipo_activo is not None]
        if not pendientes:
            self.app.avisar(
                "No hay cambios por enviar. Edita un activo dado de alta (con el "
                "botón de captura 📋 o sus celdas) y vuelve a intentar.", NARANJA)
            return

        total = len(pendientes)
        bucle = BucleRpa()
        ctrl = ControlRpa(bucle.loop)
        ui_loop = asyncio.get_running_loop()
        txt = ft.Text(f"Preparando… (0/{total})", size=13)
        barra = ft.ProgressBar(value=0)
        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("Aplicando modificaciones en el SIPP"),
            content=ft.Container(
                ft.Column([txt, barra,
                           ft.Text("Se abrirá un navegador; no lo cierres.",
                                   size=11, color=GRIS)], tight=True, spacing=12),
                width=420),
            actions=[ft.TextButton("Detener", on_click=lambda _e: ctrl.detener())],
        )
        self.page.show_dialog(dlg)
        self.page.update()

        def avance(i: int, nombre: str) -> None:
            def aplicar() -> None:
                txt.value = f"({i}/{total}) {nombre}"
                barra.value = i / total
                try:
                    dlg.update()
                except (RuntimeError, AssertionError):
                    pass
            ui_loop.call_soon_threadsafe(aplicar)

        exitosos, fallidos, omitidos = 0, [], []

        async def flujo() -> None:
            nonlocal exitosos
            async with SesionSipp(headless=False) as sipp:
                await sipp.login(usuario, contrasena)
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
                    _tipo, campos, detalles = self._payload_modificacion(r)
                    try:
                        no_aplicados = await sipp.modificar_activo(
                            r.no_serie, campos, detalles)
                        db.actualizar_datos_levantamiento(r.id, modificado=False)
                        exitosos += 1
                        if no_aplicados:
                            omitidos.append(f"{r.nombre_insumo}: {len(no_aplicados)} campo(s)")
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
            self.app.avisar(f"Proceso detenido. {exitosos} activo(s) actualizado(s).",
                            NARANJA)
        elif fallidos:
            self.app.avisar(
                f"{exitosos} actualizado(s), {len(fallidos)} con error: {fallidos[0]}",
                ROJO, duracion=9000)
        elif omitidos:
            self.app.avisar(
                f"{exitosos} actualizado(s). Sin aplicar (no existen en edición): "
                + "; ".join(omitidos[:3]), NARANJA, duracion=9000)
        else:
            self.app.avisar(f"{exitosos} activo(s) actualizado(s) en el SIPP.", VERDE)

    # ------------------------------------ actualizar catálogo de insumos
    def _actualizar_sipp(self, _e=None) -> None:
        """Abre el modal para elegir empresa y actualizar su información del SIPP
        (insumos + activos) y los empleados (global)."""
        from ui.actualizar_sipp import DialogoActualizarSipp

        def _fijar(nombre: str) -> None:
            self.dd_empresa.value = nombre
            self._safe_update()

        DialogoActualizarSipp(self.app, set_empresa=_fijar,
                              al_terminar=self._refrescar).abrir()

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
