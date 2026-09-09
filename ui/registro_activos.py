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
from core.tipos_activo import (ID_POR_NOMBRE, TIPOS_ACTIVO, campos_de_tipo,
                               faltantes_obligatorios, nombre_tipo)
from ui.captura_activo import DialogoCapturaActivo
from ui.carga_masiva import DialogoCargaMasiva
from ui.comun import (AZUL, GRIS, NARANJA, NOMBRES_EMPRESAS, ROJO, VERDE,
                      error_al_guardar, parse_fecha)
from ui.componentes import (GUTTER_SCROLL, CampoFecha, Modal, Pestanas,
                            boton_herramienta, boton_primario,
                            boton_secundario, buscador,
                            campo_opciones, campo_tabla_opciones,
                            campo_tabla_texto, campo_texto, fila_resultado,
                            lista_resultados, tarjeta_seccion)
from ui.tabla_responsiva import (IZQ, ColumnaTabla, FilaDatos,
                                 TablaResponsiva)

# Lado (px) de cada botón de acción de la tabla. Es el área táctil que Material
# reserva para un IconButton: fijarle un `width` menor NO lo encoge —el widget
# conserva su tamaño y se desborda—, así que la columna se dimensiona con este
# valor real. Fija el ancho mínimo de «Acciones», que debe caber las 6 posibles.
_LADO_ACCION = 40

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


def _fecha_corta(sello: str) -> str:
    """'2026-08-14 15:24:03' -> '14/08/2026 15:24'. Devuelve el crudo si no cuadra."""
    texto = str(sello or "")
    try:
        return datetime.strptime(texto[:16], "%Y-%m-%d %H:%M").strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return texto


# Tope para incrustar una foto en el visor. Por encima se pasa la RUTA, que el
# cliente de escritorio sí sabe leer: incrustar 20 MB en base64 infla el mensaje
# que viaja al cliente y congela la ventana mientras lo procesa.
_MAX_INCRUSTAR_MB = 12


def _src_imagen(ruta: str) -> str:
    """`src` para `ft.Image`: la foto incrustada como data URI, o la ruta.

    Se incrusta porque la ruta suelta solo la resuelve el cliente de escritorio;
    el data URI se ve igual en cualquiera. Si el archivo es grande o no se puede
    leer, se cae a la ruta y `error_content` cubre el caso de que tampoco cargue.
    """
    import base64
    import mimetypes

    try:
        if os.path.getsize(ruta) > _MAX_INCRUSTAR_MB * 1024 * 1024:
            return ruta
        with open(ruta, "rb") as fh:
            datos = base64.b64encode(fh.read()).decode("ascii")
    except OSError:
        return ruta
    tipo = mimetypes.guess_type(ruta)[0] or "image/jpeg"
    return f"data:{tipo};base64,{datos}"


