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
from datetime import datetime

import flet as ft

from core import archivos, comparacion_sipp, compras_sipp, credenciales, db, rutas
from core.empresas import ID_POR_EMPRESA
from core.rpa_sipp import (BucleRpa, ControlRpa, ErrorSipp, RpaDetenido,
                           SesionSipp, mensaje_amigable, serie_para_alta)
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


def _fecha_corta(sello: str) -> str:
    """'2026-08-14 15:24:03' -> '14/08/2026 15:24'. Devuelve el crudo si no cuadra."""
    texto = str(sello or "")
    try:
        return datetime.strptime(texto[:16], "%Y-%m-%d %H:%M").strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return texto


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
        # Carga masiva desde Excel: la empresa/sucursal salen de las columnas del
        # propio Excel (ya no hay selectores de contexto en la pantalla).
        self.dialogo_carga = DialogoCargaMasiva(
            app, contexto=self._contexto_actual, al_terminar=self._tras_importar)
        self._construir()

    def _contexto_actual(self) -> tuple:
        """Contexto para la carga masiva de Excel. El Excel estandarizado trae sus
        propias columnas EMPRESA/SUCURSAL, así que no se impone ninguno aquí."""
        return ("", "", "")

    # ------------------------------------------------------------ UI
    def _construir(self) -> None:
        # Barra de carga + búsqueda. El alta de activos se unifica en UN botón
        # ("Subir archivos") que abre un modal con los métodos (carpeta / ZIP /
        # Excel); la empresa y sucursal se piden en el propio modal (carpeta/ZIP),
        # por eso ya no hay campos de contexto arriba.
        self.progreso = ft.ProgressRing(width=22, height=22, stroke_width=3, visible=False)
        self.estado = ft.Text("", size=12, color=GRIS)
        barra_acciones = ft.Row(
            [
                boton_primario("Subir archivos", ft.Icons.UPLOAD_FILE,
                               self._abrir_subir,
                               tooltip="Da de alta activos desde una carpeta, un ZIP "
                                       "o un Excel"),
                boton_secundario("Buscar en SIPP", ft.Icons.SEARCH, self._buscar),
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

        # Sin buscador global: el filtrado se hace con los filtros por columna
        # (empresa, sucursal, departamento, insumo, etiqueta, serie). `_filtro`
        # se conserva vacío porque las consultas SQL lo siguen recibiendo.
        self._filtro = ""

        # Herramientas sobre la selección (a la derecha de las pestañas).
        self.barra_masiva = ft.Row(
            [
                boton_herramienta("Seleccionar todos", ft.Icons.SELECT_ALL,
                                  self._seleccionar_todos),
                boton_herramienta("Consultar movimientos", ft.Icons.HISTORY,
                                  self._abrir_movimientos,
                                  tooltip="Historial de altas y modificaciones "
                                          "enviadas al SIPP"),
                boton_herramienta("Eliminar seleccionados", ft.Icons.DELETE_OUTLINE,
                                  self._eliminar_seleccionados, destructivo=True),
            ],
            spacing=4, wrap=True, vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        # --- Filtros por columna (estilo Excel) -------------------------------
        # Categóricos (empresa/sucursal/departamento) como desplegable de valores
        # distintos; texto (insumo/etiqueta/serie) como "contiene". Se combinan
        # entre sí; el filtrado lo hace SQLite.
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
            [self.dd_f_empresa, self.dd_f_sucursal, self.dd_f_departamento,
             self.tf_f_insumo, self.tf_f_etiqueta, self.tf_f_serie,
             self._btn_limpiar_filtros],
            spacing=10, run_spacing=10, wrap=True, expand=True,
            alignment=ft.MainAxisAlignment.START,
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
                barra_acciones,
                ft.Divider(height=1, color=ft.Colors.OUTLINE_VARIANT),
                # Pestañas a la izquierda, herramientas de selección a la derecha.
                ft.Row([self._tabs.control, self.barra_masiva],
                       alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                       vertical_alignment=ft.CrossAxisAlignment.CENTER, wrap=True,
                       run_spacing=8),
                # Filtros por columna + la acción de RPA de la pestaña, en la MISMA
                # línea: filtros a la izquierda, botón de RPA a la derecha.
                ft.Row([self.barra_filtros, self._barra_rpa],
                       alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                       vertical_alignment=ft.CrossAxisAlignment.CENTER),
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
        # La selección es POR PESTAÑA: al cambiar de estatus se limpia, para que el
        # check general y las acciones masivas solo afecten a la pestaña activa.
        self._seleccionados.clear()
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
            # Comparar SIPP vs Excel: en ámbar si hay diferencias, en verde si
            # coinciden. Solo aplica a activos dados de alta (hay ambos lados).
            n_dif = len(comparacion_sipp.campos_distintos(r))
            controles_accion.append(ft.IconButton(
                icon=ft.Icons.COMPARE_ARROWS, icon_size=20,
                icon_color=NARANJA if n_dif else VERDE,
                tooltip=(f"Comparar SIPP vs Excel ({n_dif} diferencia(s))" if n_dif
                         else "Comparar SIPP vs Excel (coinciden)"),
                on_click=lambda _e, reg=r: self._comparar_sipp(reg)))
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
                error = mensaje_amigable(exc)

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

    def _comparar_sipp(self, reg: "db.Levantamiento") -> None:
        """Compara, campo por campo, los datos del SIPP contra los del Excel del
        activo dado de alta. El usuario elige en cada diferencia qué valor prevalece:

        - **SIPP** (conservar el SIPP): se copia el valor del SIPP al levantamiento
          local. No toca el SIPP.
        - **Excel** (sobrescribir el SIPP): el valor del Excel se marca para
          enviarse al SIPP con «Realizar modificación en SIPP» (RPA). Insumo y
          empleado no se empujan como texto (se eligen por modal en el SIPP)."""
        info = reg.info_sipp()
        if not info:
            self.app.avisar("Este registro no tiene datos del SIPP. Corre "
                            "«Buscar en SIPP» de nuevo.", NARANJA)
            return
        difs = comparacion_sipp.comparar(reg)
        distintos = [d for d in difs if d.difiere]
        if not distintos:
            self.app.avisar("Sin diferencias: los datos del SIPP y del Excel "
                            "coinciden.", VERDE)
            return

        # Elección por campo. Por defecto gana el lado que TIENE valor cuando el
        # otro está vacío; ante conflicto real (ambos con valor) gana el Excel (el
        # dato recién capturado), que el usuario revisa y puede cambiar.
        eleccion: dict[str, str] = {}
        for d in distintos:
            if not d.excel_crudo:
                eleccion[d.campo.clave] = "sipp"
            else:
                eleccion[d.campo.clave] = "excel"

        celdas: dict[str, tuple] = {}   # clave -> (celda_sipp, celda_excel)
        _BORDES = 8

        def _celda(texto: str, activa: bool, lado: str) -> ft.Container:
            return ft.Container(
                ft.Text(texto, size=13, selectable=False,
                        color=ft.Colors.ON_SURFACE if activa else GRIS),
                padding=ft.Padding.symmetric(horizontal=12, vertical=8),
                border_radius=_BORDES, expand=True,
                bgcolor=(ft.Colors.with_opacity(0.12, ft.Colors.PRIMARY)
                         if activa else None),
                border=ft.Border.all(
                    2 if activa else 1,
                    ft.Colors.PRIMARY if activa
                    else ft.Colors.with_opacity(0.4, ft.Colors.OUTLINE)),
                data=lado)

        def _repintar() -> None:
            for clave, (c_sipp, c_excel) in celdas.items():
                sel = eleccion[clave]
                for cel, lado in ((c_sipp, "sipp"), (c_excel, "excel")):
                    activa = sel == lado
                    cel.bgcolor = (ft.Colors.with_opacity(0.12, ft.Colors.PRIMARY)
                                   if activa else None)
                    cel.border = ft.Border.all(
                        2 if activa else 1,
                        ft.Colors.PRIMARY if activa
                        else ft.Colors.with_opacity(0.4, ft.Colors.OUTLINE))
                    cel.content.color = (ft.Colors.ON_SURFACE if activa else GRIS)
            modal.refrescar()

        def _elegir(clave: str, lado: str) -> None:
            eleccion[clave] = lado
            _repintar()

        def _fila_dif(d) -> ft.Control:
            c_sipp = _celda(d.sipp, eleccion[d.campo.clave] == "sipp", "sipp")
            c_excel = _celda(d.excel, eleccion[d.campo.clave] == "excel", "excel")
            celdas[d.campo.clave] = (c_sipp, c_excel)
            c_sipp.on_click = lambda _e, k=d.campo.clave: _elegir(k, "sipp")
            c_excel.on_click = lambda _e, k=d.campo.clave: _elegir(k, "excel")
            nota = "" if d.campo.empujable else "  (solo local)"
            return ft.Row([
                ft.Text(d.campo.etiqueta + nota, size=13,
                        weight=ft.FontWeight.W_600, width=170, color=GRIS),
                c_sipp, c_excel,
            ], vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=8)

        def _fila_igual(d) -> ft.Control:
            return ft.Row([
                ft.Text(d.campo.etiqueta, size=13, width=170, color=GRIS),
                ft.Text(d.sipp, size=13, color=GRIS, expand=True),
                ft.Icon(ft.Icons.CHECK, size=16, color=VERDE),
            ], vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=8)

        encabezado = ft.Row([
            ft.Text("Campo", size=12, weight=ft.FontWeight.W_700, width=170,
                    color=GRIS),
            ft.Text("SIPP", size=12, weight=ft.FontWeight.W_700, expand=True,
                    color=GRIS),
            ft.Text("Excel/levantamiento", size=12, weight=ft.FontWeight.W_700,
                    expand=True, color=GRIS),
        ], spacing=8)

        iguales = [d for d in difs if not d.difiere]
        cuerpo = [
            ft.Text("Elige, en cada diferencia, qué valor prevalece. «SIPP» copia "
                    "el dato al levantamiento local; «Excel» lo marca para enviarse "
                    "al SIPP con «Realizar modificación en SIPP».",
                    size=12, color=GRIS),
            ft.Row([
                boton_herramienta("Conservar todo del SIPP",
                                  on_click=lambda _e: [eleccion.update(
                                      {d.campo.clave: "sipp" for d in distintos}),
                                      _repintar()]),
                boton_herramienta("Usar todo del Excel",
                                  on_click=lambda _e: [eleccion.update(
                                      {d.campo.clave: "excel" for d in distintos}),
                                      _repintar()]),
            ], spacing=8),
            ft.Divider(),
            encabezado,
        ]
        cuerpo += [_fila_dif(d) for d in distintos]
        if iguales:
            cuerpo.append(ft.Divider())
            cuerpo.append(ft.Text(f"Campos que coinciden ({len(iguales)})", size=12,
                                  weight=ft.FontWeight.W_600, color=GRIS))
            cuerpo += [_fila_igual(d) for d in iguales]

        # Alto estimado del cuerpo (el Modal solo activa su scroll si se le pasa
        # un alto; luego lo recorta a lo que quepa en pantalla). Cada diferencia
        # ocupa ~2 líneas; los coincidentes, una.
        alto_est = (200 + len(distintos) * 72
                    + (52 + len(iguales) * 30 if iguales else 0))
        modal = Modal(self.page, "Comparar SIPP vs Excel",
                      subtitulo=reg.nombre_insumo, ancho=680, alto_cuerpo=alto_est)
        modal.cuerpo.controls = cuerpo

        def aplicar(_e=None) -> None:
            datos = dict(reg.datos())
            cambios_col: dict = {}       # empresa / sucursal / departamento
            nuevo_insumo = None
            nueva_serie = None
            n_push = 0
            no_empujables: list[str] = []
            for d in distintos:
                c = d.campo
                gana_excel = eleccion[c.clave] == "excel"
                valor = d.excel_crudo if gana_excel else d.sipp_crudo
                # Reflejar el valor donde vive: en datos_json (formulario/RPA) y/o
                # en la columna del registro (listado y campos del formulario).
                if c.clave_datos:
                    datos[c.clave_datos] = valor
                if c.columna == "nombre_insumo":
                    nuevo_insumo = valor or None
                elif c.columna == "no_serie":
                    nueva_serie = valor
                elif c.columna:            # empresa / sucursal / departamento
                    cambios_col[c.columna] = valor
                # ¿se enviará al SIPP? Solo lo empujable elegido como Excel.
                if gana_excel:
                    if c.empujable and c.ng_model:
                        n_push += 1
                    else:
                        no_empujables.append(c.etiqueta)
            # Si hay algo que empujar al SIPP, se marca modificado para que el RPA
            # de modificación lo reenvíe. Si solo se conservó el SIPP, no hace falta.
            db.actualizar_datos_levantamiento(
                reg.id, datos=datos, nombre_insumo=nuevo_insumo,
                no_serie=nueva_serie, modificado=True if n_push else None)
            if cambios_col:
                db.actualizar_ubicacion_levantamiento(reg.id, **cambios_col)
            modal.cerrar()
            self._refrescar()
            if n_push:
                msg = (f"Reconciliado. {n_push} cambio(s) se enviarán al SIPP con "
                       "«Realizar modificación en SIPP».")
            else:
                msg = "Levantamiento actualizado con los datos del SIPP."
            self.app.avisar(msg, VERDE, duracion=7000)
            if no_empujables:
                self.app.avisar(
                    "Estos campos no se envían automáticamente al SIPP (se eligen "
                    f"a mano allá): {', '.join(no_empujables)}.", NARANJA,
                    duracion=8000)

        modal.set_acciones([
            boton_secundario("Cancelar", on_click=lambda _e: modal.cerrar()),
            boton_primario("Aplicar", ft.Icons.CHECK, aplicar),
        ])
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
    # -------------------------------------------- modal unificado de carga
    def _abrir_subir(self, _e=None) -> None:
        """Modal que unifica los métodos de alta: carpeta, ZIP o Excel."""
        modal = Modal(self.page, "Subir archivos para dar de alta", ancho=560)

        def _opcion(icono, titulo, desc, on_click):
            return ft.Container(
                ft.Row([ft.Icon(icono, size=26, color=ft.Colors.PRIMARY),
                        ft.Column([ft.Text(titulo, size=14, weight=ft.FontWeight.W_600),
                                   ft.Text(desc, size=12, color=GRIS, no_wrap=False)],
                                  spacing=2, tight=True, expand=True)],
                       spacing=14, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                padding=14, border_radius=8,
                border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
                ink=True, on_click=on_click)

        async def _ir_excel(_e=None):
            modal.cerrar()
            await self.dialogo_carga.abrir()

        modal.cuerpo.spacing = 10
        modal.cuerpo.controls = [
            ft.Text("Elige cómo cargar los activos para el registro del RPA:",
                    size=13, color=GRIS),
            _opcion(ft.Icons.FOLDER_OPEN, "Carga de carpeta",
                    "Todas las imágenes de una carpeta (y sus subcarpetas).",
                    lambda _e: self._pedir_contexto(modal, "carpeta")),
            _opcion(ft.Icons.FOLDER_ZIP, "Carga de carpeta comprimida (ZIP)",
                    "Un .zip del levantamiento; se extrae y se procesa igual.",
                    lambda _e: self._pedir_contexto(modal, "zip")),
            _opcion(ft.Icons.TABLE_VIEW, "Carga masiva con Excel",
                    "Plantilla de Excel con los datos del alta (empresa/sucursal en "
                    "las columnas).", _ir_excel),
        ]
        modal.set_acciones([boton_herramienta(
            "Cancelar", on_click=lambda _e: modal.cerrar())])
        modal.abrir()

    def _pedir_contexto(self, modal, metodo: str) -> None:
        """Para carpeta/ZIP: pide empresa y sucursal antes de procesar."""
        _, dd_emp = campo_opciones("Empresa", list(NOMBRES_EMPRESAS), flotante=True)
        bloque_suc, dd_suc = campo_opciones("Sucursal", [], flotante=True)

        def _recargar_suc(_e=None):
            idemp = ID_POR_EMPRESA.get(dd_emp.value or "")
            sucs = db.listar_sucursales_sipp(idemp) if idemp is not None else []
            dd_suc.options = [ft.DropdownOption(key=s, text=s) for s in sucs]
            modal.refrescar()
        dd_emp.on_change = _recargar_suc

        modal.cuerpo.controls = [
            ft.Text("¿A qué empresa y sucursal corresponde "
                    + ("la carpeta?" if metodo == "carpeta" else "el ZIP?"),
                    size=13, weight=ft.FontWeight.W_600),
            dd_emp, dd_suc,
            ft.Text("La sucursal ofrece las cacheadas del SIPP; si no aparece, usa "
                    "«Actualizar información del SIPP» o escríbela.", size=11, color=GRIS),
        ]

        async def _continuar(_e=None):
            empresa = (dd_emp.value or "").strip()
            sucursal = (dd_suc.value or "").strip()
            if not empresa:
                self.app.avisar("Elige una empresa.", NARANJA)
                return
            modal.cerrar()
            if metodo == "carpeta":
                await self._subir_carpeta(empresa, sucursal)
            else:
                await self._subir_zip(empresa, sucursal)

        modal.set_acciones([
            boton_herramienta("Cancelar", on_click=lambda _e: modal.cerrar()),
            boton_primario("Continuar", ft.Icons.ARROW_FORWARD, _continuar),
        ])
        modal.refrescar()

    async def _subir_carpeta(self, empresa: str = "", sucursal: str = "") -> None:
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
        self._registrar_imagenes(entradas, empresa, sucursal)

    async def _subir_zip(self, empresa: str = "", sucursal: str = "") -> None:
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
        self._registrar_imagenes(archivos.listar_imagenes(carpeta), empresa, sucursal)

    def _registrar_imagenes(self, entradas: list[tuple[str, str]], empresa: str = "",
                            sucursal: str = "", departamento: str = "") -> None:
        """Da de alta un registro por imagen (parseando su nombre) etiquetándolo con
        la empresa/sucursal indicadas en el modal. Cuenta cuántas se agregaron y
        cuántas se omitieron por estar repetidas (misma serie+insumo)."""
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
        from core import activos_sipp
        from core.empresas import ID_POR_EMPRESA
        from core.proveedor_activos import ProveedorSipp, SinCacheActivos
        hechos = 0
        sin_cache: list[str] = []
        for empresa, regs in por_empresa.items():
            # Refresco previo por API (HTTP, sin navegador ni login): la búsqueda
            # compara así contra el SIPP de AHORA, y funciona aunque nunca se haya
            # corrido «Actualizar información del SIPP». Best-effort: si la API no
            # está configurada o falla, se sigue con la caché tal como estaba, que
            # es exactamente el comportamiento anterior.
            if activos_sipp.hay_api():
                try:
                    activos_sipp.descargar_activos_api(
                        ID_POR_EMPRESA[empresa], empresa)
                except Exception:  # noqa: BLE001 — se cae a la caché existente
                    pass
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
                    # Los activos dados de alta ANTES de reflejar la serie quedaron
                    # con la columna vacía; al reconocerlos en el SIPP se adopta la
                    # suya (que suele ser su propia etiqueta).
                    serie_sipp = str(datos_sipp.get("serie") or "").strip()
                    serie_nueva = (serie_sipp if serie_sipp
                                   and not (r.no_serie or "").strip() else None)
                    if id_tipo_nuevo is not None or prefill or serie_nueva:
                        db.actualizar_datos_levantamiento(
                            r.id, id_tipo_activo=id_tipo_nuevo, datos=prefill,
                            no_serie=serie_nueva)
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
        # A diferencia de la modificación, aquí «Detener» NO corta a media captura:
        # el alta genera la ETIQUETA (un consecutivo global del SIPP) antes de
        # guardar, así que abortar a la mitad la quemaría sin activo que la use. Se
        # termina el activo en curso y no se empieza otro; el usuario tiene que
        # saberlo, o creerá que la herramienta ignoró su clic.
        aviso_detencion = ft.Row(
            [ft.Icon(ft.Icons.HOURGLASS_TOP, size=16, color=NARANJA),
             ft.Text("", size=11, color=NARANJA, no_wrap=False, expand=True)],
            spacing=8, visible=False,
            vertical_alignment=ft.CrossAxisAlignment.START)
        actual = {"nombre": ""}   # último activo anunciado (para nombrarlo en el aviso)

        def pedir_detener(_e=None) -> None:
            ctrl.detener()
            en_curso = actual["nombre"] or "el activo en curso"
            aviso_detencion.controls[1].value = (
                f"Deteniendo… Se terminará el alta de «{en_curso}» y no se iniciará "
                "ninguna más. No se corta antes para no desperdiciar la etiqueta "
                "que el SIPP ya generó.")
            aviso_detencion.visible = True
            btn_detener.disabled = True
            btn_detener.content = "Deteniendo…"
            modal.refrescar()

        btn_detener = boton_herramienta("Detener", on_click=pedir_detener,
                                        destructivo=True)
        modal = Modal(self.page, "Registrando activos en el SIPP", ancho=460,
                      acciones=[btn_detener])
        modal.cuerpo.controls = [
            txt, barra, aviso_detencion,
            ft.Text("Se abrirá un navegador; no lo cierres. Al terminar se queda "
                    "abierto para que revises los registros.",
                    size=11, color=GRIS, no_wrap=False)]
        modal.abrir()

        def avance(i: int, nombre: str) -> None:
            """Actualiza el progreso desde el hilo del RPA (marshalado a la UI)."""
            def aplicar() -> None:
                actual["nombre"] = nombre
                txt.value = f"({i}/{total}) {nombre}"
                barra.value = i / total
                modal.refrescar()
            ui_loop.call_soon_threadsafe(aplicar)

        from core import reporte_altas
        resultados: list[dict] = []      # una fila por activo (para el reporte)
        errores_generales: list[str] = []

        # El navegador NO se cierra al terminar: queda abierto para que el usuario
        # revise en el propio SIPP lo que el RPA hizo, y se cierra desde el reporte
        # ("Cerrar navegador"). Por eso la sesión se crea aquí en vez de con
        # `async with`, que la cerraría al salir del flujo; su cierre se agenda
        # después en ESTE MISMO bucle, que es donde Playwright ató sus objetos.
        sipp = SesionSipp(headless=False)

        async def flujo() -> None:
            await sipp.iniciar()
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
                # Las claves con guion bajo son de uso interno (no salen al Excel):
                # identifican el registro para poder actualizarlo al confirmar.
                fila = {"insumo": r.nombre_insumo, "etiqueta": r.etiqueta or "",
                        "serie": r.no_serie or "", "estatus": reporte_altas.PENDIENTE,
                        "observacion": "", "_empresa": r.empresa or "",
                        "_sucursal": r.sucursal or "", "_id": r.id}
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
                        empresa=r.empresa or "", sucursal=r.sucursal or "",
                        empleado_nombre=(r.datos().get("nb_Empleado")
                                         or r.responsable or ""),
                        imagenes=r.datos().get("imagenes_insumo") or [])
                    db.actualizar_estatus_levantamiento(
                        r.id, db.EST_DADO_ALTA, etiqueta_gen or None)
                    if etiqueta_gen:
                        db.fijar_etiqueta_levantamiento(r.id, etiqueta_gen)
                        fila["etiqueta"] = etiqueta_gen
                    # El activo pudo quedar registrado con su ETIQUETA como No. de
                    # serie (el SIPP lo exige). Se refleja aquí o la herramienta
                    # seguiría mostrando «—» sobre un dato que el portal sí tiene.
                    serie_sipp = serie_para_alta(r.no_serie or "", etiqueta_gen or "",
                                                 r.etiqueta or "")
                    if serie_sipp and serie_sipp != (r.no_serie or "").strip():
                        # `datos` REEMPLAZA datos_json, así que se parte de lo ya
                        # capturado en vez de mandar solo la serie.
                        datos_act = dict(r.datos())
                        datos_act["nu_Serie"] = serie_sipp
                        db.actualizar_datos_levantamiento(
                            r.id, datos=datos_act, no_serie=serie_sipp)
                        fila["serie"] = serie_sipp
                    fila["estatus"] = reporte_altas.ALTA
                    fila["observacion"] = (f"Etiqueta generada: {etiqueta_gen}"
                                           if etiqueta_gen else "Alta registrada")
                # Un registro con error (insumo no hallado en el modal, campo, red…)
                # NO aborta el lote: se anota y se sigue con el siguiente.
                except Exception as exc:  # noqa: BLE001 — se reporta en el reporte
                    fila["observacion"] = mensaje_amigable(exc)
                resultados.append(fila)

        detenido = False
        try:
            await asyncio.wrap_future(bucle.enviar(flujo()))
        except RpaDetenido:
            detenido = True
        except Exception as exc:  # noqa: BLE001 — se reporta al usuario
            errores_generales.append(mensaje_amigable(exc))
        finally:
            # Confirmación final: por cada empresa se trae su listado del SIPP, se
            # verifica que las etiquetas generadas estén (que el alta realmente
            # quedó) y se guarda la foto del activo. Va FUERA del flujo para que
            # también corra al DETENER: si no, las altas ya hechas se quedaban sin
            # confirmar y sin foto solo por haber parado el proceso.
            if any(f.get("estatus") == reporte_altas.ALTA for f in resultados):
                avance(total, "Confirmando altas…")
                try:
                    await asyncio.wrap_future(
                        bucle.enviar(self._confirmar_altas(sipp, resultados)))
                except Exception as exc:  # noqa: BLE001 — las altas ya se hicieron
                    errores_generales.append(
                        f"No se pudieron confirmar las altas: {mensaje_amigable(exc)}")
            # Ojo: aquí NO se apaga el bucle. Sigue vivo porque es el único hilo
            # desde el que se puede cerrar el navegador que queda abierto; lo
            # apaga el reporte al cerrarlo.
            modal.cerrar()
            self._refrescar()

        self._guardar_historial(db.MOV_ALTA, resultados)
        self._mostrar_reporte_altas(resultados, detenido, errores_generales,
                                    sipp, bucle)

    async def _confirmar_altas(self, sipp, resultados: list) -> None:
        """Verifica que las altas hayan quedado en el SIPP y, de paso, REFRESCA la
        caché de activos de cada empresa (así una búsqueda posterior es consistente
        y no marca 'no dado de alta' un activo recién creado). Por empresa, descarga
        su listado fresco y comprueba que la etiqueta generada de cada alta esté.

        Con ese listado se guarda además la FOTO del activo en el registro
        (`datos_sipp`), que es el lado «SIPP» de «Comparar SIPP vs Excel»: sin ella,
        un activo recién dado de alta no tenía contra qué compararse y la pantalla
        exigía volver a correr «Buscar en SIPP»."""
        from collections import defaultdict

        from core import activos_sipp, reporte_altas
        from core.proveedor_activos import ProveedorSipp

        por_empresa: dict = defaultdict(list)
        for fila in resultados:
            if fila.get("estatus") == reporte_altas.ALTA and fila.get("etiqueta"):
                nombre = (fila.get("_empresa") or "").strip()
                idemp = ID_POR_EMPRESA.get(nombre)
                if idemp is not None:
                    por_empresa[(idemp, nombre)].append(fila)
        for (idemp, nombre), filas_e in por_empresa.items():
            try:
                # Descarga y CACHEA los activos frescos de la empresa.
                await activos_sipp.descargar_activos(sipp, idemp, nombre)
                # Misma búsqueda por etiqueta EXACTA que usa «Buscar en SIPP», para
                # que la confirmación y la foto salgan del mismo criterio.
                hallados = ProveedorSipp(idemp).buscar_por_etiqueta(
                    [f["etiqueta"].strip() for f in filas_e])
            except Exception:  # noqa: BLE001 — si falla la descarga, no se confirma
                hallados = None
            for fila in filas_e:
                if hallados is None:
                    fila["observacion"] += "  ·  No se pudo confirmar en el SIPP."
                    continue
                res = hallados.get(fila["etiqueta"].strip())
                if res is not None and res.dado_de_alta:
                    fila["observacion"] += "  ·  Confirmado en el SIPP."
                    if fila.get("_id") is not None and res.datos:
                        db.actualizar_estatus_levantamiento(
                            fila["_id"], db.EST_DADO_ALTA, res.id_activo_sipp,
                            res.datos)
                else:
                    fila["estatus"] = reporte_altas.PENDIENTE
                    fila["observacion"] += "  ·  No aparece en el listado del SIPP (revisar)."

    # ------------------------------ navegador que el RPA deja abierto al final
    @staticmethod
    def _navegador_vivo(sipp: "SesionSipp") -> bool:
        """¿Sigue abierto el navegador del RPA? El usuario pudo haberlo cerrado a
        mano desde Windows, así que no basta con que la sesión exista."""
        try:
            return sipp.browser is not None and sipp.browser.is_connected()
        except Exception:  # noqa: BLE001 — driver caído: cuenta como cerrado
            return False

    async def _cerrar_navegador(self, sipp: "SesionSipp", bucle: BucleRpa) -> None:
        """Cierra el navegador que el flujo dejó abierto y apaga el bucle del RPA.

        El cierre se agenda EN el bucle del RPA porque Playwright ata sus objetos
        al loop donde se crearon; hacerlo desde el hilo de la UI truena."""
        # Salida temprana ante un segundo disparo (doble clic): con el bucle ya
        # apagado, `enviar()` deja un Future que nadie resolvería nunca.
        if not self._navegador_vivo(sipp):
            bucle.cerrar()
            return
        try:
            await asyncio.wrap_future(bucle.enviar(sipp.cerrar()))
        except Exception:  # noqa: BLE001 — ya cerrado a mano / driver caído
            pass
        finally:
            bucle.cerrar()

    def _modal_reporte_rpa(self, titulo: str, stats: list, filas: list,
                           errores_generales: list, sipp: "SesionSipp",
                           bucle: BucleRpa, *, detenido: bool = False,
                           extra: list | None = None) -> Modal:
        """Arma el reporte final de un flujo del RPA: estadísticas arriba, una
        línea por registro con su observación y, en el pie, el botón que cierra el
        navegador que quedó abierto para la revisión.

        `stats`: [(número, etiqueta, color)].
        `filas`: [(ok, título, observación)] o, si el flujo los tiene,
        [(ok, título, observación, [(rótulo, antes, después)])] para desglosar
        DATO POR DATO lo que cambió en el portal (lo usan las modificaciones; el
        alta no tiene «antes» contra qué comparar).
        `extra`: acciones adicionales del pie (p. ej. exportar).
        """
        def stat(n, etiqueta, color):
            return ft.Column(
                [ft.Text(str(n), size=24, weight=ft.FontWeight.BOLD, color=color),
                 ft.Text(etiqueta, size=11, color=GRIS)],
                spacing=0, tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER)

        fila_stats = ft.Row([stat(*s) for s in stats],
                            alignment=ft.MainAxisAlignment.SPACE_EVENLY)

        lista = ft.ListView(spacing=4, expand=True)
        for datos in filas:
            ok, titulo_fila, observacion = datos[0], datos[1], datos[2]
            cambios = datos[3] if len(datos) > 3 else ()
            detalle = ft.Column(
                [ft.Text(titulo_fila, size=12, weight=ft.FontWeight.W_500,
                         color=ft.Colors.ON_SURFACE),
                 ft.Text(observacion, size=11, color=GRIS, no_wrap=False)],
                spacing=0, tight=True, expand=True)
            for rotulo, antes, despues in cambios:
                # El valor viejo va tachado y el nuevo destacado: se lee de un
                # golpe qué quedó registrado, sin comparar dos columnas.
                detalle.controls.append(ft.Row(
                    [ft.Text(f"{rotulo}:", size=11, color=ft.Colors.ON_SURFACE_VARIANT),
                     ft.Text(antes or "(vacío)", size=11, color=GRIS,
                             style=ft.TextStyle(
                                 decoration=ft.TextDecoration.LINE_THROUGH)),
                     ft.Icon(ft.Icons.ARROW_RIGHT_ALT, size=14, color=GRIS),
                     ft.Text(despues or "(vacío)", size=11, weight=ft.FontWeight.W_500,
                             color=ft.Colors.ON_SURFACE, no_wrap=False, expand=True)],
                    spacing=6, wrap=False,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER))
            lista.controls.append(ft.Row(
                [ft.Icon(ft.Icons.CHECK_CIRCLE if ok else ft.Icons.ERROR_OUTLINE,
                         size=16, color=VERDE if ok else NARANJA),
                 detalle],
                spacing=8, vertical_alignment=ft.CrossAxisAlignment.START))

        vivo = self._navegador_vivo(sipp)
        cuerpo = [fila_stats, ft.Divider()]
        if errores_generales:
            cuerpo.append(ft.Text("Observaciones generales: "
                                  + "; ".join(errores_generales), size=11, color=ROJO,
                                  no_wrap=False))
        if vivo:
            cuerpo.append(ft.Row(
                [ft.Icon(ft.Icons.INFO_OUTLINE, size=16, color=GRIS),
                 ft.Text("El navegador quedó abierto para que revises los registros "
                         "en el SIPP. Ciérralo desde aquí cuando termines.",
                         size=11, color=GRIS, no_wrap=False, expand=True)],
                spacing=8, vertical_alignment=ft.CrossAxisAlignment.START))
        cuerpo.append(ft.Container(lista, height=280))

        def _al_descartar() -> None:
            # Si el reporte se descarta con la X o Esc y el navegador sigue vivo,
            # este aviso es el último atajo para cerrarlo desde la herramienta.
            # `silenciar_aviso` lo pone «Reanudar»: ahí el modal se cierra para
            # seguir usando ESE navegador, así que el aviso sobraría.
            if getattr(modal, "silenciar_aviso", False):
                return
            if self._navegador_vivo(sipp):
                self.app.avisar(
                    "El navegador del RPA sigue abierto.", NARANJA,
                    accion="Cerrar navegador",
                    on_accion=lambda _e: self.page.run_task(
                        self._cerrar_navegador, sipp, bucle),
                    duracion=12000)

        modal = Modal(self.page, titulo,
                      subtitulo="Proceso detenido" if detenido else None, ancho=580,
                      al_cerrar=_al_descartar)
        modal.cuerpo.controls = cuerpo

        async def _cerrar_nav(_e=None) -> None:
            await self._cerrar_navegador(sipp, bucle)
            modal.cerrar()   # ya sin navegador vivo: no reaparece el aviso
            self.app.avisar("Navegador del RPA cerrado.", VERDE)

        if vivo:
            acciones = [boton_secundario("Cerrar navegador", ft.Icons.CLOSE,
                                         _cerrar_nav)]
        else:
            # Sin navegador que cerrar, el hilo del RPA ya no hace falta.
            bucle.cerrar()
            acciones = [boton_herramienta("Cerrar",
                                          on_click=lambda _e: modal.cerrar())]
        modal.set_acciones(acciones + list(extra or []))
        return modal

    def _mostrar_reporte_altas(self, filas: list, detenido: bool,
                               errores_generales: list, sipp: "SesionSipp",
                               bucle: BucleRpa) -> None:
        """Reporte final del proceso de altas: estadísticas (realizadas/pendientes),
        observaciones por activo y opción de exportar a Excel."""
        from core import reporte_altas
        res = reporte_altas.resumen_altas(filas, detenido)

        # Pendientes primero (son las que requieren atención).
        renglones = []
        for f in sorted(filas, key=lambda x: x.get("estatus") == reporte_altas.ALTA):
            ok = f.get("estatus") == reporte_altas.ALTA
            titulo = f.get("insumo", "")
            if f.get("etiqueta"):
                titulo += f"  ·  {f['etiqueta']}"
            renglones.append((ok, titulo, f.get("observacion", "")))

        async def _exportar(_e=None) -> None:
            await self._exportar_reporte_altas(filas, detenido, errores_generales)

        modal = self._modal_reporte_rpa(
            "Reporte de altas",
            [(res["realizadas"], "Realizadas", VERDE),
             (res["pendientes"], "Pendientes", NARANJA),
             (res["total"], "Total", ft.Colors.ON_SURFACE_VARIANT)],
            renglones, errores_generales, sipp, bucle, detenido=detenido,
            extra=[boton_primario("Exportar (Excel)", ft.Icons.DOWNLOAD, _exportar)])
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
    # El alta tiene DOS juegos de estos campos (compra y resguardo) y usa el de
    # compra; la edición solo expone el de RESGUARDO —verificado en el DOM real del
    # portal—, así que sin este puente el cambio no encontraba dónde aplicarse y se
    # perdía en silencio. En la edición no hay ambigüedad: es el único de cada uno.
    _RENOMBRES_EDICION = {
        "filtrosEditar.id_GrupoCentroCosto": "filtrosEditar.id_GrupoCentroCostoResguardo",
        "filtrosEditar.id_CentroCosto": "filtrosEditar.id_CentroCostoResguardo",
        "filtrosEditar.id_Departamento": "filtrosEditar.id_DepartamentoResguardo",
    }

    @staticmethod
    def _a_ng_model_edicion(ng_model: str) -> str:
        """Traduce el localizador del ALTA al del formulario de EDICIÓN del SIPP:
        filtrosAgregar.X -> filtrosEditar.X  y  FH_X -> FH_X_EDITAR, más los
        renombres de `_RENOMBRES_EDICION`."""
        if ng_model.startswith("filtrosAgregar."):
            ng_model = ng_model.replace("filtrosAgregar.", "filtrosEditar.", 1)
            return SeccionRegistroActivos._RENOMBRES_EDICION.get(ng_model, ng_model)
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

    async def _refrescar_sipp_de(self, sipp: "SesionSipp", registros: list) -> list:
        """Vuelve a bajar del SIPP los activos de las empresas tocadas y REESCRIBE
        la foto (`datos_sipp`) de esos registros. Devuelve avisos si algo falló.

        Hace falta porque esa foto se toma en «Buscar en SIPP» y NADA la refrescaba
        después: tras modificar en el portal, «Comparar SIPP vs Excel» seguía
        mostrando los valores viejos y marcaba diferencias ya resueltas.

        Best-effort: la modificación ya quedó guardada en el SIPP, así que un fallo
        aquí se reporta pero no la invalida."""
        from collections import defaultdict

        from core import activos_sipp
        from core.proveedor_activos import ProveedorSipp

        avisos: list[str] = []
        por_empresa: dict = defaultdict(list)
        for r in registros:
            nombre = (r.empresa or "").strip()
            idemp = ID_POR_EMPRESA.get(nombre)
            if idemp is not None:
                por_empresa[(idemp, nombre)].append(r)
        for (idemp, nombre), regs in por_empresa.items():
            try:
                # Descarga y CACHEA los activos frescos de la empresa (misma vía que
                # usa la confirmación de altas).
                await activos_sipp.descargar_activos(sipp, idemp, nombre)
                etiquetas = sorted({(r.etiqueta or "").strip() for r in regs
                                    if (r.etiqueta or "").strip()})
                resultados = ProveedorSipp(idemp).buscar_por_etiqueta(etiquetas)
            except Exception as exc:  # noqa: BLE001 — no crítico: se avisa y se sigue
                avisos.append(f"No se pudieron refrescar los datos del SIPP de "
                              f"{nombre}: {mensaje_amigable(exc)}")
                continue
            for r in regs:
                res = resultados.get((r.etiqueta or "").strip())
                if res and res.dado_de_alta and res.datos:
                    db.actualizar_estatus_levantamiento(
                        r.id, db.EST_DADO_ALTA, res.id_activo_sipp, res.datos)
        return avisos

    def _rotulos_edicion(self, r: "db.Levantamiento") -> dict:
        """{ng_model de EDICIÓN -> rótulo visible}. El RPA razona en ng-models del
        portal; el reporte tiene que decir «Fecha de asignación», no
        «dt_FH_ASIGNACION_EDITAR»."""
        return {self._a_ng_model_edicion(c.ng_model): c.etiqueta
                for c in campos_de_tipo(r.id_tipo_activo)}

    # ------------------------------------------------ historial de movimientos
    def _abrir_movimientos(self, _e=None) -> None:
        """Historial de lo enviado al SIPP, separado en Altas y Modificaciones.

        Responde la pregunta de operación «¿qué se hizo y cuándo?», que antes solo
        vivía en el reporte de la corrida y se perdía al cerrarlo."""
        resumen = db.resumen_movimientos()
        if not resumen:
            self.app.avisar(
                "Todavía no hay movimientos registrados. Se van guardando conforme "
                "des de alta o modifiques activos en el SIPP.", NARANJA, duracion=8000)
            return

        lista = ft.ListView(spacing=6, expand=True)
        estado = ft.Text("", size=12, color=GRIS)
        tf = buscador("Buscar por etiqueta, insumo o serie…", expand=True)

        def pintar(_e=None) -> None:
            tipo = (db.MOV_ALTA if tabs.activa == "altas"
                    else db.MOV_MODIFICACION)
            movs = db.listar_movimientos(tipo, tf.value or "")
            lista.controls = [_fila_movimiento(m) for m in movs] or [
                ft.Container(ft.Text("Sin movimientos con ese criterio.", size=12,
                                     color=GRIS), padding=12)]
            hechos = sum(1 for m in movs if m["exito"])
            estado.value = (f"{len(movs)} movimiento(s) · {hechos} correcto(s) · "
                            f"{len(movs) - hechos} con problema")
            modal.refrescar()

        def _fila_movimiento(m: dict) -> ft.Control:
            ok = m["exito"]
            titulo = m.get("insumo") or "(sin insumo)"
            if m.get("etiqueta"):
                titulo += f"  ·  {m['etiqueta']}"
            ubic = " · ".join(p for p in (m.get("empresa"), m.get("sucursal")) if p)
            detalle = ft.Column(
                [ft.Row([ft.Text(titulo, size=12, weight=ft.FontWeight.W_500,
                                 color=ft.Colors.ON_SURFACE, expand=True),
                         # La fecha a la derecha: es lo que se escanea al buscar
                         # "lo de ayer" sin leer cada renglón.
                         ft.Text(_fecha_corta(m.get("fecha")), size=11, color=GRIS)],
                        spacing=8),
                 ft.Text(" · ".join(p for p in (ubic, m.get("observacion") or "") if p),
                         size=11, color=GRIS, no_wrap=False)],
                spacing=2, tight=True, expand=True)
            # Las modificaciones muestran su antes -> después, igual que el reporte.
            for cambio in (m.get("cambios") or []):
                try:
                    rotulo, antes, despues = cambio
                except (ValueError, TypeError):
                    continue
                detalle.controls.append(ft.Row(
                    [ft.Text(f"{rotulo}:", size=11,
                             color=ft.Colors.ON_SURFACE_VARIANT),
                     ft.Text(antes or "(vacío)", size=11, color=GRIS,
                             style=ft.TextStyle(
                                 decoration=ft.TextDecoration.LINE_THROUGH)),
                     ft.Icon(ft.Icons.ARROW_RIGHT_ALT, size=14, color=GRIS),
                     ft.Text(despues or "(vacío)", size=11,
                             weight=ft.FontWeight.W_500, color=ft.Colors.ON_SURFACE,
                             no_wrap=False, expand=True)],
                    spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER))
            return ft.Container(
                ft.Row([ft.Icon(ft.Icons.CHECK_CIRCLE if ok else ft.Icons.ERROR_OUTLINE,
                                size=16, color=VERDE if ok else NARANJA),
                        detalle],
                       spacing=8, vertical_alignment=ft.CrossAxisAlignment.START),
                padding=ft.Padding.symmetric(horizontal=10, vertical=8),
                bgcolor=ft.Colors.SURFACE_CONTAINER_LOWEST,
                border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
                border_radius=8)

        tabs = Pestanas(
            [("altas", "Altas", ft.Icons.ADD_CIRCLE_OUTLINE),
             ("modificaciones", "Modificaciones", ft.Icons.EDIT_OUTLINED)],
            al_cambiar=lambda _clave: pintar())
        tabs.set_conteo("altas", resumen.get(db.MOV_ALTA, {}).get("total", 0))
        tabs.set_conteo("modificaciones",
                        resumen.get(db.MOV_MODIFICACION, {}).get("total", 0))
        tf.on_change = lambda _e: pintar()

        modal = Modal(self.page, "Movimientos realizados", ancho=760, alto_cuerpo=460)
        modal.cuerpo.controls = [tabs.control, tf, estado,
                                 ft.Container(lista, height=360)]
        modal.set_acciones([boton_herramienta("Cerrar",
                                              on_click=lambda _e: modal.cerrar())])
        pintar()
        modal.abrir()

    def _guardar_historial(self, tipo: str, filas: list) -> None:
        """Persiste en el historial lo que la corrida envió al SIPP.

        Se llama con las MISMAS filas que alimentan el reporte en pantalla: ese
        reporte se pierde al cerrarlo, y sin esto no quedaría rastro de qué se
        envió ni cuándo. Best-effort: un fallo aquí no puede tumbar un trabajo que
        ya se hizo en el portal."""
        from core import reporte_altas

        if not filas:
            return
        lote = datetime.now().strftime("%Y%m%d%H%M%S")
        registros = []
        for f in filas:
            # El alta marca el resultado con su estatus; la modificación con `ok`.
            exito = (f.get("estatus") == reporte_altas.ALTA if tipo == db.MOV_ALTA
                     else bool(f.get("ok")))
            registros.append({
                "id_levantamiento": f.get("_id"), "etiqueta": f.get("etiqueta"),
                "insumo": f.get("insumo"), "serie": f.get("serie"),
                "empresa": f.get("_empresa"), "sucursal": f.get("_sucursal"),
                "exito": exito, "observacion": f.get("observacion"),
                "cambios": f.get("cambios") or None,
            })
        try:
            db.registrar_movimientos(lote, tipo, registros)
        except Exception as exc:  # noqa: BLE001 — el envío ya ocurrió
            self.app.avisar(f"No se pudo guardar el historial: {exc}", NARANJA)

    async def _confirmar_etiquetas_repetidas(self, pendientes: list) -> "list | None":
        """Si alguna etiqueta por modificar está REPETIDA en el SIPP, lo consulta.

        El SIPP admite dos activos con el mismo número de inventario, y el RPA
        localiza el activo por etiqueta y edita la primera fila del listado: con una
        repetida podría estar modificando el equipo equivocado sin que nadie se
        entere. Antes de tocar nada se le enseñan al usuario los insumos que
        comparten la etiqueta para que decida.

        Devuelve la lista de registros a procesar (quizá recortada) o None si
        cancela. Sin API configurada no hay forma de detectarlas: se sigue igual
        que siempre.
        """
        from core import activos_sipp

        if not activos_sipp.hay_api():
            return pendientes

        # Una descarga por empresa (también deja la caché al día). Best-effort: si
        # la API falla, no se bloquea el trabajo por no poder hacer la advertencia.
        etiquetas_reg: dict = {}
        for r in pendientes:
            etq = (r.etiqueta or "").strip()
            if etq:
                etiquetas_reg.setdefault(etq, []).append(r)
        candidatos: dict = {}
        for empresa in sorted({(r.empresa or "").strip() for r in pendientes}):
            idemp = ID_POR_EMPRESA.get(empresa)
            if idemp is None:
                continue
            try:
                res = await asyncio.to_thread(
                    activos_sipp.descargar_activos_api, idemp, empresa)
            except Exception:  # noqa: BLE001 — sin aviso, pero el flujo sigue
                continue
            for etq, filas in (res.get("candidatos") or {}).items():
                if etq in etiquetas_reg:
                    candidatos[etq] = filas
        if not candidatos:
            return pendientes

        decision: asyncio.Future = asyncio.get_running_loop().create_future()

        def responder(valor: str) -> None:
            if not decision.done():
                decision.set_result(valor)
            modal.cerrar()

        cuerpo = [ft.Text(
            f"{len(candidatos)} etiqueta(s) por modificar están repetidas en el "
            "SIPP: varios activos comparten el mismo número de inventario. El RPA "
            "busca por etiqueta y edita la PRIMERA coincidencia, así que podría "
            "modificar el activo equivocado.", size=12, color=ft.Colors.ON_SURFACE,
            no_wrap=False)]
        for etq in sorted(candidatos):
            mios = etiquetas_reg.get(etq, [])
            nombre_mio = (mios[0].nombre_insumo or "").strip() if mios else ""
            cuerpo.append(ft.Text(f"Etiqueta {etq}", size=13,
                                  weight=ft.FontWeight.W_600, color=NARANJA))
            cuerpo.append(ft.Text(f"Tu registro: «{nombre_mio}»", size=11, color=GRIS))
            for c in candidatos[etq]:
                insumo = (c.get("insumo") or "—").strip()
                # Se marca el que coincide con el insumo capturado: suele ser el
                # que el usuario quiere, aunque el portal no garantice el orden.
                igual = insumo.casefold() == nombre_mio.casefold()
                detalle = " · ".join(p for p in (
                    f"serie {c.get('serie')}" if c.get("serie") else "",
                    c.get("empleado") or "sin empleado",
                    c.get("sucursal") or "") if p)
                cuerpo.append(ft.Row(
                    [ft.Icon(ft.Icons.CHECK_CIRCLE if igual else ft.Icons.HELP_OUTLINE,
                             size=15, color=VERDE if igual else GRIS),
                     ft.Column([ft.Text(insumo, size=12,
                                        color=ft.Colors.ON_SURFACE),
                                ft.Text(detalle, size=11, color=GRIS, no_wrap=False)],
                               spacing=0, tight=True, expand=True)],
                    spacing=8, vertical_alignment=ft.CrossAxisAlignment.START))

        modal = Modal(self.page, "Etiquetas repetidas en el SIPP", ancho=620,
                      alto_cuerpo=380,
                      al_cerrar=lambda: responder("cancelar"))
        modal.cuerpo.controls = cuerpo
        modal.set_acciones([
            boton_herramienta("Cancelar", on_click=lambda _e: responder("cancelar")),
            boton_secundario("Enviar de todos modos", ft.Icons.WARNING_AMBER,
                             lambda _e: responder("todos")),
            boton_primario("Omitir los repetidos", ft.Icons.CHECK,
                           lambda _e: responder("omitir")),
        ])
        modal.abrir()

        eleccion = await decision
        if eleccion == "cancelar":
            return None
        if eleccion == "omitir":
            quedan = [r for r in pendientes
                      if (r.etiqueta or "").strip() not in candidatos]
            self.app.avisar(
                f"Se omitieron {len(pendientes) - len(quedan)} activo(s) con "
                "etiqueta repetida; quedan pendientes.", NARANJA)
            return quedan
        return pendientes

    @staticmethod
    def _pendientes_modificacion() -> list:
        """Activos dados de alta con cambios locales por enviar. Se relee de la base
        en cada corrida: es lo que hace que «Reanudar» retome exactamente lo que
        faltó, sin llevar cuentas aparte."""
        return [r for r in db.listar_levantamiento_por_estatus(db.EST_DADO_ALTA)
                if r.modificado and r.id_tipo_activo is not None]

    async def _modificar_en_sipp(self, _e=None, *, sesion=None,
                                 bucle_previo=None) -> None:
        """Reenvía al SIPP (vía RPA) los activos dados de alta que fueron EDITADOS
        en la herramienta (marca `modificado`).

        `sesion`/`bucle_previo` los pasa «Reanudar proceso» desde el reporte: si el
        navegador de la corrida anterior sigue vivo se reaprovecha —ya está logueado
        y con la empresa elegida— en vez de abrir otro y volver a entrar."""
        creds = credenciales.cargar()
        if not creds or not creds[0]:
            self.app.avisar("Configura primero las credenciales del SIPP (botón ⚙).", ROJO)
            return
        usuario, contrasena = creds
        pendientes = self._pendientes_modificacion()
        if not pendientes:
            self.app.avisar(
                "No hay cambios por enviar. Edita un activo dado de alta (con el "
                "botón de captura 📋 o sus celdas) y vuelve a intentar.", NARANJA)
            return

        # Antes de abrir el navegador: avisar de las etiquetas que el SIPP tiene
        # repetidas, donde el RPA no puede distinguir cuál activo es el tuyo.
        pendientes = await self._confirmar_etiquetas_repetidas(pendientes)
        if pendientes is None:
            return
        if not pendientes:
            self.app.avisar("No quedó ningún activo por enviar.", NARANJA)
            return

        total = len(pendientes)
        # Reanudar: se reaprovecha el navegador anterior si sigue vivo; si el
        # usuario ya lo cerró, se arranca uno nuevo con su propio bucle.
        reanudar = (sesion is not None and bucle_previo is not None
                    and self._navegador_vivo(sesion))
        if not reanudar and bucle_previo is not None:
            # El navegador anterior ya no está (lo cerró el usuario): su hilo del
            # RPA no sirve para nada y quedaría colgado.
            bucle_previo.cerrar()
        sipp = sesion if reanudar else SesionSipp(headless=False)
        bucle = bucle_previo if reanudar else BucleRpa()
        ctrl = ControlRpa(bucle.loop)
        ui_loop = asyncio.get_running_loop()
        txt = ft.Text(f"Preparando… (0/{total})", size=13)
        barra = ft.ProgressBar(value=0)
        modal = Modal(self.page,
                      "Reanudando modificaciones en el SIPP" if reanudar
                      else "Aplicando modificaciones en el SIPP", ancho=460,
                      acciones=[boton_herramienta("Detener",
                                                  on_click=lambda _e: ctrl.detener(),
                                                  destructivo=True)])
        modal.cuerpo.controls = [
            txt, barra,
            ft.Text(("Se usará el navegador que quedó abierto." if reanudar else
                     "Se abrirá un navegador; no lo cierres.")
                    + " Al terminar se queda abierto para que revises los registros. "
                      "«Detener» corta en el acto: el activo en curso NO se guarda.",
                    size=11, color=GRIS, no_wrap=False)]
        modal.abrir()

        def avance(i: int, nombre: str) -> None:
            def aplicar() -> None:
                txt.value = f"({i}/{total}) {nombre}"
                barra.value = i / total
                modal.refrescar()
            ui_loop.call_soon_threadsafe(aplicar)

        resultados: list[dict] = []      # una fila por activo (para el reporte)
        aplicados: list = []             # registros que sí se enviaron al SIPP
        errores_generales: list[str] = []

        async def flujo() -> None:
            # Al reanudar, la sesión ya está iniciada y con empresa elegida: repetir
            # el login solo costaría tiempo.
            if not reanudar:
                await sipp.iniciar()
                await sipp.login(usuario, contrasena)
                primero = pendientes[0]
                if primero.empresa and primero.sucursal:
                    try:
                        await sipp.seleccionar_empresa_sucursal(
                            primero.empresa, primero.sucursal)
                    except ErrorSipp as exc:
                        errores_generales.append(
                            f"Selección de empresa/sucursal: {exc}")
            for i, r in enumerate(pendientes, 1):
                await ctrl.punto_control()
                avance(i, r.nombre_insumo)
                fila = {"insumo": r.nombre_insumo, "etiqueta": r.etiqueta or "",
                        "serie": r.no_serie or "", "ok": False, "observacion": "",
                        "cambios": [], "_empresa": r.empresa or "",
                        "_sucursal": r.sucursal or "", "_id": r.id}
                _tipo, campos, detalles = self._payload_modificacion(r)
                try:
                    resultado = await sipp.modificar_activo(
                        r.etiqueta, r.no_serie, campos, detalles,
                        punto_control=ctrl.punto_control)
                    no_aplicados = resultado["no_aplicados"]
                    db.actualizar_datos_levantamiento(r.id, modificado=False)
                    fila["ok"] = True
                    # Antes/después con el rótulo que el usuario conoce; los
                    # ng-models del portal no le dicen nada.
                    rotulos = self._rotulos_edicion(r)
                    fila["cambios"] = [(rotulos.get(ng, ng), antes, despues)
                                       for ng, antes, despues in resultado["cambios"]]
                    # Sin diferencias, decirlo explícitamente: "aplicada" a secas
                    # haría creer que se cambió algo que ya estaba igual.
                    fila["observacion"] = (
                        f"Modificación aplicada ({len(fila['cambios'])} dato(s))."
                        if fila["cambios"] else
                        "Sin cambios: los datos del SIPP ya coincidían.")
                    # El formulario de edición no expone los mismos campos que el
                    # alta, y algunos los bloquea el portal. Se nombra CADA campo y
                    # su motivo: un conteo suelto («1 campo sin aplicar») deja al
                    # usuario sin saber si perdió un dato o si el SIPP no lo admite.
                    if no_aplicados:
                        detalle = "; ".join(f"{rotulos.get(ng, ng)} → {motivo}"
                                            for ng, motivo in no_aplicados)
                        fila["observacion"] += f" Sin aplicar: {detalle}"
                    aplicados.append(r)
                # Detenido a media captura: NADA se guardó de este activo (no se
                # llegó a Guardar), así que queda pendiente tal cual para reanudar.
                # Va ANTES del except genérico: RpaDetenido también es Exception.
                except RpaDetenido:
                    fila["observacion"] = ("Detenido aquí: no se guardó nada de este "
                                           "activo, queda pendiente para reanudar.")
                    resultados.append(fila)
                    raise
                # Un registro con error NO aborta el lote: se anota y se sigue.
                except Exception as exc:  # noqa: BLE001 — se reporta en el reporte
                    fila["observacion"] = mensaje_amigable(exc)
                resultados.append(fila)

        detenido = False
        try:
            await asyncio.wrap_future(bucle.enviar(flujo()))
        except RpaDetenido:
            detenido = True
        except Exception as exc:  # noqa: BLE001 — se reporta al usuario
            errores_generales.append(mensaje_amigable(exc))
        finally:
            # Con lo enviado ya en el portal, se vuelve a bajar la foto del SIPP:
            # sin esto «Comparar SIPP vs Excel» seguiría mostrando los valores
            # previos. Va FUERA del flujo, y no dentro, para que también corra
            # cuando el usuario detiene a media lista: lo ya enviado se quedaría
            # con la foto vieja. Como toca la sesión de Playwright, se manda al
            # bucle del RPA igual que el resto.
            if aplicados:
                avance(total, "Actualizando datos del SIPP…")
                try:
                    errores_generales.extend(await asyncio.wrap_future(
                        bucle.enviar(self._refrescar_sipp_de(sipp, aplicados))))
                except Exception as exc:  # noqa: BLE001 — no crítico: ya se guardó
                    errores_generales.append(mensaje_amigable(exc))
            # El bucle sigue vivo a propósito: es el único hilo desde el que se
            # puede cerrar el navegador que queda abierto (lo apaga el reporte).
            modal.cerrar()
            self._refrescar()

        self._guardar_historial(db.MOV_MODIFICACION, resultados)
        self._mostrar_reporte_modificaciones(resultados, detenido, errores_generales,
                                             sipp, bucle)

    def _mostrar_reporte_modificaciones(self, filas: list, detenido: bool,
                                        errores_generales: list, sipp: "SesionSipp",
                                        bucle: BucleRpa) -> None:
        """Reporte final del envío de modificaciones al SIPP."""
        hechas = sum(1 for f in filas if f.get("ok"))
        datos = sum(len(f.get("cambios") or ()) for f in filas)
        # Con error primero: son las que requieren atención.
        renglones = []
        for f in sorted(filas, key=lambda x: bool(x.get("ok"))):
            titulo = f.get("insumo", "")
            if f.get("etiqueta"):
                titulo += f"  ·  {f['etiqueta']}"
            renglones.append((bool(f.get("ok")), titulo, f.get("observacion", ""),
                              f.get("cambios") or ()))

        # Lo que quedó sin enviar se relee de la base (no se lleva una cuenta
        # aparte): así «Reanudar» retoma exactamente lo pendiente, ya sea porque se
        # detuvo el proceso o porque algún activo falló.
        faltan = self._pendientes_modificacion()

        async def _reanudar(_e=None) -> None:
            modal.silenciar_aviso = True   # el navegador NO se cierra: se reusa
            modal.cerrar()
            await self._modificar_en_sipp(sesion=sipp, bucle_previo=bucle)

        extra = []
        if faltan:
            extra.append(boton_primario(f"Reanudar proceso ({len(faltan)})",
                                        ft.Icons.PLAY_ARROW, _reanudar))

        modal = self._modal_reporte_rpa(
            "Reporte de modificaciones",
            [(hechas, "Modificados", VERDE),
             (datos, "Datos cambiados", ft.Colors.PRIMARY),
             (len(faltan), "Pendientes", NARANJA),
             (len(filas), "Procesados", ft.Colors.ON_SURFACE_VARIANT)],
            renglones, errores_generales, sipp, bucle, detenido=detenido,
            extra=extra)
        modal.abrir()

    # ------------------------------------ actualizar catálogo de insumos
    # El botón vive en el encabezado (app.py); el shell llama a este gancho.
    def tras_actualizar_sipp(self) -> None:
        self._refrescar()

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
