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

from core import archivos, compras_sipp, credenciales, db, rutas
from core.empresas import ID_POR_EMPRESA
from core.rpa_sipp import BucleRpa, ControlRpa, ErrorSipp, RpaDetenido, SesionSipp
from core.tipos_activo import ID_POR_NOMBRE, TIPOS_ACTIVO, campos_de_tipo, nombre_tipo
from ui.captura_activo import DialogoCapturaActivo
from ui.carga_masiva import DialogoCargaMasiva
from ui.comun import GRIS, NARANJA, NOMBRES_EMPRESAS, ROJO, VERDE
from ui.componentes import (GUTTER_SCROLL, Modal, Pestanas, boton_herramienta,
                            boton_primario, boton_secundario, buscador,
                            campo_opciones, campo_tabla_opciones,
                            campo_tabla_texto, campo_texto, tarjeta_seccion)
from ui.tabla_responsiva import ColumnaTabla, FilaDatos, TablaResponsiva

# Extensiones de imagen aceptadas para el levantamiento (sin PDF: son fotos).
IMG_EXT = ["png", "jpg", "jpeg", "tif", "tiff", "bmp"]

# Similitud mínima (0-1) para tratar una etiqueta como "posible coincidencia" con
# una del SIPP (errores de dedo / un dígito faltante). Ver ProveedorSipp.
_UMBRAL_PARCIAL = 0.85

