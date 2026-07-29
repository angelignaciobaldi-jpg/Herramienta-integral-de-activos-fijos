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

from core import archivos, credenciales, db
from core.proveedor_activos import proveedor_por_defecto
from core.rpa_sipp import BucleRpa, ControlRpa, ErrorSipp, RpaDetenido, SesionSipp
from core.tipos_activo import ID_POR_NOMBRE, TIPOS_ACTIVO, campos_de_tipo, nombre_tipo
from ui.captura_activo import DialogoCapturaActivo
from ui.carga_masiva import DialogoCargaMasiva
from ui.comun import GRIS, NARANJA, NOMBRES_EMPRESAS, ROJO, VERDE
from ui.componentes import (GUTTER_SCROLL, Pestanas, boton_herramienta,
                            boton_primario, boton_secundario, buscador,
                            campo_opciones, campo_tabla_opciones,
                            campo_tabla_texto, campo_texto, tarjeta_seccion)
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


class SeccionRegistroActivos:
    """Levantamiento: carga de imágenes, tabla, búsqueda y categorización."""

    def __init__(self, app):
        self.app = app
        self.page = app.page
        self.proveedor = proveedor_por_defecto()
        self._tab = _TAB_TODOS
        self._seleccionados: set[int] = set()
        # Paginación: un inventario completo son miles de activos y cada fila
        # lleva controles editables; pintarlos todos vuelve la pantalla inusable.
        self._pagina = 0
        self._por_pagina = _POR_PAGINA[0]
        # Formulario dinámico de captura por tipo de activo (prepara el alta en SIPP).
        self.dialogo_captura = DialogoCapturaActivo(app, al_guardar=self._refrescar)
        # Carga masiva desde Excel: toma el contexto de los selectores de arriba.
        self.dialogo_carga = DialogoCargaMasiva(
            app, contexto=self._contexto_actual, al_terminar=self._refrescar)
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
                boton_secundario("Actualizar catálogos (SIPP)",
                                 ft.Icons.CLOUD_DOWNLOAD, self._actualizar_insumos,
                                 tooltip="Descarga del SIPP los catálogos de insumos "
                                         "(de la empresa de arriba) y de empleados"),
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
                (db.EST_NO_DADO_ALTA, "No dados de alta", ft.Icons.CANCEL),
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
            ColumnaTabla("Acciones", 10, ancho_min_px=130),
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
        self._refrescar()

    def _estatus_tab(self) -> "str | None":
        """Estatus por el que filtra la pestaña activa (None = todas)."""
        return None if self._tab == _TAB_TODOS else self._tab

    def _ids_actuales(self) -> list[int]:
        """Ids de TODO lo que cumple pestaña + filtro (sin traer las filas)."""
        return db.ids_levantamiento(self._estatus_tab(), self._filtro)

    def _aplicar_filtro(self, _e=None) -> None:
        self._filtro = (self.tf_buscar.value or "").strip().lower()
        self._btn_limpiar.visible = bool(self._filtro)
        self._pagina = 0
        self._refrescar()

    def _limpiar_filtro(self, _e=None) -> None:
        self.tf_buscar.value = ""
        self._aplicar_filtro()

    def _refrescar(self) -> None:
        """Repinta SOLO la página actual, pidiéndosela ya recortada a SQLite."""
        estatus, filtro = self._estatus_tab(), self._filtro
        total = db.contar_levantamiento(estatus, filtro)
        # Ajusta la página si quedó fuera de rango (p. ej. tras filtrar o borrar).
        ultima = max(0, (total - 1) // self._por_pagina) if total else 0
        self._pagina = min(max(0, self._pagina), ultima)
        pagina = db.listar_levantamiento_pagina(
            estatus, filtro, self._por_pagina, self._pagina * self._por_pagina)
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
        emp = campo_tabla_opciones(
            NOMBRES_EMPRESAS, valor=r.empresa or None, ancho=_W,
            page=self.page, titulo="Elegir empresa",
            on_change=lambda e, i=r.id, a=_alta: self._set_ubic(
                i, empresa=e.control.value or "", ya_de_alta=a))
        suc = campo_tabla_texto(
            valor=r.sucursal or "", ancho=_W,
            on_blur=lambda e, i=r.id, a=_alta: self._set_ubic(
                i, sucursal=(e.control.value or "").strip(), ya_de_alta=a))
        dep = campo_tabla_texto(
            valor=r.departamento or "", ancho=_W,
            on_blur=lambda e, i=r.id, a=_alta: self._set_ubic(
                i, departamento=(e.control.value or "").strip(), ya_de_alta=a))
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
            r.etiqueta or "—",
            r.no_serie or "—",
            estatus,
            acciones,
        ])

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
        registros = db.listar_levantamiento()
        # Se busca por ETIQUETA (número de inventario) y, si el activo no la
        # tiene, por su número de serie. En los inventarios reales la mayoría de
        # los activos NO trae serie, así que la etiqueta es el identificador.
        series = sorted({r.identificador() for r in registros if r.identificador()})
        if not series:
            self.app.avisar("No hay etiquetas ni números de serie que buscar.", ROJO)
            return
        self._set_cargando(True, f"Buscando {len(series)} activo(s) en el SIPP…")
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
        # Aplica el resultado a cada registro (por su etiqueta o su serie).
        for r in registros:
            serie = r.identificador()
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
            actions=[boton_herramienta("Detener", on_click=lambda _e: ctrl.detener(),
                                       destructivo=True)],
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
            actions=[boton_herramienta("Detener", on_click=lambda _e: ctrl.detener(),
                                       destructivo=True)],
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
    async def _actualizar_insumos(self, _e=None) -> None:
        """Descarga del SIPP el catálogo de insumos de la empresa seleccionada
        arriba y lo cachea localmente (para el selector de insumo). Corre el RPA en
        un hilo, con progreso."""
        creds = credenciales.cargar()
        if not creds or not creds[0]:
            self.app.avisar("Configura primero las credenciales del SIPP (botón ⚙).", ROJO)
            return
        usuario, contrasena = creds
        empresa = self.dd_empresa.value
        if not empresa:
            self.app.avisar("Elige arriba la empresa cuyo catálogo quieres descargar.",
                            NARANJA)
            return

        bucle = BucleRpa()
        ui_loop = asyncio.get_running_loop()
        txt = ft.Text("Conectando al SIPP…", size=13)
        barra = ft.ProgressBar()
        dlg = ft.AlertDialog(
            modal=True, title=ft.Text("Actualizando catálogo de insumos"),
            content=ft.Container(
                ft.Column([txt, barra,
                           ft.Text("Se abrirá un navegador; no lo cierres.",
                                   size=11, color=GRIS)], tight=True, spacing=12),
                width=420))
        self.page.show_dialog(dlg)
        self.page.update()

        def _refrescar_dlg() -> None:
            try:
                dlg.update()
            except (RuntimeError, AssertionError):
                pass

        def avance(hechos: int, total: int) -> None:
            def aplicar() -> None:
                txt.value = f"Descargando insumos… {hechos}/{total or '?'}"
                barra.value = (hechos / total) if total else None
                _refrescar_dlg()
            ui_loop.call_soon_threadsafe(aplicar)

        def mensaje(texto: str) -> None:
            def aplicar() -> None:
                txt.value = texto
                barra.value = None
                _refrescar_dlg()
            ui_loop.call_soon_threadsafe(aplicar)

        resultado, error = {}, None

        async def flujo() -> None:
            nonlocal resultado, error
            from core import empleados, insumos
            from core.rpa_sipp import SesionSipp
            try:
                async with SesionSipp(headless=False) as sipp:
                    await sipp.login(usuario, contrasena)
                    await sipp.preparar_sesion_empresa(empresa)
                    resultado = await insumos.descargar_catalogo(
                        sipp, progreso=avance, solo_activo_fijo=True)
                    # El catálogo de empleados es global (una sola descarga).
                    mensaje("Descargando empleados…")
                    resultado["empleados"] = (
                        await empleados.descargar_catalogo(sipp)).get("guardados", 0)
            except Exception as exc:  # noqa: BLE001 — se reporta al usuario
                error = str(exc)

        try:
            await asyncio.wrap_future(bucle.enviar(flujo()))
        finally:
            bucle.cerrar()
            self.page.pop_dialog()

        if error:
            self.app.avisar(f"No se pudo actualizar el catálogo: {error}", ROJO,
                            duracion=9000)
        else:
            self.app.avisar(
                f"Catálogos actualizados: {resultado.get('guardados', 0)} insumo(s) "
                f"de «{resultado.get('empresa_nombre', empresa)}» y "
                f"{resultado.get('empleados', 0)} empleado(s).", VERDE, duracion=7000)

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