def _foto_del_registro(r: "db.Levantamiento") -> str:
    """Foto que representa al activo, venga de donde venga. "" si no hay ninguna.

    Un registro puede tener imagen por dos caminos: la del LEVANTAMIENTO
    (`ruta_imagen`, la que creó el registro o se le relacionó después) o las
    adjuntadas a mano en la ficha (`imagenes_insumo`). La carga por Excel no crea
    la primera, así que quien adjuntaba una foto en la ficha veía «No se encontró
    la imagen original» al pulsar el ícono de la tabla, que solo miraba
    `ruta_imagen`.

    Se prefiere la de la ficha por el mismo criterio que usa el alta (ver
    `_imagenes_para_alta`): es la elección explícita del usuario. Se descartan las
    rutas que ya no existen, para no prometer una imagen que se borró.
    """
    candidatas = list((r.datos().get("imagenes_insumo") or []))
    candidatas.append(r.ruta_imagen or "")
    for ruta in candidatas:
        ruta = (ruta or "").strip()
        if ruta and os.path.exists(ruta):
            return ruta
    return ""


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
                boton_herramienta("Consultar bitácora", ft.Icons.HISTORY,
                                  self._abrir_movimientos,
                                  tooltip="Altas y modificaciones enviadas al "
                                          "SIPP, y uso de la herramienta"),
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
            # Los porcentajes suman 100: pasarse mete una barra horizontal (ver
            # ui/tabla_responsiva.py). Al entrar «Responsable» se recortaron los
            # tres desplegables de ubicación, que ya tienen su piso en píxeles.
            ColumnaTabla("Empresa", 10, ancho_min_px=155),
            ColumnaTabla("Sucursal", 10, ancho_min_px=155),
            ColumnaTabla("Departamento", 11, ancho_min_px=155),
            ColumnaTabla("Nombre insumo", 14, ancho_min_px=140),
            ColumnaTabla("Responsable", 12, ancho_min_px=150),
            ColumnaTabla("Etiqueta", 9, ancho_min_px=100),
            ColumnaTabla("No. de serie", 9, ancho_min_px=100),
            ColumnaTabla("Estatus", 8, ancho_min_px=95),
            # 6 acciones x _LADO_ACCION + holgura: es el MÁXIMO que puede tener
            # una fila (dada de alta y con serie válida), no el caso promedio.
            # `alineacion=IZQ`: las acciones arrancan pegadas al borde izquierdo y
            # crecen hacia la derecha, así todas las filas comparten el punto de
            # partida aunque tengan distinto número de botones. La alineación de la
            # Row interna NO sirve para esto: al ser `tight` se encoge a su
            # contenido y manda el contenedor de la celda.
            ColumnaTabla("Acciones", 13, ancho_min_px=6 * _LADO_ACCION + 8,
                         alineacion=IZQ),
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
        foto = _foto_del_registro(r)
        tiene_imagen = bool(foto)
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
                icon=ft.Icons.IMAGE, icon_size=20,
                # En color solo si HAY foto: de un vistazo se ve qué activos
                # quedaron sin imagen, que es lo que hay que salir a levantar.
                icon_color=AZUL if tiene_imagen else None,
                tooltip=("Ver imagen del activo" if tiene_imagen
                         else "Sin imagen relacionada"),
                on_click=lambda _e, ruta=foto: self._ver_imagen(ruta)),
            ft.IconButton(
                icon=ft.Icons.DELETE_OUTLINE, tooltip="Eliminar", icon_size=20,
                icon_color=ft.Colors.ERROR,
                on_click=lambda _e, i=r.id: self._eliminar_uno(i)),
        ]
        # Tamaño uniforme y explícito: con las 6 acciones posibles (info,
        # comparar, factura, captura, imagen, eliminar) la fila mide
        # 6 x _LADO_ACCION, que es justo lo que reserva la columna. Antes la
        # columna era más angosta y recortaba en silencio los últimos botones.
        for boton in controles_accion:
            boton.width = boton.height = _LADO_ACCION
        # Alineadas a la IZQUIERDA, no centradas: cada fila tiene un número
        # distinto de acciones (la de factura y las del SIPP son condicionales) y
        # al centrarlas cada renglón arrancaba en otra posición, con los íconos
        # bailando en zigzag al recorrer la tabla.
        acciones = ft.Row(controles_accion, spacing=0,
                          alignment=ft.MainAxisAlignment.START, tight=True,
                          wrap=False)
        return FilaDatos([
            chk,
            emp,
            suc,
            dep,
            r.nombre_insumo,
            r.responsable or "—",
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
        """Muestra la foto del activo en un modal DENTRO de la herramienta.

        Antes se abría con `abrir_en_sistema`, es decir con el visor de Windows,
        que al abrir un archivo carga TODA su carpeta y deja pasar de una foto a
        otra con las flechas. Con las imágenes de un ZIP —decenas en la misma
        carpeta de extracción— eso hacía imposible saber cuál era la del registro
        que se estaba consultando. Aquí solo existe la que se pidió.

        Queda el botón «Abrir en el sistema» para lo que el visor propio no hace:
        zoom fino, girar, imprimir.
        """
        ruta = (ruta or "").strip()
        if not ruta or not os.path.exists(ruta):
            self.app.avisar("No se encontró la imagen original.", ROJO)
            return
        modal = Modal(self.page, "Imagen del activo", ancho=760,
                      subtitulo=os.path.basename(ruta))
        modal.cuerpo.controls = [
            # `CONTAIN` para que no recorte: una foto de campo puede ser vertical
            # u horizontal y aquí lo que importa es ver el equipo completo.
            ft.Container(
                ft.Image(src=_src_imagen(ruta), fit=ft.BoxFit.CONTAIN,
                         error_content=ft.Text(
                             "No se pudo mostrar la imagen aquí. Usa «Abrir en el "
                             "sistema».", size=12, color=GRIS, no_wrap=False)),
                height=440, alignment=ft.Alignment(0, 0),
                bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST, border_radius=8),
            ft.Text(ruta, size=11, color=GRIS, selectable=True, no_wrap=False)]
        modal.set_acciones([
            boton_herramienta("Abrir en el sistema", ft.Icons.OPEN_IN_NEW,
                              lambda _e, x=ruta: self.app.abrir_en_sistema(x)),
            boton_primario("Cerrar", ft.Icons.CHECK,
                           lambda _e: modal.cerrar())])
        modal.abrir()

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
            insumo_con_id = False
            insumo_sin_catalogo = False
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
                    # El insumo no viaja como texto: el RPA lo elige por ID en el
                    # modal de la edición. Aquí se deja fijado ese ID —el del
                    # catálogo si ganó el Excel, el del portal si ganó el SIPP—
                    # para que después no se empuje un insumo que nadie eligió.
                    insumo_con_id = self._fijar_insumo_origen(
                        datos, valor, reg, gana_excel)
                elif c.columna == "no_serie":
                    nueva_serie = valor
                elif c.columna:            # empresa / sucursal / departamento
                    cambios_col[c.columna] = valor
                # ¿se enviará al SIPP? Solo lo empujable elegido como Excel.
                if gana_excel:
                    puede = bool(c.empujable and (c.ng_model or c.modal))
                    # Insumo elegido del Excel pero sin equivalente EXACTO en el
                    # catálogo: no se empuja. Mandar el parecido cambiaría el
                    # activo por otro, que es peor que dejarlo como está.
                    if puede and c.modal == "insumo" and not insumo_con_id:
                        puede = False
                        insumo_sin_catalogo = True
                    if puede:
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
                # El empleado merece su propia explicación: no es que haya que
                # elegirlo a mano en la edición, es que ahí NO se puede cambiar (el
                # portal lo mueve a su flujo de Reasignación). Decir «se elige a
                # mano allá» mandaba al usuario a buscar un botón que no existe.
                # El empleado merece su propia explicación: no es que haya que
                # elegirlo a mano en la edición, es que ahí NO se puede cambiar (el
                # portal lo manda a su flujo de Reasignación). Decir «se elige a
                # mano allá» mandaba al usuario a buscar un botón que no existe.
                partes = []
                if any("mpleado" in c for c in no_empujables):
                    partes.append("El empleado de resguardo se cambia con "
                                  "«Reasignación», no en la edición del activo.")
                if insumo_sin_catalogo:
                    partes.append("El insumo no está en el catálogo del SIPP con "
                                  "ese nombre exacto: elígelo en la ficha del "
                                  "activo (botón 📋) para poder enviarlo.")
                detalle = " ".join(partes) or "Se eligen a mano en el portal."
                self.app.avisar(
                    f"Estos campos no se envían automáticamente al SIPP: "
                    f"{', '.join(no_empujables)}. {detalle}", NARANJA,
                    duracion=9000)

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
            _opcion(ft.Icons.ADD_PHOTO_ALTERNATE, "Relacionar imágenes con activos",
                    "Para activos ya cargados (p. ej. por Excel): empareja las fotos "
                    "de una CARPETA por etiqueta, serie o nombre del archivo.",
                    lambda _e: self.page.run_task(self._relacionar_imagenes, modal)),
            # El ZIP va como opción propia y no como un diálogo que pregunte
            # «¿carpeta o ZIP?»: el resto del modal ya distingue así las dos
            # cargas, y un paso extra solo para elegir el formato sobra.
            _opcion(ft.Icons.FOLDER_ZIP, "Relacionar imágenes desde un ZIP",
                    "Igual que la anterior, pero las fotos vienen comprimidas en "
                    "un .zip; se extrae y se empareja igual.",
                    lambda _e: self.page.run_task(
                        self._relacionar_imagenes, modal, True)),
        ]
        modal.set_acciones([boton_herramienta(
            "Cancelar", on_click=lambda _e: modal.cerrar())])
        modal.abrir()

    def _pedir_contexto(self, modal, metodo: str) -> None:
        """Para carpeta/ZIP: pide empresa, sucursal y departamento antes de procesar.

        Los tres se aplican a TODAS las imágenes de esa carga: el nombre del archivo
        solo trae insumo y etiqueta, así que la ubicación se fija aquí una vez en
        lugar de fila por fila."""
        _, dd_suc = campo_opciones("Sucursal", [], flotante=True)
        _, dd_dep = campo_opciones("Departamento", [], flotante=True)
        aviso = ft.Text("", size=11, color=NARANJA, no_wrap=False, visible=False)

        def _recargar_dependientes(_e=None):
            """Sucursal y departamento salen del catálogo de la empresa elegida."""
            idemp = ID_POR_EMPRESA.get(dd_emp.value or "")
            sucs = db.listar_sucursales_sipp(idemp) if idemp is not None else []
            deps = db.listar_departamentos(idemp) if idemp is not None else []
            dd_suc.options = [ft.DropdownOption(key=x, text=x) for x in sucs]
            dd_dep.options = [ft.DropdownOption(key=x, text=x) for x in deps]
            # Lo elegido antes puede no existir en la empresa nueva.
            dd_suc.value = None
            dd_dep.value = None
            faltan = [t for t, n in (("sucursales", len(sucs)),
                                     ("departamentos", len(deps))) if not n]
            aviso.value = (
                f"Esta empresa no tiene {' ni '.join(faltan)} en la caché. "
                "Descárgalos con «Actualizar SIPP» (arriba) y vuelve a abrir esta "
                "ventana." if faltan else "")
            aviso.visible = bool(faltan)
            modal.refrescar()

        # Se engancha por el parámetro del componente (no asignando el evento a
        # mano): `campo_opciones` traduce `on_change` al `on_select` real del
        # Dropdown y, de paso, fija el valor antes de llamar al manejador.
        _, dd_emp = campo_opciones("Empresa", list(NOMBRES_EMPRESAS), flotante=True,
                                   on_change=_recargar_dependientes)

        modal.cuerpo.controls = [
            ft.Text("¿A qué empresa, sucursal y departamento corresponde "
                    + ("la carpeta?" if metodo == "carpeta" else "el ZIP?"),
                    size=13, weight=ft.FontWeight.W_600),
            dd_emp, dd_suc, dd_dep, aviso,
            ft.Text("Sucursal y departamento salen del catálogo del SIPP de la "
                    "empresa elegida. Se aplican a todas las imágenes de esta carga "
                    "(después se pueden cambiar fila por fila).",
                    size=11, color=GRIS),
        ]

        async def _continuar(_e=None):
            empresa = (dd_emp.value or "").strip()
            sucursal = (dd_suc.value or "").strip()
            departamento = (dd_dep.value or "").strip()
            if not empresa:
                self.app.avisar("Elige una empresa.", NARANJA)
                return
            modal.cerrar()
            if metodo == "carpeta":
                await self._subir_carpeta(empresa, sucursal, departamento)
            else:
                await self._subir_zip(empresa, sucursal, departamento)

        modal.set_acciones([
            boton_herramienta("Cancelar", on_click=lambda _e: modal.cerrar()),
            boton_primario("Continuar", ft.Icons.ARROW_FORWARD, _continuar),
        ])
        modal.refrescar()

    async def _subir_carpeta(self, empresa: str = "", sucursal: str = "",
                             departamento: str = "") -> None:
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
        await self._registrar_imagenes(entradas, empresa, sucursal, departamento)

    async def _subir_zip(self, empresa: str = "", sucursal: str = "",
                         departamento: str = "") -> None:
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
            carpeta, extraidas, _carpetas = await asyncio.to_thread(
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
        await self._registrar_imagenes(
            archivos.listar_imagenes(carpeta), empresa, sucursal, departamento)

    async def _relacionar_imagenes(self, modal_origen=None,
                                   desde_zip: bool = False) -> None:
        """Empareja imágenes de una carpeta (o de un ZIP) con activos YA cargados.

        Pensado para la carga por Excel, que crea los registros sin foto: aquí se
        suben las del levantamiento y se asignan por lo que dice el nombre del
        archivo (etiqueta, serie o insumo). Lo que no se pueda emparejar solo, lo
        resuelve el usuario a mano.

        `desde_zip` pide un .zip en vez de una carpeta. Se extrae a la carpeta de
        datos de la app —no a una temporal— porque la ruta extraída es la que se
        guarda en el registro: desde ahí se abre la foto después, y una temporal
        dejaría la imagen rota en cuanto Windows la limpiara."""
        if modal_origen is not None:
            modal_origen.cerrar()
        # {ruta: subcarpeta} del ZIP. En una carpeta normal la estructura sigue en
        # disco y el emparejador la deduce de la ruta; el ZIP se extrae APLANADO
        # (por el límite de 260 caracteres de Windows), así que su árbol viaja
        # aquí.
        carpetas_zip: dict = {}
        if desde_zip:
            seleccion = await self.app.picker.pick_files(
                dialog_title="Selecciona el ZIP con las imágenes de los activos",
                allowed_extensions=["zip"], allow_multiple=False)
            if not seleccion:
                return
            self._set_cargando(True, f"Extrayendo «{seleccion[0].name}»…")
            try:
                carpeta, _extraidas, carpetas_zip = await asyncio.to_thread(
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
        else:
            carpeta = await self.app.picker.get_directory_path(
                dialog_title="Carpeta con las imágenes de los activos")
        if not carpeta:
            return
        try:
            entradas = archivos.listar_imagenes(carpeta)
        except OSError as exc:
            self.app.avisar(f"No se pudo leer la carpeta: {exc}", ROJO)
            return
        if not entradas:
            self.app.avisar(
                "El ZIP no contiene imágenes compatibles." if desde_zip else
                "La carpeta no contiene imágenes compatibles.", NARANJA)
            return

        # Solo los que NO tienen foto: relacionar no debe pisar una ya asignada.
        disponibles = [r for r in db.listar_levantamiento()
                       if not (r.ruta_imagen or "").strip()]
        if not disponibles:
            self.app.avisar("Todos los activos del levantamiento ya tienen imagen.",
                            NARANJA, duracion=7000)
            return

        # `carpeta` va como raíz para que el emparejador vea en qué subcarpeta
        # cayó cada foto: ahí es donde viene el nombre del responsable.
        pares = archivos.emparejar_imagenes(entradas, disponibles, carpeta,
                                            carpetas_zip)
        await self._modal_relacion(pares, disponibles)

    async def _modal_relacion(self, pares: list, disponibles: list) -> None:
        """Muestra el emparejamiento en tres pestañas y deja resolver lo suelto.

        La separación no es decorativa: cada grupo pide una acción distinta.
        «Emparejados» solo se revisa, «Posibles coincidencias» se confirma con un
        clic y «Sin coincidencia» exige buscar el activo. Mezclados en una sola
        lista, lo que necesita atención se pierde entre lo que ya está resuelto.
        """
        from ui.componentes import Pestanas

        decision: asyncio.Future = asyncio.get_running_loop().create_future()
        por_id = {r.id: r for r in disponibles}
        manuales: dict = {}      # archivo -> id_registro elegido a mano
        textos: dict = {}        # archivo -> ft.Text con la relación

        def responder(valor) -> None:
            if decision.done():
                return
            decision.set_result(valor)
            modal.cerrar()

        def _describir(rid: int) -> str:
            r = por_id.get(rid)
            if r is None:
                return ""
            partes = [r.nombre_insumo or ""]
            if r.etiqueta:
                partes.append(f"etiqueta {r.etiqueta}")
            if r.no_serie:
                partes.append(f"serie {r.no_serie}")
            return "  ·  ".join(p for p in partes if p)

        def _asignados() -> list:
            """[(id_registro, ruta)] de todo lo relacionado (automático + manual)."""
            items = [(p.id_registro, p.ruta) for p in pares if p.emparejado]
            items += [(rid, next(p.ruta for p in pares if p.archivo == arch))
                      for arch, rid in manuales.items()]
            return items

        def _refrescar_boton() -> None:
            btn.content = f"Relacionar ({len(_asignados())})"

        def elegir_para(par):
            async def _abrir(_e=None) -> None:
                usados = {p.id_registro for p in pares if p.emparejado}
                usados |= set(manuales.values())
                rid = await self._elegir_activo_para(
                    par.archivo, [r for r in disponibles if r.id not in usados])
                if rid is None:
                    return
                manuales[par.archivo] = rid
                textos[par.archivo].value = f"Se relacionará con {_describir(rid)}"
                textos[par.archivo].color = VERDE
                # Si la fila tiene casilla, queda marcada: el estado visible no
                # puede contradecir lo que se va a guardar.
                if par.archivo in casillas:
                    casillas[par.archivo].value = True
                _refrescar_boton()
                modal.refrescar()
            return lambda _e: self.page.run_task(_abrir)

        casillas: dict = {}      # archivo -> ft.Checkbox (solo las candidatas)

        def _marcar(par, rid: int):
            """Casilla de «Posibles coincidencias»: al marcar una, se desmarcan las
            demás del grupo. Un activo solo puede quedarse con UNA foto, así que se
            comporta como opción única aunque el control sea una casilla (que es lo
            que la gente espera para elegir de una lista)."""
            def _cambiar(_e=None) -> None:
                if casillas[par.archivo].value:
                    manuales[par.archivo] = rid
                    textos[par.archivo].value = f"Se relacionará con {_describir(rid)}"
                    textos[par.archivo].color = VERDE
                    for otro in pares:
                        if otro is par or otro.candidato_de != rid:
                            continue
                        manuales.pop(otro.archivo, None)
                        if otro.archivo in casillas:
                            casillas[otro.archivo].value = False
                        if otro.archivo in textos:
                            textos[otro.archivo].value = "Sin relacionar"
                            textos[otro.archivo].color = NARANJA
                else:
                    manuales.pop(par.archivo, None)
                    textos[par.archivo].value = "Sin relacionar"
                    textos[par.archivo].color = NARANJA
                _refrescar_boton()
                modal.refrescar()
            return _cambiar

        def _fila(par, con_atajo: bool) -> ft.Control:
            """Renglón de una imagen pendiente, con sus acciones."""
            txt = ft.Text("Sin relacionar", size=11, color=NARANJA, no_wrap=False)
            textos[par.archivo] = txt
            izquierda = [ft.Icon(ft.Icons.HELP_OUTLINE, size=16, color=NARANJA)]
            if con_atajo and par.candidato_de is not None:
                chk = ft.Checkbox(value=False)
                chk.on_change = _marcar(par, par.candidato_de)
                casillas[par.archivo] = chk
                izquierda = [chk]
            return ft.Row(
                izquierda
                + [ft.Column([ft.Text(par.archivo, size=12, no_wrap=False,
                                      color=ft.Colors.ON_SURFACE), txt],
                             spacing=0, tight=True, expand=True),
                   boton_secundario("Elegir activo", ft.Icons.SEARCH,
                                    elegir_para(par))],
                spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)

        emparejados = [p for p in pares if p.emparejado]
        posibles = [p for p in pares if not p.emparejado and p.candidato_de is not None]
        sueltas = [p for p in pares if not p.emparejado and p.candidato_de is None]

        # --- Pestaña 1: ya relacionadas -----------------------------------
        lista_ok = ft.ListView(spacing=6, expand=True)
        for p in emparejados:
            lista_ok.controls.append(ft.Row(
                [ft.Icon(ft.Icons.CHECK_CIRCLE, size=16, color=VERDE),
                 ft.Column([ft.Text(p.archivo, size=12, no_wrap=False,
                                    color=ft.Colors.ON_SURFACE),
                            ft.Text(f"{_describir(p.id_registro)}  ({p.motivo})",
                                    size=11, color=GRIS, no_wrap=False)],
                           spacing=0, tight=True, expand=True)],
                spacing=8, vertical_alignment=ft.CrossAxisAlignment.START))
        if not emparejados:
            lista_ok.controls.append(ft.Container(
                ft.Text("Ninguna imagen se pudo relacionar sola.", size=12, color=GRIS),
                padding=12))

        # --- Pestaña 2: varias fotos compiten por el mismo activo ---------
        lista_posibles = ft.ListView(spacing=6, expand=True)
        grupo = object()
        for p in sorted(posibles, key=lambda x: (x.candidato_de or 0, x.archivo)):
            if p.candidato_de != grupo:
                grupo = p.candidato_de
                cuantas = sum(1 for x in posibles if x.candidato_de == grupo)
                # El nombre del activo va en el color primario y en negrita: es
                # el dato que hay que leer para decidir, y en un bloque todo del
                # mismo color se perdía entre el resto de la frase.
                lista_posibles.controls.append(ft.Container(
                    ft.Text(spans=[
                        ft.TextSpan(f"{cuantas} imágenes podrían ser de ",
                                    ft.TextStyle(size=12,
                                                 color=ft.Colors.ON_SURFACE)),
                        ft.TextSpan(_describir(grupo),
                                    ft.TextStyle(size=13,
                                                 weight=ft.FontWeight.BOLD,
                                                 color=ft.Colors.PRIMARY)),
                        ft.TextSpan(".  Marca cuál es la correcta:",
                                    ft.TextStyle(size=12,
                                                 color=ft.Colors.ON_SURFACE)),
                    ], no_wrap=False),
                    padding=ft.Padding.only(top=10, bottom=2)))
            lista_posibles.controls.append(_fila(p, con_atajo=True))
        if not posibles:
            lista_posibles.controls.append(ft.Container(
                ft.Text("Sin ambigüedades: ninguna imagen compite por el mismo activo.",
                        size=12, color=GRIS), padding=12))

        # --- Pestaña 3: hay que buscarlas a mano --------------------------
        lista_manual = ft.ListView(spacing=6, expand=True)
        for p in sueltas:
            lista_manual.controls.append(_fila(p, con_atajo=False))
        if not sueltas:
            lista_manual.controls.append(ft.Container(
                ft.Text("Todas las imágenes tienen al menos un candidato.", size=12,
                        color=GRIS), padding=12))

        paneles = {"ok": lista_ok, "posibles": lista_posibles, "manual": lista_manual}
        for clave, panel in paneles.items():
            panel.visible = clave == "ok"

        def cambiar(clave: str) -> None:
            for c, panel in paneles.items():
                panel.visible = c == clave
            modal.refrescar()

        tabs = Pestanas(
            [("ok", "Emparejados", ft.Icons.CHECK_CIRCLE),
             ("posibles", "Posibles coincidencias", ft.Icons.HELP_OUTLINE),
             ("manual", "Sin coincidencia", ft.Icons.SEARCH)],
            al_cambiar=cambiar)
        tabs.set_conteo("ok", len(emparejados))
        tabs.set_conteo("posibles", len(posibles))
        tabs.set_conteo("manual", len(sueltas))

        btn = boton_primario(f"Relacionar ({len(emparejados)})", ft.Icons.LINK,
                             lambda _e: responder(_asignados()))
        modal = Modal(self.page, "Relacionar imágenes con activos", ancho=800,
                      subtitulo=f"{len(pares)} imagen(es)",
                      al_cerrar=lambda: responder(None))
        modal.cuerpo.controls = [
            ft.Text("Se empareja por el nombre del archivo —primero la etiqueta, "
                    "luego la serie y, si no, el insumo— y por la SUBCARPETA: si "
                    "está nombrada como el responsable, sus fotos se asignan a los "
                    "activos de esa persona.",
                    size=12, color=ft.Colors.ON_SURFACE, no_wrap=False),
            ft.Row([tabs.control]),
            ft.Container(ft.Column(list(paneles.values()), expand=True), height=320)]
        modal.set_acciones([
            boton_herramienta("Cancelar", on_click=lambda _e: responder(None)), btn])
        modal.abrir()

        asignados = await decision
        if not asignados:
            return
        for rid, ruta in asignados:
            db.fijar_imagen_levantamiento(rid, ruta)
        self._refrescar()
        self.app.avisar(f"{len(asignados)} imagen(es) relacionada(s) con su activo.",
                        VERDE, duracion=7000)

    async def _elegir_activo_para(self, archivo: str, candidatos: list):
        """Selector con buscador para asignar `archivo` a un activo. Devuelve su id."""
        decision: asyncio.Future = asyncio.get_running_loop().create_future()

        def responder(valor) -> None:
            if decision.done():
                return
            decision.set_result(valor)
            modal.cerrar()

        lista = lista_resultados()

        def pintar(filtro: str = "") -> None:
            f = (filtro or "").strip().casefold()
            vistos = [r for r in candidatos
                      if not f or f in " ".join(
                          (r.nombre_insumo or "", r.etiqueta or "",
                           r.no_serie or "")).casefold()]
            lista.controls = [
                fila_resultado(
                    r.etiqueta or "—", r.nombre_insumo or "(sin insumo)",
                    " · ".join(p for p in (
                        f"serie {r.no_serie}" if r.no_serie else "",
                        r.empresa or "", r.sucursal or "") if p),
                    on_click=lambda _e, rid=r.id: responder(rid))
                for r in vistos[:100]]
            if not vistos:
                lista.controls = [ft.Container(
                    ft.Text("Sin coincidencias.", size=12, color=GRIS), padding=12)]
            modal.refrescar()

        tf = buscador("Buscar por insumo, etiqueta o serie…", expand=True,
                      autofocus=True)
        tf.on_change = lambda e: pintar(e.control.value)
        modal = Modal(self.page, "¿A qué activo corresponde?", ancho=680,
                      subtitulo=archivo, al_cerrar=lambda: responder(None))
        modal.cuerpo.controls = [tf, lista]
        modal.set_acciones([
            boton_herramienta("Cancelar", on_click=lambda _e: responder(None))])
        pintar()
        modal.abrir()
        return await decision

    async def _registrar_imagenes(self, entradas: list[tuple[str, str]],
                                 empresa: str = "", sucursal: str = "",
                                 departamento: str = "") -> None:
        """Da de alta un registro por imagen, deduciendo insumo y ETIQUETA de su
        nombre (ver core.archivos.parsear_nombre).

        Si un archivo termina en un número que NO tiene largo de etiqueta se
        pregunta antes de registrar: casi siempre es el número de serie, pero el
        nombre por sí solo no lo distingue, y meterlo como etiqueta arruinaría la
        búsqueda en el SIPP."""
        leidas = [(nombre, ruta, archivos.parsear_nombre(nombre))
                  for nombre, ruta in entradas]
        ambiguos = [(nombre, datos) for nombre, _r, datos in leidas
                    if datos.necesita_confirmar]
        es_serie = {}
        if ambiguos:
            es_serie = await self._confirmar_numeros_ambiguos(ambiguos)
            if es_serie is None:
                return                      # el usuario canceló la carga

        agregadas, omitidas, con_etiqueta, con_serie = 0, 0, 0, 0
        for nombre, ruta, datos in leidas:
            insumo, etiqueta, serie = datos.insumo, datos.etiqueta, datos.serie
            if datos.necesita_confirmar:
                if es_serie.get(nombre):
                    serie = datos.numero_ambiguo
                else:
                    # No era serie: el número es parte del nombre, así que se
                    # restaura el original en vez de perderlo al cortar.
                    insumo = datos.base
            if not insumo:
                omitidas += 1
                continue
            nuevo = db.guardar_levantamiento(
                insumo, serie, ruta, empresa=empresa, sucursal=sucursal,
                departamento=departamento, etiqueta=etiqueta)
            if nuevo is None:
                omitidas += 1
            else:
                agregadas += 1
                con_etiqueta += bool(etiqueta)
                con_serie += bool(serie)
        self._refrescar()
        msg = f"{agregadas} imagen(es) agregada(s)"
        detalle = []
        if con_etiqueta:
            detalle.append(f"{con_etiqueta} con etiqueta")
        if con_serie:
            detalle.append(f"{con_serie} con serie")
        if detalle:
            msg += " (" + ", ".join(detalle) + ")"
        msg += "."
        if omitidas:
            msg += f" {omitidas} omitida(s) (repetidas o sin nombre válido)."
        self.app.avisar(msg, VERDE if agregadas else NARANJA, duracion=8000)

    async def _confirmar_numeros_ambiguos(self, ambiguos: list):
        """Pregunta, archivo por archivo, si el número final es el No. de serie.

        Devuelve {nombre_archivo: es_serie} o None si se cancela. Vienen marcados
        que SÍ porque es lo habitual: la convención anterior de nombres era
        INSUMO_SERIE, así que los levantamientos viejos caen todos aquí."""
        decision = asyncio.get_running_loop().create_future()
        checks = {}

        def responder(valor) -> None:
            # Salida temprana OBLIGATORIA: `modal.cerrar()` dispara `al_cerrar`,
            # que vuelve a entrar aquí. Sin esto se recursan mutuamente hasta
            # reventar (RecursionError con solo cerrar el diálogo).
            if decision.done():
                return
            decision.set_result(valor)
            modal.cerrar()

        lista = ft.ListView(spacing=4, expand=True)
        for nombre, datos in ambiguos:
            chk = ft.Checkbox(value=True)
            checks[nombre] = chk
            lista.controls.append(ft.Row(
                [chk,
                 ft.Column(
                     [ft.Text(nombre, size=12, color=ft.Colors.ON_SURFACE,
                              no_wrap=False),
                      ft.Text(f"insumo: «{datos.insumo}» · número: "
                              f"{datos.numero_ambiguo} "
                              f"({len(datos.numero_ambiguo)} dígitos)",
                              size=11, color=GRIS, no_wrap=False)],
                     spacing=0, tight=True, expand=True)],
                spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER))

        largos = ", ".join(str(n) for n in archivos.LARGOS_ETIQUETA)
        modal = Modal(self.page, "¿Estos números son el No. de serie?", ancho=640,
                      subtitulo=f"{len(ambiguos)} archivo(s)",
                      al_cerrar=lambda: responder(None))
        modal.cuerpo.controls = [
            ft.Text(f"Una etiqueta del SIPP tiene {largos} dígitos. Estos archivos "
                    "terminan en un número de otro largo, así que no se tomó como "
                    "etiqueta.", size=12, color=ft.Colors.ON_SURFACE, no_wrap=False),
            ft.Text("Marcado = se guarda como No. de serie. Sin marcar = el número "
                    "es parte del nombre del insumo.", size=11, color=GRIS,
                    no_wrap=False),
            ft.Container(lista, height=300)]
        modal.set_acciones([
            boton_herramienta("Cancelar carga", on_click=lambda _e: responder(None)),
            boton_primario(
                "Continuar", ft.Icons.CHECK,
                lambda _e: responder({n: c.value for n, c in checks.items()})),
        ])
        modal.abrir()
        return await decision

    # ------------------------------------------------------ búsqueda en SIPP
    async def _buscar(self, _e=None) -> None:
        """Compara cada activo del levantamiento contra los activos REALES ya
        descargados del SIPP: dado de alta si su ETIQUETA aparece en la caché.

        La búsqueda es GENERAL: recorre las empresas descargadas, no solo la que
        el registro tenga asignada. Antes, un activo cuya empresa estuviera mal
        capturada —o vacía— salía «no dado de alta» aunque existiera en el SIPP, y
        el RPA lo habría vuelto a crear.
        """
        registros = db.listar_levantamiento()
        if not registros:
            self.app.avisar("No hay activos en el levantamiento para buscar.", ROJO)
            return
        if not db.hay_activos_sipp():
            self.app.avisar(
                "No hay activos del SIPP descargados con qué comparar. Corre "
                "«Actualizar SIPP» de al menos una empresa.", ROJO, duracion=9000)
            return

        self._set_cargando(True, f"Buscando {len(registros)} activo(s) en el SIPP…")
        try:
            hechos, ambiguos = await asyncio.to_thread(
                self._clasificar_contra_sipp, registros)
        except Exception as exc:  # noqa: BLE001 — se reporta al usuario
            self._set_cargando(False)
            self.app.avisar(f"No se pudo buscar en el SIPP: {exc}", ROJO)
            return
        self._set_cargando(False)

        # Las etiquetas que existen en varias empresas las resuelve el usuario: son
        # activos DISTINTOS que comparten número, y elegir uno solo por orden le
        # copiaría al registro el insumo y el resguardante de otro.
        sin_resolver = 0
        if ambiguos:
            self._refrescar()
            elegidos = await self._resolver_etiquetas_ambiguas(ambiguos)
            if elegidos:
                for r, datos in elegidos:
                    self._aplicar_resultado_sipp(r, datos)
                    hechos += 1
            sin_resolver = len(ambiguos) - len(elegidos or [])

        # Respaldo en el portal: lo que la caché no encontró puede existir en una
        # empresa que nunca se descargó. Es la única forma de saberlo sin bajar el
        # catálogo de las 58 empresas.
        self._refrescar()
        hallados_portal = await self._respaldo_portal()

        n_dado = len(db.listar_levantamiento_por_estatus(db.EST_DADO_ALTA))
        n_no = len(db.listar_levantamiento_por_estatus(db.EST_NO_DADO_ALTA))
        self._refrescar()
        msg = f"Búsqueda completada: {n_dado} dado(s) de alta, {n_no} sin dar de alta."
        extras = []
        if hallados_portal:
            extras.append(f"{hallados_portal} encontrado(s) en otra empresa "
                          f"consultando el portal")
        if sin_resolver:
            # No quedan como «no dado de alta»: eso invitaría al RPA a duplicarlos.
            # Se quedan pendientes, que es lo que realmente son.
            extras.append(f"{sin_resolver} sin resolver (siguen pendientes)")
        if extras:
            msg += " · " + " · ".join(extras)
        self.app.avisar(msg, VERDE if not extras else NARANJA,
                        duracion=9000 if extras else 6000)

    async def _respaldo_portal(self) -> int:
        """Consulta en el PORTAL las etiquetas que la caché local no encontró.

        La búsqueda local solo ve las empresas descargadas; el listado del SIPP, en
        cambio, se puede consultar sin ámbito y dice a qué empresa pertenece cada
        etiqueta. Devuelve cuántas se encontraron.

        Corre automáticamente al terminar la búsqueda, pero SOLO sobre lo que quedó
        «no dado de alta» CON etiqueta: sin etiqueta no hay nada que preguntar, y
        repetir lo ya resuelto sería pagar el portal por gusto.

        Si el portal no devuelve nada para una etiqueta NO se toca el registro: el
        listado oculta ciertos activos (bajas, fuera del alcance del usuario), así
        que un vacío significa «no se pudo confirmar», no «no existe». Ya está
        marcado como no dado de alta por la búsqueda local, que es la lectura
        prudente.
        """
        candidatos = [r for r in db.listar_levantamiento_por_estatus(db.EST_NO_DADO_ALTA)
                      if (r.etiqueta or "").strip()]
        if not candidatos:
            return 0
        creds = credenciales.cargar()
        if not creds or not creds[0]:
            self.app.avisar(
                f"{len(candidatos)} etiqueta(s) no están en la caché local. Para "
                "buscarlas en otras empresas configura las credenciales del SIPP "
                "(botón ⚙).", NARANJA, duracion=9000)
            return 0
        usuario, contrasena = creds

        total = len(candidatos)
        bucle = BucleRpa()
        ctrl = ControlRpa(bucle.loop)
        ui_loop = asyncio.get_running_loop()

        txt = ft.Text(f"Conectando al SIPP… (0/{total})", size=13)
        barra = ft.ProgressBar(value=0)
        # El detalle de qué se está buscando va a la vista, no solo un contador:
        # son varios minutos y el usuario necesita ver que avanza sobre SUS
        # activos, no sobre una barra anónima.
        lista = ft.ListView(spacing=4, expand=True, auto_scroll=True)

        def pedir_detener(_e=None) -> None:
            ctrl.detener()
            btn_detener.disabled = True
            btn_detener.content = "Deteniendo…"
            modal.refrescar()

        btn_detener = boton_herramienta("Detener", on_click=pedir_detener,
                                        destructivo=True)
        modal = Modal(self.page, "Buscando etiquetas en otras empresas", ancho=620,
                      subtitulo=f"{total} etiqueta(s) fuera de la caché local",
                      acciones=[btn_detener])
        # Cada consulta recarga la grid por AJAX: ~4 s entre navegación y espera.
        minutos = max(1, round(total * 4 / 60))
        modal.cuerpo.controls = [
            ft.Text("Estas etiquetas no están en las empresas descargadas. Se "
                    "consultan una por una en el catálogo del SIPP, sin filtro de "
                    "empresa, para ver si pertenecen a otra.",
                    size=12, color=ft.Colors.ON_SURFACE, no_wrap=False),
            ft.Text(f"Son {total} consultas: unos {minutos} minuto(s). Puedes "
                    "detenerlo cuando quieras; lo ya encontrado se conserva.",
                    size=11, color=NARANJA, no_wrap=False),
            txt, barra, ft.Container(lista, height=240),
            ft.Text("Se abrirá un navegador; no lo cierres.", size=11, color=GRIS)]
        modal.abrir()

        def avance(i: int, r, resultado: str, color) -> None:
            """Refleja el avance desde el hilo del RPA (marshalado a la UI)."""
            def aplicar() -> None:
                barra.value = i / total
                txt.value = f"Consultando el portal… ({i}/{total})"
                lista.controls.append(ft.Row(
                    [ft.Text(f"{r.nombre_insumo or '(sin insumo)'}  ·  {r.etiqueta}",
                             size=12, color=ft.Colors.ON_SURFACE, expand=True,
                             no_wrap=False),
                     ft.Text(resultado, size=11, color=color, no_wrap=False)],
                    spacing=8, vertical_alignment=ft.CrossAxisAlignment.START))
                modal.refrescar()
            ui_loop.call_soon_threadsafe(aplicar)

        encontrados: list = []
        error = None

        async def flujo() -> None:
            nonlocal error
            from core.rpa_sipp import SesionSipp, mensaje_amigable
            try:
                async with SesionSipp(headless=True) as sipp:
                    await sipp.login(usuario, contrasena)
                    # El catálogo no monta sin sesión configurada. CUÁL empresa da
                    # igual —el ámbito se limpia antes de cada búsqueda—, pero
                    # tiene que ser una que el usuario tenga: si la del registro
                    # no aparece en su selector, se cae a la primera del catálogo
                    # en vez de tumbar todo el respaldo.
                    for intento in ((candidatos[0].empresa or "").strip(),
                                    *NOMBRES_EMPRESAS):
                        if not intento:
                            continue
                        try:
                            await sipp.preparar_sesion_empresa(intento)
                            break
                        except ErrorSipp:
                            continue
                    else:
                        raise ErrorSipp(
                            "No se pudo configurar la sesión con ninguna empresa.")
                    for i, r in enumerate(candidatos, 1):
                        await ctrl.punto_control()
                        filas = await sipp.buscar_activo_global(r.etiqueta or "")
                        if filas:
                            encontrados.append((r, filas[0]))
                            avance(i, r,
                                   f"en {filas[0].get('empresa') or '(sin empresa)'}",
                                   VERDE)
                        else:
                            avance(i, r, "no aparece en el portal", GRIS)
            except RpaDetenido:
                pass
            except Exception as exc:  # noqa: BLE001 — se reporta al usuario
                error = mensaje_amigable(exc)

        try:
            await asyncio.wrap_future(bucle.enviar(flujo()))
        finally:
            bucle.cerrar()
            modal.cerrar()

        for r, datos in encontrados:
            self._aplicar_resultado_sipp(r, datos)
        if error:
            self.app.avisar(f"La consulta al portal falló: {error}", NARANJA,
                            duracion=9000)
        return len(encontrados)

    def _marcar_no_dado_alta(self, r: "db.Levantamiento") -> None:
        db.actualizar_estatus_levantamiento(r.id, db.EST_NO_DADO_ALTA, None, None)

    def _aplicar_resultado_sipp(self, r: "db.Levantamiento", datos_sipp: dict) -> None:
        """Marca el registro como dado de alta y adopta lo que el SIPP ya sabe.

        Está fuera del bucle porque lo usan los dos caminos: el automático y el
        del modal de desambiguación. Duplicarlo garantizaría que uno de los dos se
        quedara atrás al tocar el prellenado."""
        id_sipp = (datos_sipp.get("etiqueta") or "").strip() or None
        db.actualizar_estatus_levantamiento(r.id, db.EST_DADO_ALTA, id_sipp, datos_sipp)
        try:
            idt = int(datos_sipp.get("id_tipo"))
        except (TypeError, ValueError):
            idt = None
        id_tipo_nuevo = (idt if idt in TIPOS_ACTIVO
                         and r.id_tipo_activo is None else None)
        prefill = _prefill_desde_sipp(datos_sipp) if not r.datos() else None
        # Los activos dados de alta ANTES de reflejar la serie quedaron con la
        # columna vacía; al reconocerlos en el SIPP se adopta la suya (que suele
        # ser su propia etiqueta).
        serie_sipp = str(datos_sipp.get("serie") or "").strip()
        serie_nueva = (serie_sipp if serie_sipp
                       and not (r.no_serie or "").strip() else None)
        # Si se adopta la serie del SIPP, el nombre no debe seguir arrastrándola
        # pegada al final.
        limpio = (archivos.nombre_sin_serie(
            r.nombre_insumo, serie_nueva, r.etiqueta or "")
            if serie_nueva else None)
        if limpio == r.nombre_insumo:
            limpio = None
        if id_tipo_nuevo is not None or prefill or serie_nueva:
            db.actualizar_datos_levantamiento(
                r.id, id_tipo_activo=id_tipo_nuevo, datos=prefill,
                no_serie=serie_nueva, nombre_insumo=limpio)

    def _clasificar_contra_sipp(self, registros: list) -> tuple:
        """(hilo) Resuelve cada registro contra la caché del SIPP por ETIQUETA.

        Devuelve `(hechos, ambiguos)`, con `ambiguos = [(registro, candidatos)]`.

        Criterio: el identificador del alta es la ETIQUETA. Sin etiqueta se da por
        NO dado de alta (ni se busca). Si la etiqueta aparece en la empresa del
        propio registro se usa esa —dentro de una empresa la etiqueta es única, así
        que no hay duda—; si no, se acepta la coincidencia global cuando es una
        sola y se difiere al usuario cuando hay varias.
        """
        from core import activos_sipp
        from core.empresas import ID_POR_EMPRESA

        # Refresco previo por API (HTTP, sin navegador ni login) de las empresas que
        # los registros mencionan: la comparación es contra el SIPP de AHORA. Es
        # best-effort; si la API no está configurada o falla, se usa la caché tal
        # como esté, que es el comportamiento de siempre.
        if activos_sipp.hay_api():
            for nombre in {(r.empresa or "").strip() for r in registros}:
                idemp = ID_POR_EMPRESA.get(nombre)
                if idemp is None:
                    continue
                try:
                    activos_sipp.descargar_activos_api(idemp, nombre)
                except Exception:  # noqa: BLE001 — se cae a la caché existente
                    pass

        candidatos = db.activos_sipp_por_etiquetas(
            [(r.etiqueta or "") for r in registros])
        hechos, ambiguos = 0, []
        for r in registros:
            etq = (r.etiqueta or "").strip()
            opciones = candidatos.get(etq.upper(), []) if etq else []
            if not opciones:
                self._marcar_no_dado_alta(r)
                hechos += 1
                continue
            idemp = ID_POR_EMPRESA.get((r.empresa or "").strip())
            propio = next((c for c in opciones
                           if c.get("id_empresa") == idemp), None) if idemp else None
            if propio is not None:
                self._aplicar_resultado_sipp(r, propio)
                hechos += 1
            elif len(opciones) == 1:
                self._aplicar_resultado_sipp(r, opciones[0])
                hechos += 1
            else:
                ambiguos.append((r, opciones))
        return hechos, ambiguos

    async def _resolver_etiquetas_ambiguas(self, ambiguos: list) -> list:
        """Pide elegir a qué activo corresponde cada etiqueta repetida.

        `ambiguos`: [(registro, candidatos)]. Devuelve [(registro, activo elegido)]
        SOLO de los resueltos: lo que no se elija se queda PENDIENTE, no «no dado
        de alta» — marcarlo así invitaría al RPA a crear un duplicado de un activo
        que sí existe.
        """
        decision: asyncio.Future = asyncio.get_running_loop().create_future()
        campos: dict = {}          # id de registro -> (registro, dropdown, opciones)

        def responder(valor) -> None:
            if decision.done():
                return
            decision.set_result(valor)
            modal.cerrar()

        def _rotulo(c: dict) -> str:
            partes = [c.get("empresa") or "(sin empresa)",
                      c.get("insumo") or "(sin insumo)"]
            if c.get("serie"):
                partes.append(f"serie {c['serie']}")
            if c.get("sucursal"):
                partes.append(c["sucursal"])
            return "  ·  ".join(partes)

        lista = ft.ListView(spacing=10, expand=True)
        for r, opciones in ambiguos:
            rotulos = [_rotulo(c) for c in opciones]
            _, dd = campo_opciones("¿Cuál es?", rotulos, flotante=True)
            campos[r.id] = (r, dd, dict(zip(rotulos, opciones)))
            lista.controls.append(ft.Container(
                ft.Column(
                    [ft.Row([ft.Icon(ft.Icons.HELP_OUTLINE, size=16, color=NARANJA),
                             ft.Text(f"Etiqueta {r.etiqueta}", size=13,
                                     weight=ft.FontWeight.W_600,
                                     color=ft.Colors.ON_SURFACE, expand=True)],
                            spacing=6),
                     ft.Text(f"En el levantamiento: {r.nombre_insumo or '(sin insumo)'}"
                             + (f"  ·  {r.empresa}" if r.empresa else ""),
                             size=11, color=GRIS, no_wrap=False),
                     ft.Text(f"Existe en {len(opciones)} empresas:", size=11,
                             color=GRIS),
                     dd],
                    spacing=6, tight=True),
                padding=ft.Padding.symmetric(horizontal=10, vertical=10),
                bgcolor=ft.Colors.SURFACE_CONTAINER_LOWEST,
                border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
                border_radius=8))

        def _confirmar(_e=None) -> None:
            elegidos = []
            for r, dd, mapa in campos.values():
                activo = mapa.get(dd.value or "")
                if activo is not None:
                    elegidos.append((r, activo))
            responder(elegidos)

        modal = Modal(self.page, "¿A qué activo corresponde cada etiqueta?",
                      ancho=760, subtitulo=f"{len(ambiguos)} etiqueta(s) repetida(s)",
                      alto_cuerpo=520, al_cerrar=lambda: responder([]))
        modal.cuerpo.controls = [
            ft.Text("Estas etiquetas existen en varias empresas del SIPP y no son el "
                    "mismo activo, así que la herramienta no puede decidir sola.",
                    size=12, color=ft.Colors.ON_SURFACE, no_wrap=False),
            ft.Text("Lo que dejes sin elegir queda PENDIENTE: no se marca como «sin "
                    "dar de alta», porque el RPA lo daría de alta otra vez.",
                    size=11, color=GRIS, no_wrap=False),
            ft.Container(lista, height=340)]
        modal.set_acciones([
            boton_herramienta("Dejar pendientes", on_click=lambda _e: responder([])),
            boton_primario("Aplicar", ft.Icons.CHECK, _confirmar)])
        modal.abrir()
        return await decision

    # ------------------------------------------------ RPA: alta en el SIPP
    @staticmethod
    def _imagenes_para_alta(r: "db.Levantamiento") -> list:
        """Fotos que el RPA sube al dar de alta (el SIPP admite hasta 3).

        Salen de `imagenes_insumo` —lo adjuntado en la ficha— y, si ahí no hay,
        de la FOTO del levantamiento: es la imagen con la que se creó el registro
        (o la que se le relacionó después), y se espera que suba aunque nadie haya
        abierto la ficha. Sin este respaldo, un alta hecha directo desde la tabla
        llegaba al portal sin fotografía.

        Se descartan las rutas que ya no existen: subir un archivo borrado aborta
        el alta entera en el portal.
        """
        datos = r.datos()
        imagenes = [p for p in (datos.get("imagenes_insumo") or [])
                    if p and os.path.exists(p)]
        if imagenes:
            return imagenes[:3]
        ruta = (r.ruta_imagen or "").strip()
        return [ruta] if ruta and os.path.exists(ruta) else []

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
        if not insumo_id:
            # Se resuelve por NOMBRE, pero SOLO si el catálogo tiene ese nombre
            # exacto. Con varias variantes ('NVR 32 CANALES', 'NVR DE 4 CANALES'…)
            # se deja vacío a propósito: el flujo de alta lo apartará para que el
            # usuario elija, en vez de registrar una que quizá no es.
            from core.insumos import resolver
            elegido, exacto = resolver(
                (datos.get("nb_NombreInsumo") or r.nombre_insumo or "").strip(),
                ID_POR_EMPRESA.get((r.empresa or "").strip()))
            if elegido is not None and exacto:
                insumo_id = str(elegido.id_insumo)
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

        # Nada de abrir el portal con datos incompletos: se valida primero.
        pendientes = await self._validar_obligatorios(pendientes)
        if not pendientes:
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
        faltan_insumo: list = []         # (registro, fila) sin insumo claro
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
                        "_sucursal": r.sucursal or "", "_id": r.id,
                        # Para deshacer si el alta no se confirma en el portal.
                        "_etiqueta_previa": r.etiqueta or "",
                        "_serie_previa": r.no_serie or ""}
                resultados.append(fila)
                if not await dar_alta(sipp, r, fila):
                    # Sin insumo claro: NO se intenta. Se aparta para preguntarle
                    # al usuario al terminar el resto (ver _elegir_insumos_faltantes).
                    fila["observacion"] = ("Sin coincidencia exacta en el catálogo; "
                                           "se elegirá al terminar.")
                    faltan_insumo.append((r, fila))

        async def dar_alta(sipp, r, fila, insumo_forzado: str = "") -> bool:
            """Da de alta UN activo y vuelca el resultado en `fila`.

            Devuelve False si no hay insumo con el que intentarlo (ni resuelto ni
            forzado): el llamador decide qué hacer con ese caso."""
            tipo, campos, detalles, insumo_id, empleado_id = self._payload_alta(r)
            insumo_id = (insumo_forzado or insumo_id or "").strip()
            if not insumo_id:
                return False
            try:
                # El alta devuelve la ETIQUETA que el SIPP generó; se guarda
                # en el registro (id del activo = su etiqueta).
                etiqueta_gen = await sipp.alta_activo(
                    tipo, campos, detalles, insumo_id, empleado_id,
                    serie=r.no_serie or "", etiqueta_actual=r.etiqueta or "",
                    empresa=r.empresa or "", sucursal=r.sucursal or "",
                    empleado_nombre=(r.datos().get("nb_Empleado")
                                     or r.responsable or ""),
                    imagenes=self._imagenes_para_alta(r))
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
            return True

        async def flujo_elegidos(elegidos: list, avanzar) -> None:
            """Segunda vuelta: da de alta lo que el usuario resolvió a mano."""
            for n, (r, fila, id_insumo) in enumerate(elegidos, 1):
                await ctrl.punto_control()
                avanzar(n, r.nombre_insumo)
                await dar_alta(sipp, r, fila, insumo_forzado=id_insumo)

        detenido = False
        try:
            await asyncio.wrap_future(bucle.enviar(flujo()))
        except RpaDetenido:
            detenido = True
        except Exception as exc:  # noqa: BLE001 — se reporta al usuario
            errores_generales.append(mensaje_amigable(exc))

        # Los activos sin insumo claro se resuelven AHORA, con el resto ya dado de
        # alta y el navegador todavía abierto: el usuario elige y se registran en
        # la misma sesión, sin volver a entrar al portal.
        if faltan_insumo and not detenido and self._navegador_vivo(sipp):
            modal.cerrar()
            elegidos = await self._elegir_insumos_faltantes(faltan_insumo)
            if elegidos:
                # Modal de progreso NUEVO para la segunda vuelta. Reabrir el
                # anterior con `abrir()` reventaba con "Dialog is already opened":
                # una vez cerrado, esa instancia no se puede volver a mostrar.
                txt2 = ft.Text(f"Dando de alta {len(elegidos)} activo(s)…", size=13)
                barra2 = ft.ProgressBar(value=0)
                modal2 = Modal(self.page, "Registrando los insumos elegidos",
                               ancho=460)
                modal2.cuerpo.controls = [txt2, barra2]
                modal2.abrir()

                def avance2(hechos: int, nombre: str) -> None:
                    def aplicar() -> None:
                        txt2.value = f"({hechos}/{len(elegidos)}) {nombre}"
                        barra2.value = hechos / len(elegidos)
                        modal2.refrescar()
                    ui_loop.call_soon_threadsafe(aplicar)

                try:
                    await asyncio.wrap_future(
                        bucle.enviar(flujo_elegidos(elegidos, avance2)))
                except RpaDetenido:
                    detenido = True
                except Exception as exc:  # noqa: BLE001 — se reporta al usuario
                    errores_generales.append(mensaje_amigable(exc))
                finally:
                    modal2.cerrar()

        # Confirmación final: por cada empresa se trae su listado del SIPP, se
        # verifica que las etiquetas generadas estén (que el alta realmente quedó)
        # y se guarda la foto del activo. Va FUERA del flujo para que también corra
        # al DETENER: si no, las altas ya hechas se quedaban sin confirmar y sin
        # foto solo por haber parado el proceso.
        if any(f.get("estatus") == reporte_altas.ALTA for f in resultados):
            # `avance` pinta sobre el modal principal; si ya se cerró (hubo segunda
            # vuelta) no pasa nada: refrescar un modal cerrado es inofensivo.
            avance(total, "Confirmando altas…")
            try:
                await asyncio.wrap_future(
                    bucle.enviar(self._confirmar_altas(sipp, resultados)))
            except Exception as exc:  # noqa: BLE001 — las altas ya se hicieron
                errores_generales.append(
                    f"No se pudieron confirmar las altas: {mensaje_amigable(exc)}")
        # Ojo: aquí NO se apaga el bucle. Sigue vivo porque es el único hilo desde
        # el que se puede cerrar el navegador que queda abierto; lo apaga el
        # reporte al cerrarlo.
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
        from collections import Counter, defaultdict

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
            # La ETIQUETA es un consecutivo GLOBAL que el portal entrega ANTES de
            # guardar y solo avanza cuando el alta se completa: si varias altas
            # comparten etiqueta, todas menos una fallaron. Con la etiqueta
            # repetida, buscarla en el listado daba "confirmado" a todas.
            repetidas = {e for e, n in Counter(
                (f.get("etiqueta") or "").strip() for f in filas_e).items()
                if e and n > 1}
            for fila in filas_e:
                if hallados is None:
                    fila["observacion"] += "  ·  No se pudo confirmar en el SIPP."
                    continue
                etq = (fila.get("etiqueta") or "").strip()
                res = hallados.get(etq)
                datos_sipp = (res.datos if res is not None else None) or {}
                # El desempate real es la SERIE: es el dato que la herramienta
                # envía y que identifica al activo dentro de esa etiqueta.
                serie_portal = (datos_sipp.get("serie") or "").strip().upper()
                serie_fila = (fila.get("serie") or "").strip().upper()
                coincide = bool(serie_portal) and serie_portal == serie_fila
                if res is not None and res.dado_de_alta and coincide:
                    fila["observacion"] += "  ·  Confirmado en el SIPP."
                    if fila.get("_id") is not None and datos_sipp:
                        db.actualizar_estatus_levantamiento(
                            fila["_id"], db.EST_DADO_ALTA, res.id_activo_sipp,
                            datos_sipp)
                    continue
                # No cuajó: se explica POR QUÉ y se deshace lo que la herramienta
                # había dado por hecho (etiqueta y estatus), o el activo quedaría
                # marcado como dado de alta con la etiqueta de otro.
                fila["estatus"] = reporte_altas.PENDIENTE
                if etq in repetidas:
                    motivo = (f"El SIPP no guardó el alta: la etiqueta {etq} quedó "
                              "asignada a otro activo del mismo lote.")
                elif res is None or not res.dado_de_alta:
                    motivo = "No aparece en el listado del SIPP (revisar)."
                else:
                    motivo = (f"En el SIPP la etiqueta {etq} corresponde a otro "
                              f"activo (serie {serie_portal or '—'}): el alta no se "
                              "guardó.")
                fila["observacion"] += "  ·  " + motivo
                if fila.get("_id") is not None:
                    db.actualizar_estatus_levantamiento(
                        fila["_id"], db.EST_NO_DADO_ALTA, None, None)
                    db.fijar_etiqueta_levantamiento(
                        fila["_id"], fila.get("_etiqueta_previa") or "")
                    db.actualizar_datos_levantamiento(
                        fila["_id"], no_serie=fila.get("_serie_previa") or "")
                fila["etiqueta"] = fila.get("_etiqueta_previa") or ""
                fila["serie"] = fila.get("_serie_previa") or ""

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
            self.app.avisar(error_al_guardar(exc, ruta), ROJO, duracion=10000)
            return
        self.app.avisar("Reporte exportado.", VERDE, accion="Abrir",
                        on_accion=lambda _e, x=ruta: self.app.abrir_en_sistema(x),
                        duracion=8000)

    # --------------------------------------------- RPA: modificación en SIPP
    # Red de seguridad: alta y edición ya declaran los localizadores de RESGUARDO,
    # pero si alguno se declarara con el nombre de compra la edición no lo
    # encontraría (allí solo existe el de resguardo) y el cambio se perdería en
    # silencio. Traducirlo aquí cuesta nada y evita ese fallo mudo.
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

    @staticmethod
    def _fijar_insumo_origen(datos: dict, nombre: str, reg: "db.Levantamiento",
                             gana_excel: bool) -> bool:
        """Deja en `datos['id_InsumoOrigen']` el ID del insumo que debe quedar.
        Devuelve si quedó un ID utilizable para empujar al SIPP.

        Es lo que separa «el usuario eligió este insumo» de «el nombre se parece a
        este»: el RPA solo cambia el insumo cuando hay un ID puesto aquí. Si ganó el
        SIPP se copia el ID del portal —así el RPA no intenta corregir nada—; si
        ganó el Excel se exige que el nombre resuelva a UN insumo EXACTO del
        catálogo, porque empujar el parecido cambiaría el activo por otro.
        """
        if not gana_excel:
            id_sipp = str((reg.info_sipp() or {}).get("id_insumo_origen") or "").strip()
            if id_sipp:
                datos["id_InsumoOrigen"] = id_sipp
            else:
                datos.pop("id_InsumoOrigen", None)
            return False        # ganó el SIPP: no hay nada que empujar
        from core.insumos import resolver
        elegido, exacto = resolver((nombre or "").strip(),
                                   ID_POR_EMPRESA.get((reg.empresa or "").strip()))
        if elegido is not None and exacto:
            datos["id_InsumoOrigen"] = str(elegido.id_insumo)
            return True
        datos.pop("id_InsumoOrigen", None)
        return False

    def _payload_modificacion(self, r: "db.Levantamiento") -> tuple:
        """Igual que _payload_alta pero con los localizadores del formulario de
        edición. Devuelve (tipo, campos, detalles, insumo_id).

        El `insumo_id` va aparte porque no es un campo de texto: el RPA lo elige en
        el modal «Buscar Insumo» de la edición.

        Se toma SOLO de `id_InsumoOrigen`, que es un insumo ELEGIDO (en la ficha del
        activo o al reconciliar), y no del nombre como hace el alta. La diferencia
        importa: aquí ya hay un activo vivo en el portal, y resolver por nombre
        cambiaría su insumo por el parecido en cada modificación, aunque el usuario
        solo hubiera venido a corregir la ubicación. El empleado, en cambio, no se
        puede cambiar ahí de ninguna forma."""
        tipo, campos, detalles, _insumo_alta, _empleado_id = self._payload_alta(r)
        campos_edicion = [(self._a_ng_model_edicion(ng), v, c) for ng, v, c in campos]
        insumo_id = (r.datos().get("id_InsumoOrigen") or "").strip()
        return tipo, campos_edicion, detalles, insumo_id

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
        """Bitácora: Altas, Modificaciones y Sesiones de uso de la herramienta.

        Las dos primeras responden la pregunta de operación «¿qué se hizo y
        cuándo?», que antes solo vivía en el reporte de la corrida y se perdía al
        cerrarlo. La tercera responde la de adopción —«¿se está usando?»—, que las
        otras dos no pueden: un equipo que abre la herramienta a diario para
        consultar no deja NINGÚN movimiento.
        """
        resumen = db.resumen_movimientos()
        uso = db.resumen_sesiones_uso()
        if not resumen and not uso["sesiones"]:
            self.app.avisar(
                "Todavía no hay nada en la bitácora. Se va llenando conforme se "
                "abra la herramienta y se den de alta o modifiquen activos.",
                NARANJA, duracion=8000)
            return

        LIMITE = 300
        lista = ft.ListView(spacing=6, expand=True)
        estado = ft.Text("", size=12, color=GRIS)
        tf = buscador("Buscar por etiqueta, insumo o serie…", expand=True)
        # Periodo: por calendario, como todas las fechas del proyecto. Vacío =
        # sin tope por ese lado, para poder pedir "de tal día en adelante".
        f_desde = CampoFecha(self.page, "Desde", flotante=True,
                             on_change=lambda _v: pintar())
        f_hasta = CampoFecha(self.page, "Hasta", flotante=True,
                             on_change=lambda _v: pintar())

        # Estado: el dato ya se pinta con ícono por renglón, pero encontrar los
        # fallidos entre cientos de correctos obligaba a barrer la lista a ojo.
        _ESTADOS = {"Todos": None, "Correctos": True, "Con problema": False}
        _blq_estado, f_estado = campo_opciones(
            "Estado", list(_ESTADOS), valor="Todos", flotante=True,
            editable=False, on_change=lambda _v: pintar())

        # Equipo: es la pregunta de fondo de la bitácora («¿desde qué máquinas se
        # está usando la herramienta?»). Las opciones salen de lo REGISTRADO, no
        # de un catálogo: un equipo que nunca movió nada no tiene por qué ocupar
        # un renglón del filtro.
        _TODOS_EQUIPOS = "Todos"
        _blq_equipo, f_equipo = campo_opciones(
            "Equipo", [_TODOS_EQUIPOS] + db.equipos_registrados(),
            valor=_TODOS_EQUIPOS, flotante=True,
            on_change=lambda _v: pintar())

        def _limpiar_filtros(_e=None) -> None:
            f_desde.value = f_hasta.value = ""
            f_estado.value = "Todos"
            f_equipo.value = _TODOS_EQUIPOS
            pintar()

        def _periodo() -> "tuple[str | None, str | None, str]":
            """(desde, hasta) en 'AAAA-MM-DD' + aviso si el rango está al revés."""
            d, h = parse_fecha(f_desde.value), parse_fecha(f_hasta.value)
            if d and h and d > h:
                # Se respeta lo tecleado y solo se avisa: invertirlo en silencio
                # haría creer que el filtro dice algo que no dice.
                return None, None, "La fecha «Desde» es posterior a «Hasta»."
            return (d.strftime("%Y-%m-%d") if d else None,
                    h.strftime("%Y-%m-%d") if h else None, "")

        def pintar(_e=None) -> None:
            desde, hasta, aviso = _periodo()
            if aviso:
                lista.controls = [ft.Container(ft.Text(aviso, size=12, color=NARANJA),
                                               padding=12)]
                estado.value = ""
                modal.refrescar()
                return
            equipo = (f_equipo.value or _TODOS_EQUIPOS)
            equipo = None if equipo == _TODOS_EQUIPOS else equipo
            # Los conteos de las tres pestañas se recalculan SIEMPRE, se esté en
            # la que se esté: si solo se actualizara la activa, las otras dos
            # seguirían anunciando el total sin filtrar.
            tabs.set_conteo("sesiones",
                            db.resumen_sesiones_uso(desde, hasta, equipo)["sesiones"])
            if tabs.activa == "sesiones":
                _pintar_sesiones(desde, hasta, equipo)
                return
            tipo = (db.MOV_ALTA if tabs.activa == "altas"
                    else db.MOV_MODIFICACION)
            solo = _ESTADOS.get(f_estado.value or "Todos")
            movs = db.listar_movimientos(tipo, tf.value or "", desde, hasta, solo,
                                         LIMITE, equipo)
            # Los conteos de las pestañas siguen a los filtros: si no, anunciarían
            # más movimientos de los que la lista puede mostrar. El resumen ya trae
            # total y exitosos por tipo, así que el estado se resuelve restando.
            por_tipo = db.resumen_movimientos(desde, hasta, equipo)

            def _cuantos(clave: str) -> int:
                r = por_tipo.get(clave, {})
                total, ok = r.get("total", 0), r.get("exitosos", 0)
                return total if solo is None else (ok if solo else total - ok)

            tabs.set_conteo("altas", _cuantos(db.MOV_ALTA))
            tabs.set_conteo("modificaciones", _cuantos(db.MOV_MODIFICACION))
            lista.controls = [_fila_movimiento(m) for m in movs] or [
                ft.Container(ft.Text("Sin movimientos con ese criterio.", size=12,
                                     color=GRIS), padding=12)]
            hechos = sum(1 for m in movs if m["exito"])
            if solo is None:
                estado.value = (f"{len(movs)} movimiento(s) · {hechos} correcto(s) · "
                                f"{len(movs) - hechos} con problema")
            else:
                # Con el estado ya filtrado, el desglose sobra: repetiría el filtro.
                estado.value = (f"{len(movs)} " + ("correcto(s)" if solo
                                                   else "con problema"))
            if len(movs) >= LIMITE:
                # Sin esto, un periodo amplio se vería completo estando recortado.
                estado.value += (f" · se muestran los {LIMITE} más recientes; "
                                 f"acota el periodo para ver el resto")
            modal.refrescar()

        def _duracion(minutos: int) -> str:
            """«2 h 15 min». En minutos sueltos, una jornada («487 min») obliga a
            hacer la división mentalmente para saber si es mucho o poco."""
            if minutos < 60:
                return f"{minutos} min"
            horas, resto = divmod(minutos, 60)
            return f"{horas} h" + (f" {resto} min" if resto else "")

        def _pintar_sesiones(desde, hasta, equipo) -> None:
            """Pestaña de uso: una línea por vez que se abrió la herramienta."""
            sesiones = db.listar_sesiones_uso(desde, hasta, equipo,
                                              tf.value or "", LIMITE)
            res = db.resumen_sesiones_uso(desde, hasta, equipo)
            lista.controls = [_fila_sesion(x) for x in sesiones] or [
                ft.Container(ft.Text("Sin sesiones con ese criterio.", size=12,
                                     color=GRIS), padding=12)]
            # Equipos y tiempo total van juntos porque es el par que se lee para
            # juzgar adopción: cuántas máquinas y cuánto se trabajó en ellas.
            estado.value = (f"{res['sesiones']} sesión(es) · "
                            f"{res['equipos']} equipo(s) · "
                            f"{_duracion(res['minutos'])} en total")
            if len(sesiones) >= LIMITE:
                estado.value += (f" · se listan las {LIMITE} más recientes; "
                                 f"acota el periodo para ver el resto")
            modal.refrescar()

        def _fila_sesion(x: dict) -> ft.Control:
            quien = " · ".join(p for p in (
                x.get("equipo") or "(equipo sin registrar)",
                x.get("usuario_sipp") or x.get("usuario_windows") or "") if p)
            detalle = [
                ft.Row([ft.Text(quien, size=12, weight=ft.FontWeight.W_500,
                                color=ft.Colors.ON_SURFACE, expand=True),
                        ft.Text(_fecha_corta(x.get("inicio")), size=11, color=GRIS)],
                       spacing=8),
                ft.Text(f"Abierta {_duracion(x.get('minutos') or 0)}"
                        + (f"  ·  versión {x['version_app']}"
                           if x.get("version_app") else ""),
                        size=11, color=GRIS)]
            return ft.Container(
                ft.Row([ft.Icon(ft.Icons.LAPTOP_CHROMEBOOK, size=16,
                                color=ft.Colors.PRIMARY),
                        ft.Column(detalle, spacing=2, tight=True, expand=True)],
                       spacing=8, vertical_alignment=ft.CrossAxisAlignment.START),
                padding=ft.Padding.symmetric(horizontal=10, vertical=8),
                bgcolor=ft.Colors.SURFACE_CONTAINER_LOWEST,
                border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
                border_radius=8)

        def _fila_movimiento(m: dict) -> ft.Control:
            ok = m["exito"]
            titulo = m.get("insumo") or "(sin insumo)"
            if m.get("etiqueta"):
                titulo += f"  ·  {m['etiqueta']}"
            ubic = " · ".join(p for p in (m.get("empresa"), m.get("sucursal")) if p)
            # Origen: equipo y quién operó. Va en su propio renglón y no mezclado
            # con la ubicación del activo, porque responde otra pregunta («quién
            # lo hizo») y confundir ambas al leer sería fácil.
            origen = " · ".join(p for p in (
                m.get("equipo") or "(equipo sin registrar)",
                m.get("usuario_sipp") or m.get("usuario_windows") or "") if p)
            detalle = ft.Column(
                [ft.Row([ft.Text(titulo, size=12, weight=ft.FontWeight.W_500,
                                 color=ft.Colors.ON_SURFACE, expand=True),
                         # La fecha a la derecha: es lo que se escanea al buscar
                         # "lo de ayer" sin leer cada renglón.
                         ft.Text(_fecha_corta(m.get("fecha")), size=11, color=GRIS)],
                        spacing=8),
                 ft.Text(" · ".join(p for p in (ubic, m.get("observacion") or "") if p),
                         size=11, color=GRIS, no_wrap=False),
                 ft.Row([ft.Icon(ft.Icons.COMPUTER, size=12,
                                 color=ft.Colors.ON_SURFACE_VARIANT),
                         ft.Text(origen, size=11,
                                 color=ft.Colors.ON_SURFACE_VARIANT,
                                 no_wrap=False, expand=True)],
                        spacing=4,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER)],
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

        def _cambiar_pestana(clave: str) -> None:
            """Los filtros que no aplican se OCULTAN, no se dejan inertes.

            En «Sesiones» no hay correcto/con problema que filtrar, y un
            desplegable que no hace nada al moverlo se lee como un defecto. El
            buscador sí sirve, pero busca otra cosa, así que cambia de rótulo."""
            es_uso = clave == "sesiones"
            caja_estado.visible = not es_uso
            tf.hint_text = ("Buscar por equipo o usuario…" if es_uso
                            else "Buscar por etiqueta, insumo o serie…")
            pintar()

        tabs = Pestanas(
            [("altas", "Altas", ft.Icons.ADD_CIRCLE_OUTLINE),
             ("modificaciones", "Modificaciones", ft.Icons.EDIT_OUTLINED),
             ("sesiones", "Sesiones de uso", ft.Icons.LAPTOP_CHROMEBOOK)],
            al_cambiar=_cambiar_pestana)
        tf.on_change = lambda _e: pintar()

        # El contenedor se guarda aparte porque es LO QUE SE OCULTA en la pestaña
        # de sesiones: esconder solo el desplegable dejaría su hueco reservado por
        # el `expand` del contenedor, y los otros dos filtros no se recorrerían.
        caja_estado = ft.Container(_blq_estado, expand=True)
        filtros = ft.Row(
            [ft.Container(f_desde.control, expand=True),
             ft.Container(f_hasta.control, expand=True),
             caja_estado,
             boton_herramienta("Limpiar", ft.Icons.FILTER_ALT_OFF_OUTLINED,
                               _limpiar_filtros,
                               tooltip="Quitar los filtros de fecha, estado y equipo")],
            spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)

        modal = Modal(self.page, "Bitácora de la herramienta", ancho=840,
                      alto_cuerpo=520)
        modal.cuerpo.controls = [
            tabs.control,
            ft.Row([tf, ft.Container(_blq_equipo, width=240)], spacing=8,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            filtros, estado, ft.Container(lista, height=320)]
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

    async def _elegir_insumos_faltantes(self, faltantes: list) -> list:
        """Pide al usuario el insumo de los activos que el catálogo no resolvió.

        `faltantes`: [(registro, fila_del_reporte)]. Devuelve
        [(registro, fila, id_insumo)] SOLO de los que se eligieron: lo que no se
        elija queda pendiente, que es preferible a registrarlo con un insumo
        aproximado.

        Se llama al FINAL del lote, no al tropezar con cada uno: así el usuario
        resuelve todo de una vez en lugar de vigilar el proceso.
        """
        from core.insumos import elegir_mas_general
        from ui.selector_insumo import DialogoSelectorInsumo

        decision: asyncio.Future = asyncio.get_running_loop().create_future()
        elegidos: dict = {}      # id_registro -> (id_insumo, nombre)
        textos: dict = {}        # id_registro -> ft.Text con la elección
        en_curso: dict = {"id": None}

        def _al_elegir(id_insumo, nombre: str) -> None:
            rid = en_curso.get("id")
            if rid is None:
                return
            elegidos[rid] = (str(id_insumo), nombre)
            textos[rid].value = f"{nombre}  ·  clave {id_insumo}"
            textos[rid].color = VERDE
            btn_alta.content = f"Dar de alta ({len(elegidos)})"
            btn_alta.disabled = not elegidos
            modal.refrescar()

        selector = DialogoSelectorInsumo(self.app, al_elegir=_al_elegir)

        def responder(valor) -> None:
            if decision.done():
                return
            decision.set_result(valor)
            modal.cerrar()

        def abridor(registro, nombre_sugerido):
            """Manejador del botón de una fila: marca cuál se está eligiendo y abre
            el selector. El selector es UNO solo, así que hay que recordar a qué
            activo pertenece la elección que devuelva."""
            def _abrir(_e=None) -> None:
                en_curso["id"] = registro.id
                selector.abrir(sugerido=nombre_sugerido)
            return _abrir

        lista = ft.ListView(spacing=6, expand=True)
        for r, _fila in faltantes:
            nombre = (r.datos().get("nb_NombreInsumo") or r.nombre_insumo or "").strip()
            sugerido = elegir_mas_general(
                nombre, ID_POR_EMPRESA.get((r.empresa or "").strip()))
            txt = ft.Text("Sin elegir", size=11, color=NARANJA, no_wrap=False,
                          expand=True)
            textos[r.id] = txt
            pista = (f"El más parecido del catálogo: «{sugerido.nombre}»"
                     if sugerido else "El catálogo no tiene nada parecido.")
            lista.controls.append(ft.Container(
                ft.Row([
                    ft.Column(
                        [ft.Text(f"{nombre}"
                                 + (f"  ·  serie {r.no_serie}" if r.no_serie else ""),
                                 size=12, weight=ft.FontWeight.W_500,
                                 color=ft.Colors.ON_SURFACE, no_wrap=False),
                         ft.Text(pista, size=11, color=GRIS, no_wrap=False),
                         txt],
                        spacing=1, tight=True, expand=True),
                    boton_secundario("Elegir insumo", ft.Icons.SEARCH,
                                     abridor(r, nombre)),
                ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                padding=ft.Padding.symmetric(horizontal=10, vertical=8),
                bgcolor=ft.Colors.SURFACE_CONTAINER_LOWEST,
                border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
                border_radius=8))

        btn_alta = boton_primario(
            "Dar de alta (0)", ft.Icons.PLAYLIST_ADD_CHECK,
            lambda _e: responder([(r, f, elegidos[r.id][0])
                                  for r, f in faltantes if r.id in elegidos]))
        btn_alta.disabled = True

        modal = Modal(self.page, "Elige el insumo de estos activos", ancho=760,
                      subtitulo=f"{len(faltantes)} sin coincidencia exacta",
                      al_cerrar=lambda: responder([]))
        modal.cuerpo.controls = [
            ft.Text("El catálogo del SIPP no tiene un insumo con ese nombre exacto, "
                    "solo variantes con marca o capacidad. La herramienta NO elige "
                    "por ti: indica cuál corresponde y se darán de alta con esa "
                    "clave, en el navegador que sigue abierto.",
                    size=12, color=ft.Colors.ON_SURFACE, no_wrap=False),
            ft.Text("Lo que dejes sin elegir queda pendiente para otra corrida.",
                    size=11, color=GRIS, no_wrap=False),
            ft.Container(lista, height=320)]
        modal.set_acciones([
            boton_herramienta("Ahora no", on_click=lambda _e: responder([])),
            btn_alta,
        ])
        modal.abrir()

        seleccion = await decision
        # La elección se guarda en la ficha: la próxima corrida ya no pregunta.
        for r, _f, id_insumo in seleccion:
            datos = dict(r.datos())
            datos["id_InsumoOrigen"] = id_insumo
            nombre_cat = elegidos.get(r.id, ("", ""))[1]
            if nombre_cat:
                datos["nb_NombreInsumo"] = nombre_cat
            db.actualizar_datos_levantamiento(r.id, datos=datos)
        return seleccion

    @staticmethod
    def _faltantes(r: "db.Levantamiento") -> list:
        """Campos obligatorios del SIPP que le faltan a un registro."""
        return faltantes_obligatorios(
            r.id_tipo_activo, r.datos(), r.nombre_insumo or "", r.responsable or "",
            r.empresa or "", r.sucursal or "")

    async def _validar_obligatorios(self, pendientes: list) -> list:
        """Aparta los registros incompletos ANTES de tocar el portal.

        Devuelve los que sí se pueden enviar (lista vacía = no continuar). El SIPP
        exige tipo de activo, insumo y empleado de resguardo: mandar un activo sin
        ellos deja el alta a medias en el portal, y eso cuesta más de arreglar que
        de prevenir.
        """
        incompletos = [(r, self._faltantes(r)) for r in pendientes]
        incompletos = [(r, f) for r, f in incompletos if f]
        if not incompletos:
            return pendientes
        ids_malos = {r.id for r, _f in incompletos}
        completos = [r for r in pendientes if r.id not in ids_malos]

        decision: asyncio.Future = asyncio.get_running_loop().create_future()

        def responder(valor) -> None:
            if decision.done():
                return
            decision.set_result(valor)
            modal.cerrar()

        lista = ft.ListView(spacing=4, expand=True)
        for r, faltan in incompletos:
            lista.controls.append(ft.Row(
                [ft.Icon(ft.Icons.ERROR_OUTLINE, size=16, color=NARANJA),
                 ft.Column(
                     [ft.Text(f"{r.nombre_insumo}"
                              + (f"  ·  {r.etiqueta}" if r.etiqueta else ""),
                              size=12, weight=ft.FontWeight.W_500,
                              color=ft.Colors.ON_SURFACE, no_wrap=False),
                      ft.Text("Falta: " + ", ".join(faltan), size=11, color=GRIS,
                              no_wrap=False)],
                     spacing=0, tight=True, expand=True)],
                spacing=8, vertical_alignment=ft.CrossAxisAlignment.START))

        acciones = [boton_herramienta("Cancelar", on_click=lambda _e: responder([]))]
        if completos:
            acciones.append(boton_primario(
                f"Continuar con los {len(completos)} completos", ft.Icons.CHECK,
                lambda _e: responder(completos)))
        modal = Modal(self.page, "Faltan datos obligatorios del SIPP", ancho=680,
                      subtitulo=f"{len(incompletos)} activo(s) sin completar",
                      al_cerrar=lambda: responder([]))
        modal.cuerpo.controls = [
            ft.Text("Estos activos no se pueden registrar: el formulario del SIPP "
                    "exige esos campos. Captúralos con el botón de la ficha (📋) en "
                    "cada fila y vuelve a intentar.",
                    size=12, color=ft.Colors.ON_SURFACE, no_wrap=False),
            ft.Container(lista, height=300)]
        modal.set_acciones(acciones)
        modal.abrir()
        return await decision

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
            # Salida temprana OBLIGATORIA: `modal.cerrar()` dispara `al_cerrar`,
            # que vuelve a entrar aquí. Sin esto se recursan mutuamente hasta
            # reventar (RecursionError con solo cerrar el diálogo).
            if decision.done():
                return
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
                _tipo, campos, detalles, insumo_id = self._payload_modificacion(r)
                try:
                    resultado = await sipp.modificar_activo(
                        r.etiqueta, r.no_serie, campos, detalles,
                        punto_control=ctrl.punto_control, insumo_id=insumo_id)
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