# El selector de empresa/sucursal del portal es frágil, así que el RPA de ALTA
# entra SIEMPRE con una empresa/sucursal estable y la empresa/sucursal real del
# activo se fija dentro del formulario de alta (en el RESGUARDO, que es donde queda
# asignado el activo).
_EMPRESA_RPA = "Aske"
_SUCURSAL_RPA = "Corporativo"
# Campos del formulario de alta que llevan la empresa / la sucursal del activo.
# Solo RESGUARDO: los de compra (id_*Agregar) viven en la sección "datos de compra",
# que está oculta por defecto, y tratar de llenarlos colgaba el RPA.
_CLAVES_EMPRESA = ("id_EmpresaResguardo",)
_CLAVES_SUCURSAL = ("id_SucursalResguardo",)
# Campos de la sección de compra que se OMITEN (sección oculta por defecto).
_CLAVES_COMPRA_OMITIR = ("id_EmpresaAgregar", "id_SucursalAgregar")

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
        # Van en una tarjeta de sección, con la etiqueta ARRIBA de cada campo.
        # Sin `width`: cada bloque se lleva un tercio del panel vía `expand`, así
        # el reparto sigue al ancho de la ventana en vez de quedar fijo.
        bloque_emp, self.dd_empresa = campo_opciones(
            "Empresa", NOMBRES_EMPRESAS, hint="Seleccionar empresa")
        bloque_suc, self.tf_sucursal = campo_texto("Sucursal")
        bloque_dep, self.tf_departamento = campo_texto("Departamento")
        for bloque in (bloque_emp, bloque_suc, bloque_dep):
            bloque.expand = True
        contexto = ft.Column(
            [
                ft.Text("Datos del levantamiento (se aplican a las imágenes que subas; "
                        "puedes ajustarlos por fila):",
                        theme_style=ft.TextThemeStyle.BODY_MEDIUM, color=GRIS),
                # Sin `wrap`: con `expand` los tres reparten SIEMPRE el ancho en
                # tercios; envolver los devolvería a su tamaño natural.
                ft.Row([bloque_emp, bloque_suc, bloque_dep], spacing=16,
                       vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ],
            spacing=12, tight=True,
        )
        contexto = tarjeta_seccion(contexto)

        # Barra de carga + búsqueda.
        self.progreso = ft.ProgressRing(width=22, height=22, stroke_width=3, visible=False)
        self.estado = ft.Text("", size=12, color=GRIS)
        barra_acciones = ft.Row(
            [
                boton_primario("Subir archivos", ft.Icons.UPLOAD_FILE,
                               self._subir_archivos),
                boton_secundario("Subir carpeta", ft.Icons.FOLDER_OPEN,
                                 self._subir_carpeta),
                boton_secundario("Subir ZIP", ft.Icons.FOLDER_ZIP, self._subir_zip,
                                 tooltip="Carpeta comprimida del levantamiento: "
                                         "se extrae y se procesa igual"),
                boton_secundario("Carga masiva (Excel)", ft.Icons.TABLE_VIEW,
                                 self.dialogo_carga.abrir,
                                 tooltip="Importa un inventario completo desde Excel"),
                boton_secundario("Buscar en SIPP", ft.Icons.SEARCH, self._buscar),
                boton_secundario("Actualizar información del SIPP", ft.Icons.SYNC,
                                 self._actualizar_sipp,
                                 tooltip="Descarga del SIPP, para la empresa de "
                                         "arriba, sus insumos y activos, más el "
                                         "catálogo de empleados (global)"),
                self.progreso,
                self.estado,
            ],
            spacing=8, run_spacing=8, wrap=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        # Pestañas (Todos / Dados de alta / No dados de alta) como control
        # segmentado: una pista gris con la activa en relieve.
        self._tabs = Pestanas(
            [
                (_TAB_TODOS, "Todos", ft.Icons.LIST_ALT),
                (db.EST_DADO_ALTA, "Dados de alta", ft.Icons.CHECK_CIRCLE),
                (db.EST_NO_DADO_ALTA, "No dados de alta", ft.Icons.PENDING_ACTIONS),
            ],
            al_cambiar=self._cambiar_tab, activa=self._tab)

        # Buscador: con miles de activos importados es la forma práctica de
        # aislar un grupo (p. ej. todas las LAPTOP) y clasificarlo de golpe.
        # Filtra al pulsar Enter (no en cada tecla: repintar la tabla cuesta).
        self.tf_buscar = buscador(
            "Buscar insumo, etiqueta, serie o ubicación… (Enter)",
            on_submit=self._aplicar_filtro)
        self._filtro = ""
        self._btn_limpiar = ft.IconButton(
            icon=ft.Icons.CLOSE, icon_size=18, tooltip="Limpiar búsqueda",
            visible=False, on_click=self._limpiar_filtro)

        # Herramientas sobre la selección (a la derecha de las pestañas).
        self.barra_masiva = ft.Row(
            [
                boton_herramienta("Seleccionar todos", ft.Icons.SELECT_ALL,
                                  self._seleccionar_todos),
                boton_herramienta("Asignar tipo", ft.Icons.CATEGORY,
                                  self._abrir_asignar_tipo,
                                  tooltip="Asigna el tipo de activo a los seleccionados"),
                boton_herramienta("Eliminar seleccionados", ft.Icons.DELETE_OUTLINE,
                                  self._eliminar_seleccionados, destructivo=True),
            ],
            spacing=4, wrap=True, vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        # --- Filtros por columna (estilo Excel) -------------------------------
        # Categóricos (empresa/sucursal/departamento) como desplegable de valores
        # distintos; texto (insumo/etiqueta/serie) como "contiene". Se combinan
        # entre sí y con el buscador global; el filtrado lo hace SQLite.
        self._filtros_col: dict = {}
        # Rótulo corto para la opción "sin filtro" (así no se corta en el combo).
        self._TODOS = {"empresa": "Todas", "sucursal": "Todas",
                       "departamento": "Todos"}
        # Mismo ancho para TODOS los controles del filtro; la altura la fija el
        # estándar de Material (los componentes no la tocan, para que Dropdown y
        # TextField sigan alineando sus bordes). El rótulo va FLOTANTE (encajado
        # en el borde), como en un modal, para no ganar altura sobre la fila.
        _WF = 172

        def _mk_dd(col, etiqueta):
            _, campo = campo_opciones(
                etiqueta, [self._TODOS[col]], valor=self._TODOS[col], width=_WF,
                on_change=lambda e, c=col: self._set_filtro_col(c, e.control.value),
                flotante=True)
            return campo

        def _mk_tf(col, etiqueta):
            _, campo = campo_texto(
                etiqueta, width=_WF, flotante=True,
                on_submit=lambda e, c=col: self._set_filtro_col(c, e.control.value),
                on_blur=lambda e, c=col: self._set_filtro_col(c, e.control.value))
            return campo

        self.dd_f_empresa = _mk_dd("empresa", "Empresa")
        self.dd_f_sucursal = _mk_dd("sucursal", "Sucursal")
        self.dd_f_departamento = _mk_dd("departamento", "Departamento")
        self.tf_f_insumo = _mk_tf("nombre_insumo", "Nombre insumo")
        self.tf_f_etiqueta = _mk_tf("etiqueta", "Etiqueta")
        self.tf_f_serie = _mk_tf("no_serie", "No. de serie")
        self._btn_limpiar_filtros = boton_herramienta(
            "Limpiar filtros", ft.Icons.FILTER_ALT_OFF, self._limpiar_filtros_col)
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
        # Sin etiqueta: `campo_opciones` devuelve el campo directo (el rótulo lo
        # pone el texto "por página:" que va al lado).
        _, self._dd_por_pagina = campo_opciones(
            None, [str(n) for n in _POR_PAGINA], valor=str(self._por_pagina),
            width=90, on_change=self._cambiar_por_pagina)
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
        # SIN scroll ni expand: la tabla se pinta a su alto natural (el de todas
        # las filas de la página) y quien desplaza es la pantalla completa.
        self._area_tabla = ft.Column([self.tabla.control], spacing=0, tight=True)

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
            # Alto fijo en vez de `expand`: ahora vive dentro de una columna con
            # scroll, donde una altura sin acotar no es válida.
            alignment=ft.Alignment(0, 0), height=320, visible=False,
        )

        cuerpo = ft.Column(
            [
                contexto,
                barra_acciones,
                ft.Divider(height=1, color=ft.Colors.OUTLINE_VARIANT),
                # Pestañas a la izquierda, herramientas de selección a la derecha.
                ft.Row([self._tabs.control, self.barra_masiva],
                       alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                       vertical_alignment=ft.CrossAxisAlignment.CENTER, wrap=True,
                       run_spacing=8),
                # Buscador a la izquierda, acción de RPA de la pestaña a la derecha.
                ft.Row([ft.Row([self.tf_buscar, self._btn_limpiar], spacing=4,
                               tight=True),
                        self._barra_rpa],
                       alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                       vertical_alignment=ft.CrossAxisAlignment.CENTER),
                # Filtros por columna (estilo Excel): mi feature, se combina con
                # el buscador global y con las pestañas.
                self.barra_filtros,
                # Antes iban superpuestos en un Stack con `expand`; como se
                # alternan por `visible`, apilarlos basta y evita la altura sin
                # acotar que un Stack expandido metería en la columna con scroll.
                self._area_tabla,
                self.txt_vacio,
                self.barra_paginacion,
            ],
            spacing=16, tight=True,
            # STRETCH: sin esto una tarjeta sin `width` se encoge a su contenido
            # en vez de ocupar el ancho disponible de la pantalla.
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )
        # La barra de scroll se dibuja SOBRE el borde derecho del área con
        # scroll. Como la tabla mide su ancho contra ese mismo borde, sin
        # reservarle sitio se encimaba a la columna de Acciones. El padding
        # derecho del contenido la deja por fuera y la medición ya la descuenta.
        self.contenido = ft.Column(
            [ft.Container(cuerpo, padding=ft.Padding.only(right=GUTTER_SCROLL))],
            expand=True, spacing=0, scroll=ft.ScrollMode.AUTO,
        )
        self._actualizar_barra_rpa()

    # ------------------------------------------------------ pestañas
    def _cambiar_tab(self, clave: str) -> None:
        """Callback de `Pestanas` (ya repintó el segmentado antes de llamarnos)."""
        self._tab = clave
        self._pagina = 0  # cada pestaña arranca en su primera página
        self._actualizar_barra_rpa()
        # `_refrescar()` termina actualizando la pantalla; un segundo
        # `_safe_update()` aquí volvía a mandar los ~790 controles.
        self._refrescar()

    def _actualizar_barra_rpa(self) -> None:
        """Muestra el botón de RPA correspondiente a la pestaña activa."""
        if self._tab == db.EST_NO_DADO_ALTA:
            self._barra_rpa.content = boton_primario(
                "Iniciar registro en SIPP", ft.Icons.SMART_TOY,
                self._iniciar_registro_sipp,
                tooltip="Da de alta en el SIPP los activos que ya tienen datos capturados")
        elif self._tab == db.EST_DADO_ALTA:
            self._barra_rpa.content = boton_primario(
                "Realizar modificación en SIPP", ft.Icons.EDIT_NOTE,
                self._modificar_en_sipp,
                tooltip="Reenvía al SIPP los activos que editaste después de darlos de alta")
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
            dd.options = ([ft.DropdownOption(key=self._TODOS[col], text=self._TODOS[col])]
                          + [ft.DropdownOption(key=v, text=v) for v in vals])
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
        # `refrescar=False`: el `_safe_update()` del final de este método ya
        # manda la pantalla entera; dejar que la tabla se actualice por su
        # cuenta duplicaría el envío de su cuerpo.
        self.tabla.set_contenido([self._fila(r) for r in pagina], refrescar=False)
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
        chk = ft.Checkbox(
            value=r.id in self._seleccionados,
            on_change=lambda e, i=r.id: self._toggle_sel(i, e.control.value))
        # Celdas editables de ubicación (se persisten sin reconstruir la tabla,
        # para no perder el foco ni el scroll mientras se capturan). El estilo y
        # el alto común de los tres viven en ui/componentes.py; aquí solo se fija
        # el ancho de la celda.
        _W = 145
        _alta = r.estatus_registro == db.EST_DADO_ALTA  # editar => marcar modificado
        # Sucursal y departamento se eligen del catálogo del SIPP de la empresa de
        # la fila (desplegable). Si esa empresa no tiene catálogo descargado, se cae
        # al campo de texto. El AREA del Excel NO llena el departamento (no coincide
        # con el del SIPP): se elige aquí.
        idemp = ID_POR_EMPRESA.get(r.empresa or "")
        sucs = db.listar_sucursales_sipp(idemp) if idemp is not None else []
        deptos = db.listar_departamentos(idemp) if idemp is not None else []
        emp = campo_tabla_opciones(
            NOMBRES_EMPRESAS, valor=r.empresa or None, ancho=_W,
            page=self.page, titulo="Elegir empresa",
            on_change=lambda e, i=r.id, a=_alta: self._set_empresa_fila(
                i, e.control.value or "", a))
        if sucs:
            suc = campo_tabla_opciones(
                sucs, valor=r.sucursal or None, ancho=_W, page=self.page,
                titulo="Elegir sucursal",
                on_change=lambda e, i=r.id, a=_alta: self._set_ubic(
                    i, sucursal=e.control.value or "", ya_de_alta=a))
        else:
            suc = campo_tabla_texto(
                valor=r.sucursal or "", ancho=_W,
                on_blur=lambda e, i=r.id, a=_alta: self._set_ubic(
                    i, sucursal=(e.control.value or "").strip(), ya_de_alta=a))
        if deptos:
            dep = campo_tabla_opciones(
                deptos, valor=r.departamento or None, ancho=_W, page=self.page,
                titulo="Elegir departamento",
                on_change=lambda e, i=r.id, a=_alta: self._set_ubic(
                    i, departamento=e.control.value or "", ya_de_alta=a))
        else:
            dep = campo_tabla_texto(
                valor=r.departamento or "", ancho=_W,
                on_blur=lambda e, i=r.id, a=_alta: self._set_ubic(
                    i, departamento=(e.control.value or "").strip(), ya_de_alta=a))
        # Datos del SIPP (solo dados de alta). Una POSIBLE coincidencia (parcial)
        # se distingue en ámbar y con su propia acción para resolverla.
        info = r.info_sipp() if r.estatus_registro == db.EST_DADO_ALTA else {}
        es_parcial = bool(info.get("parcial"))
        if es_parcial:
            estatus = ft.Text("Posible coincidencia", size=12, color=NARANJA,
                              weight=ft.FontWeight.W_500)
        else:
            etiqueta, color = _ESTATUS_UI.get(r.estatus_registro, ("—", GRIS))
            estatus = ft.Text(etiqueta, size=12, color=color,
                              weight=ft.FontWeight.W_500)
        capturado = r.id_tipo_activo is not None
        controles_accion = []
        if es_parcial:
            # Resolver: comparar con el activo del SIPP y decidir si es el mismo.
            controles_accion.append(ft.IconButton(
                icon=ft.Icons.RULE, icon_size=20, icon_color=NARANJA,
                tooltip="Resolver posible coincidencia (¿es el mismo activo?)",
                on_click=lambda _e, reg=r: self._resolver_parcial(reg)))
        elif info:
            # Dado de alta confirmado: consultar la información del SIPP.
            controles_accion.append(ft.IconButton(
                icon=ft.Icons.INFO_OUTLINE, icon_size=20, icon_color=VERDE,
                tooltip="Ver información registrada en el SIPP",
                on_click=lambda _e, reg=r: self._ver_info_sipp(reg)))
        # Buscar factura en el sistema: solo si el activo tiene un No. de serie
        # válido (existe y no coincide con la etiqueta).
        if compras_sipp.serie_valida(r.no_serie, r.etiqueta):
            controles_accion.append(ft.IconButton(
                icon=ft.Icons.RECEIPT_LONG, icon_size=20,
                tooltip="Buscar factura en el sistema (por No. de serie)",
                on_click=lambda _e, reg=r: self._buscar_factura_sistema(reg)))
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
            emp,
            suc,
            dep,
            r.nombre_insumo,
            r.etiqueta or "—",
            r.no_serie or "—",
            estatus,
            acciones,
        ], bgcolor=ft.Colors.with_opacity(0.12, NARANJA) if es_parcial else None)

    def _set_ubic(self, id_lev: int, empresa: "str | None" = None,
                  sucursal: "str | None" = None, departamento: "str | None" = None,
                  ya_de_alta: bool = False) -> None:
        """Persiste la edición de empresa/sucursal/departamento de una fila. No
        reconstruye la tabla (conserva foco y scroll durante la captura).

        Si el activo ya está dado de alta en el SIPP, lo marca como MODIFICADO
        para que el RPA de modificación lo reenvíe al portal."""
        db.actualizar_ubicacion_levantamiento(
            id_lev, empresa=empresa, sucursal=sucursal, departamento=departamento)
        if ya_de_alta:
            db.actualizar_datos_levantamiento(id_lev, modificado=True)

    def _set_empresa_fila(self, id_lev: int, empresa: str, ya_de_alta: bool) -> None:
        """Fija la empresa de la fila y repinta: sucursal y departamento dependen de
        la empresa, así que sus desplegables deben rearmarse con el catálogo nuevo."""
        self._set_ubic(id_lev, empresa=empresa, ya_de_alta=ya_de_alta)
        self._refrescar()

    async def _buscar_factura_sistema(self, reg: "db.Levantamiento") -> None:
        """Busca en la bandeja de compras la factura del activo por su No. de serie,
        la descarga y ofrece abrirla; informa proveedor, folio y precio del CFDI."""
        if not compras_sipp.serie_valida(reg.no_serie, reg.etiqueta):
            self.app.avisar("El activo no tiene un No. de serie válido para buscar "
                            "factura.", NARANJA)
            return
        creds = credenciales.cargar()
        if not creds or not creds[0]:
            self.app.avisar("Configura primero las credenciales del SIPP (botón ⚙).", ROJO)
            return
        usuario, contrasena = creds
        id_empresa = ID_POR_EMPRESA.get((reg.empresa or "").strip())

        modal = Modal(self.page, "Buscar factura en sistema",
                      subtitulo=f"Serie: {reg.no_serie}", ancho=440)
        modal.cuerpo.controls = [ft.Text("Buscando la factura en el SIPP…", size=13),
                                 ft.ProgressBar()]
        modal.abrir()

        entrada, info, ruta, error = None, None, None, None

        async def flujo() -> None:
            nonlocal entrada, info, ruta, error
            try:
                async with SesionSipp(headless=True) as sipp:
                    await sipp.login(usuario, contrasena)
                    entrada = await compras_sipp.buscar_entrada_por_serie(
                        sipp, reg.no_serie, id_empresa)
                    if entrada is not None:
                        info = await compras_sipp.datos_factura(sipp, entrada)
                        if entrada.tiene_factura:
                            ruta = await compras_sipp.descargar_factura(
                                sipp, entrada, os.path.join(rutas.DATOS, "facturas"))
            except Exception as exc:  # noqa: BLE001 — se reporta al usuario
                error = str(exc)

        bucle = BucleRpa()
        try:
            await asyncio.wrap_future(bucle.enviar(flujo()))
        finally:
            bucle.cerrar()
            modal.cerrar()

        if error:
            self.app.avisar(f"No se pudo buscar la factura: {error}", ROJO, duracion=8000)
            return
        if entrada is None:
            self.app.avisar(f"No se encontró factura para la serie «{reg.no_serie}».",
                           NARANJA, duracion=7000)
            return
        precio = (info or {}).get("precio")
        folio = (info or {}).get("folio")
        partes = []
        if entrada.proveedor:
            partes.append(entrada.proveedor)
        if folio:
            partes.append(f"folio {folio}")
        if precio is not None:
            partes.append(f"${precio:,.2f}")
        resumen = (" · " + " · ".join(partes)) if partes else ""
        if ruta:
            self.app.avisar(
                f"Factura encontrada{resumen}.", VERDE, accion="Abrir",
                on_accion=lambda _e, x=str(ruta): self.app.abrir_en_sistema(x),
                duracion=9000)
        else:
            self.app.avisar(
                f"Entrada de compra encontrada, sin PDF de factura (pendiente){resumen}.",
                NARANJA, duracion=8000)

    def _actualizar_conteos(self) -> None:
        """Conteos por pestaña con UNA consulta agregada (no listando la tabla)."""
        c = db.contar_levantamiento_por_estatus()
        conteos = {_TAB_TODOS: c.get("total", 0),
                   db.EST_DADO_ALTA: c.get(db.EST_DADO_ALTA, 0),
                   db.EST_NO_DADO_ALTA: c.get(db.EST_NO_DADO_ALTA, 0)}
        for clave, n in conteos.items():
            self._tabs.set_conteo(clave, n)

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
                    boton_herramienta("Cancelar",
                                      on_click=lambda _e: self.page.pop_dialog()),
                    boton_primario("Eliminar", ft.Icons.DELETE, eliminar),
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

        bloque_tipo, dd = campo_opciones(
            "Tipo de activo", list(TIPOS_ACTIVO.values()), width=340,
            hint="Elige un tipo")

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
                         bloque_tipo,
                         ft.Text("Los campos particulares de cada tipo se capturan "
                                 "después, en el formulario de cada activo.",
                                 size=11, color=GRIS)],
                        spacing=12, tight=True),
                    width=380),
                actions=[
                    boton_herramienta("Cancelar",
                                      on_click=lambda _e: self.page.pop_dialog()),
                    boton_primario("Asignar", ft.Icons.CHECK, aplicar),
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
            ("fecha_adquisicion", "Fecha de adquisición / levantamiento"),
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
        modal = Modal(self.page, "Información registrada en el SIPP", ancho=520)
        modal.set_acciones([boton_secundario("Cerrar", on_click=lambda _e: modal.cerrar())])
        modal.cuerpo.controls = filas
        modal.abrir()

    def _resolver_parcial(self, reg: "db.Levantamiento") -> None:
        """Diálogo de una POSIBLE coincidencia: compara la etiqueta del
        levantamiento con la del SIPP parecida (y su detalle) para que el usuario
        decida si es el mismo activo (adopta la etiqueta del SIPP) o uno nuevo."""
        info = reg.info_sipp()
        if not info.get("parcial"):
            self.app.avisar("Este registro ya no es una posible coincidencia.", NARANJA)
            return
        etq_sipp = str(info.get("etiqueta_sipp") or info.get("etiqueta") or "").strip()
        sim = info.get("similitud")
        sim_txt = f"{round(float(sim) * 100)}%" if sim is not None else "—"

        def fila(etq, valor):
            return ft.Row(
                [ft.Text(f"{etq}:", size=13, weight=ft.FontWeight.W_600, width=170,
                         color=GRIS),
                 ft.Text(str(valor or "—"), size=13, selectable=True, expand=True)],
                vertical_alignment=ft.CrossAxisAlignment.START)

        cuerpo = [
            ft.Text("La etiqueta del levantamiento se parece a una del SIPP, pero no "
                    "es idéntica (posible error de dedo o un dígito faltante). "
                    "Revisa el detalle y decide:", size=12, color=GRIS),
            ft.Divider(),
            fila("Etiqueta del levantamiento", reg.etiqueta or "—"),
            fila("Etiqueta en el SIPP", f"{etq_sipp}   (similitud {sim_txt})"),
            ft.Divider(),
            fila("Insumo (SIPP)", info.get("insumo")),
            fila("No. de serie (SIPP)", info.get("serie")),
            fila("Empresa / Sucursal", f"{info.get('empresa') or '—'} / "
                                       f"{info.get('sucursal') or '—'}"),
            fila("Departamento", info.get("departamento")),
            fila("Ubicación", info.get("ubicacion")),
            fila("Empleado resguardo", info.get("empleado")),
        ]
        modal = Modal(self.page, "Resolver posible coincidencia",
                      subtitulo=reg.nombre_insumo, ancho=560)
        modal.cuerpo.controls = cuerpo

        def es_el_mismo(_e=None):
            modal.cerrar()
            self._confirmar_coincidencia(reg, etq_sipp, info)

        def es_nuevo(_e=None):
            modal.cerrar()
            # Insumo nuevo: pasa a No dados de alta (el SIPP le generará su etiqueta
            # al darlo de alta); se limpian los datos del SIPP de la coincidencia.
            db.actualizar_estatus_levantamiento(reg.id, db.EST_NO_DADO_ALTA, None, None)
            self._refrescar()
            self.app.avisar("Marcado como insumo nuevo (No dados de alta).", VERDE)

        modal.set_acciones([
            boton_herramienta("Cancelar", on_click=lambda _e: modal.cerrar()),
            boton_secundario("Es un insumo nuevo", ft.Icons.FIBER_NEW, es_nuevo),
            boton_primario(f"Es el mismo (usar {etq_sipp})", ft.Icons.CHECK, es_el_mismo),
        ])
        modal.abrir()

    def _confirmar_coincidencia(self, reg, etq_sipp: str, info: dict) -> None:
        """Adopta la etiqueta del SIPP: el registro queda como Dado de alta
        confirmado (sin marca de parcial) y se precarga su detalle."""
        db.fijar_etiqueta_levantamiento(reg.id, etq_sipp)
        datos_sipp = {k: v for k, v in info.items()
                      if k not in ("parcial", "similitud", "etiqueta_sipp")}
        db.actualizar_estatus_levantamiento(
            reg.id, db.EST_DADO_ALTA, etq_sipp, datos_sipp)
        # Prefill del tipo/detalle si aún no hay captura (ya es una coincidencia
        # confirmada).
        try:
            idt = int(datos_sipp.get("id_tipo"))
        except (TypeError, ValueError):
            idt = None
        id_tipo_nuevo = idt if idt in TIPOS_ACTIVO and reg.id_tipo_activo is None else None
        prefill = _prefill_desde_sipp(datos_sipp) if not reg.datos() else None
        if id_tipo_nuevo is not None or prefill:
            db.actualizar_datos_levantamiento(reg.id, id_tipo_activo=id_tipo_nuevo,
                                              datos=prefill)
        self._refrescar()
        self.app.avisar(f"Etiqueta del SIPP adoptada: {etq_sipp}. Dado de alta.", VERDE)

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
        # Se evalúan TODOS los registros: los que tienen etiqueta se verifican contra
        # el listado del SIPP; los que NO tienen etiqueta se dan por NO dados de alta
        # (criterio), en vez de quedarse en "Pendiente".
        registros = db.listar_levantamiento()
        if not registros:
            self.app.avisar("No hay activos en el levantamiento para buscar.", ROJO)
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
            # Criterio: el identificador del alta es la ETIQUETA. Sin etiqueta se da
            # por hecho que NO está dado de alta (ni se busca). Con etiqueta se busca
            # EXACTA en el listado del SIPP: si está -> dado de alta; si no -> no dado
            # de alta (se dará de alta). Sin serie ni coincidencia parcial.
            etiquetas = sorted({(r.etiqueta or "").strip()
                                for r in regs if (r.etiqueta or "").strip()})
            resultados = {}
            if etiquetas:   # sin etiquetas no hace falta la caché (todos serán no dado)
                try:
                    resultados = proveedor.buscar_por_etiqueta(etiquetas)
                except SinCacheActivos:
                    sin_cache.append(empresa)
                    # Sin caché no se pueden verificar los que tienen etiqueta; pero
                    # los que NO tienen etiqueta sí se marcan no dado de alta.
                    for r in regs:
                        if not (r.etiqueta or "").strip():
                            db.actualizar_estatus_levantamiento(
                                r.id, db.EST_NO_DADO_ALTA, None, None)
                            hechos += 1
                    continue
            for r in regs:
                etq = (r.etiqueta or "").strip()
                res = resultados.get(etq) if etq else None
                if res and res.dado_de_alta:
                    dado, datos_sipp, id_sipp = True, res.datos, res.id_activo_sipp
                else:
                    dado, datos_sipp, id_sipp = False, None, None
                estatus = db.EST_DADO_ALTA if dado else db.EST_NO_DADO_ALTA
                db.actualizar_estatus_levantamiento(r.id, estatus, id_sipp, datos_sipp)
                # Prefill del tipo/detalle desde el SIPP en la coincidencia exacta.
                if dado and datos_sipp:
                    try:
                        idt = int(datos_sipp.get("id_tipo"))
                    except (TypeError, ValueError):
                        idt = None
                    id_tipo_nuevo = (idt if idt in TIPOS_ACTIVO
                                     and r.id_tipo_activo is None else None)
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
        emp_real = (r.empresa or "").strip()
        suc_real = (r.sucursal or "").strip()
        campos, detalles = [], {}
        for campo in campos_de_tipo(r.id_tipo_activo):
            # tipo, insumo y empleado se eligen aparte (por combo/modales del SIPP).
            if campo.clave in ("id_TipoActivo", "nb_NombreInsumo", "nb_Empleado"):
                continue
            # La empresa/sucursal de COMPRA viven en una sección oculta por defecto;
            # llenarlas colgaba el RPA, así que se omiten (la empresa/sucursal real
            # se registra en el resguardo).
            if campo.clave in _CLAVES_COMPRA_OMITIR:
                continue
            # La empresa/sucursal de RESGUARDO se FIJAN con las del activo: como el
            # RPA entra con una empresa/sucursal estable, aquí se registra la que
            # corresponde. La empresa se emite antes que la sucursal (esta depende
            # de aquella), orden que respeta campos_de_tipo.
            if campo.clave in _CLAVES_EMPRESA and emp_real:
                valor = emp_real
            elif campo.clave in _CLAVES_SUCURSAL and suc_real:
                valor = suc_real
            else:
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
        modal = Modal(self.page, "Registrando activos en el SIPP", ancho=460,
                      acciones=[boton_herramienta("Detener",
                                                  on_click=lambda _e: ctrl.detener(),
                                                  destructivo=True)])
        modal.cuerpo.controls = [
            txt, barra,
            ft.Text("Se abrirá un navegador; no lo cierres.", size=11, color=GRIS)]
        modal.abrir()

        def avance(i: int, nombre: str) -> None:
            """Actualiza el progreso desde el hilo del RPA (marshalado a la UI)."""
            def aplicar() -> None:
                txt.value = f"({i}/{total}) {nombre}"
                barra.value = i / total
                modal.refrescar()
            ui_loop.call_soon_threadsafe(aplicar)

        from core import reporte_altas
        resultados: list[dict] = []      # una fila por activo (para el reporte)
        errores_generales: list[str] = []

        async def flujo() -> None:
            async with SesionSipp(headless=False) as sipp:
                await sipp.login(usuario, contrasena)
                # El RPA entra con una empresa/sucursal ESTABLE (el selector del
                # portal es frágil); la empresa/sucursal real de cada activo se fija
                # en el formulario de alta (ver _payload_alta).
                try:
                    await sipp.seleccionar_empresa_sucursal(_EMPRESA_RPA, _SUCURSAL_RPA)
                except ErrorSipp as exc:
                    errores_generales.append(
                        f"Selección de empresa/sucursal ({_EMPRESA_RPA}/"
                        f"{_SUCURSAL_RPA}): {exc}")
                for i, r in enumerate(pendientes, 1):
                    await ctrl.punto_control()
                    avance(i, r.nombre_insumo)
                    fila = {"insumo": r.nombre_insumo, "etiqueta": r.etiqueta or "",
                            "serie": r.no_serie or "", "estatus": reporte_altas.PENDIENTE,
                            "observacion": "", "_empresa": r.empresa or ""}
                    tipo, campos, detalles, insumo_id, empleado_id = self._payload_alta(r)
                    # Sin insumo resuelto: no se puede dar de alta -> se salta y se
                    # anota, sin intentar (el alta fallaría en el SIPP).
                    if not insumo_id:
                        fila["observacion"] = ("No se encontró el insumo en el catálogo "
                                               "del SIPP; captúralo en la ficha.")
                        resultados.append(fila)
                        continue
                    try:
                        # El alta devuelve la ETIQUETA que el SIPP generó; se guarda
                        # en el registro (id del activo = su etiqueta).
                        etiqueta_gen = await sipp.alta_activo(
                            tipo, campos, detalles, insumo_id, empleado_id,
                            serie=r.no_serie or "", etiqueta_actual=r.etiqueta or "",
                            empresa=r.empresa or "", sucursal=r.sucursal or "")
                        db.actualizar_estatus_levantamiento(
                            r.id, db.EST_DADO_ALTA, etiqueta_gen or None)
                        if etiqueta_gen:
                            db.fijar_etiqueta_levantamiento(r.id, etiqueta_gen)
                            fila["etiqueta"] = etiqueta_gen
                        fila["estatus"] = reporte_altas.ALTA
                        fila["observacion"] = (f"Etiqueta generada: {etiqueta_gen}"
                                               if etiqueta_gen else "Alta registrada")
                    # Un registro con error (insumo no hallado en el modal, campo, red…)
                    # NO aborta el lote: se anota y se sigue con el siguiente.
                    except Exception as exc:  # noqa: BLE001 — se reporta en el reporte
                        fila["observacion"] = str(exc)
                    resultados.append(fila)

                # Confirmación final: por cada empresa, se trae su listado del SIPP y
                # se verifica que las etiquetas generadas estén presentes (que el alta
                # realmente quedó). Lo que no aparezca se marca para revisar.
                avance(total, "Confirmando altas…")
                await self._confirmar_altas(sipp, resultados)

        detenido = False
        try:
            await asyncio.wrap_future(bucle.enviar(flujo()))
        except RpaDetenido:
            detenido = True
        except Exception as exc:  # noqa: BLE001 — se reporta al usuario
            errores_generales.append(str(exc))
        finally:
            bucle.cerrar()
            modal.cerrar()
            self._refrescar()

        self._mostrar_reporte_altas(resultados, detenido, errores_generales)

    async def _confirmar_altas(self, sipp, resultados: list) -> None:
        """Verifica que las altas hayan quedado en el SIPP: por empresa, trae su
        listado de activos y comprueba que la etiqueta generada de cada alta esté
        presente. Anota la confirmación en cada fila; lo que no aparezca se marca
        como pendiente para revisar."""
        from collections import defaultdict

        from core import reporte_altas
        from core.catalogos_sipp import _query
        from core.empresas import ID_POR_EMPRESA

        por_empresa: dict = defaultdict(list)
        for fila in resultados:
            if fila.get("estatus") == reporte_altas.ALTA and fila.get("etiqueta"):
                idemp = ID_POR_EMPRESA.get((fila.get("_empresa") or "").strip())
                if idemp is not None:
                    por_empresa[idemp].append(fila)
        for idemp, filas_e in por_empresa.items():
            try:
                idx, filas_sipp = await _query(
                    sipp, "ActivosFijosNuevo", "getListadoActivosFijos",
                    {"id_Empresa": idemp, "sn_Registro": 1})
                col = idx.get("DE_ETIQUETA")
                presentes = {str(f[col]).strip().upper()
                             for f in filas_sipp if col is not None and f[col]}
            except Exception:  # noqa: BLE001 — si falla la consulta, no se confirma
                presentes = None
            for fila in filas_e:
                if presentes is None:
                    fila["observacion"] += "  ·  No se pudo confirmar en el SIPP."
                elif fila["etiqueta"].strip().upper() in presentes:
                    fila["observacion"] += "  ·  Confirmado en el SIPP."
                else:
                    fila["estatus"] = reporte_altas.PENDIENTE
                    fila["observacion"] += "  ·  No aparece en el listado del SIPP (revisar)."

    def _mostrar_reporte_altas(self, filas: list, detenido: bool,
                               errores_generales: list) -> None:
        """Reporte final del proceso de altas: estadísticas (realizadas/pendientes),
        observaciones por activo y opción de exportar a Excel."""
        from core import reporte_altas
        res = reporte_altas.resumen_altas(filas, detenido)

        def stat(n, etiqueta, color):
            return ft.Column(
                [ft.Text(str(n), size=24, weight=ft.FontWeight.BOLD, color=color),
                 ft.Text(etiqueta, size=11, color=GRIS)],
                spacing=0, tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER)

        stats = ft.Row(
            [stat(res["realizadas"], "Realizadas", VERDE),
             stat(res["pendientes"], "Pendientes", NARANJA),
             stat(res["total"], "Total", ft.Colors.ON_SURFACE_VARIANT)],
            alignment=ft.MainAxisAlignment.SPACE_EVENLY)

        lista = ft.ListView(spacing=4, expand=True)
        # Pendientes primero (son las que requieren atención).
        for f in sorted(filas, key=lambda x: x.get("estatus") == reporte_altas.ALTA):
            ok = f.get("estatus") == reporte_altas.ALTA
            titulo = f.get("insumo", "")
            if f.get("etiqueta"):
                titulo += f"  ·  {f['etiqueta']}"
            lista.controls.append(ft.Row(
                [ft.Icon(ft.Icons.CHECK_CIRCLE if ok else ft.Icons.ERROR_OUTLINE,
                         size=16, color=VERDE if ok else NARANJA),
                 ft.Column(
                     [ft.Text(titulo, size=12, weight=ft.FontWeight.W_500,
                              color=ft.Colors.ON_SURFACE),
                      ft.Text(f.get("observacion", ""), size=11, color=GRIS,
                              no_wrap=False)],
                     spacing=0, tight=True, expand=True)],
                spacing=8, vertical_alignment=ft.CrossAxisAlignment.START))

        cuerpo = [stats, ft.Divider()]
        if errores_generales:
            cuerpo.append(ft.Text("Observaciones generales: "
                                  + "; ".join(errores_generales), size=11, color=ROJO,
                                  no_wrap=False))
        cuerpo.append(ft.Container(lista, height=280))

        modal = Modal(self.page, "Reporte de altas",
                      subtitulo="Proceso detenido" if detenido else None, ancho=580)
        modal.cuerpo.controls = cuerpo

        async def _exportar(_e=None) -> None:
            await self._exportar_reporte_altas(filas, detenido, errores_generales)

        modal.set_acciones([
            boton_herramienta("Cerrar", on_click=lambda _e: modal.cerrar()),
            boton_primario("Exportar (Excel)", ft.Icons.DOWNLOAD, _exportar),
        ])
        modal.abrir()

    async def _exportar_reporte_altas(self, filas: list, detenido: bool,
                                      errores_generales: list) -> None:
        from core import reporte_altas
        destino = await self.app.picker.save_file(
            dialog_title="Guardar reporte de altas",
            file_name="Reporte de altas.xlsx", allowed_extensions=["xlsx"])
        if not destino:
            return
        ruta = destino if destino.lower().endswith(".xlsx") else destino + ".xlsx"
        try:
            await asyncio.to_thread(
                reporte_altas.generar_reporte_altas, ruta, filas, detenido,
                errores_generales)
        except Exception as exc:  # noqa: BLE001 — se reporta al usuario
            self.app.avisar(f"No se pudo generar el reporte: {exc}", ROJO)
            return
        self.app.avisar("Reporte exportado.", VERDE, accion="Abrir",
                        on_accion=lambda _e, x=ruta: self.app.abrir_en_sistema(x),
                        duracion=8000)

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
        modal = Modal(self.page, "Aplicando modificaciones en el SIPP", ancho=460,
                      acciones=[boton_herramienta("Detener",
                                                  on_click=lambda _e: ctrl.detener(),
                                                  destructivo=True)])
        modal.cuerpo.controls = [
            txt, barra,
            ft.Text("Se abrirá un navegador; no lo cierres.", size=11, color=GRIS)]
        modal.abrir()

        def avance(i: int, nombre: str) -> None:
            def aplicar() -> None:
                txt.value = f"({i}/{total}) {nombre}"
                barra.value = i / total
                modal.refrescar()
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
                            r.etiqueta, r.no_serie, campos, detalles)
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
            modal.cerrar()
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
