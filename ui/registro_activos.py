"""Pantalla "Registro de activos".

Captura y administra activos fijos: alta/edición en un formulario modal, listado
en una tabla y persistencia en SQLite (core/db.py). Es la pantalla de referencia
del patrón modular: cada sección vive en su propio archivo, expone `.contenido`
(el control raíz que el shell muestra) y, si lo necesita, un `_on_resize`.

Los campos son una BASE; ajústalos junto con el esquema de core/db.py conforme el
área defina la información definitiva del activo.
"""

from __future__ import annotations

import flet as ft

from core import db
from ui.comun import GRIS, NOMBRES_EMPRESAS, ROJO_BOTON, VERDE, parse_monto

# Estatus posibles de un activo (ajústalos según el catálogo del área).
ESTATUS = ["Activo", "En reparación", "Baja", "Extraviado"]


class SeccionRegistroActivos:
    """Alta, edición y listado de activos fijos."""

    def __init__(self, app):
        self.app = app
        self.page = app.page
        self._editando: int | None = None  # id del activo en edición (None = alta)
        self._construir()

    # ------------------------------------------------------------ UI
    def _construir(self) -> None:
        self.lista = ft.Column(spacing=8, scroll=ft.ScrollMode.AUTO, expand=True)
        self.txt_vacio = ft.Container(
            content=ft.Column(
                [
                    ft.Icon(ft.Icons.INVENTORY_2, size=52, color=GRIS),
                    ft.Text("Aún no hay activos registrados",
                            size=16, color=GRIS, text_align=ft.TextAlign.CENTER),
                    ft.Text("Usa «Agregar activo» para capturar el primero.",
                            size=13, color=GRIS, text_align=ft.TextAlign.CENTER),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=8,
            ),
            alignment=ft.Alignment(0, 0), expand=True, visible=False,
        )

        encabezado = ft.Row(
            [
                ft.Text("Registro de activos", size=20, weight=ft.FontWeight.BOLD),
                ft.FilledButton("Agregar activo", icon=ft.Icons.ADD,
                                on_click=lambda _e: self._abrir_form()),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        self.contenido = ft.Column(
            [encabezado, ft.Divider(), ft.Stack([self.lista, self.txt_vacio], expand=True)],
            expand=True, spacing=12,
        )
        self._construir_form()

    def _construir_form(self) -> None:
        """Formulario modal de alta/edición (reutiliza los mismos campos)."""
        self.f_inventario = ft.TextField(label="Núm. de inventario *", dense=True)
        self.f_descripcion = ft.TextField(label="Descripción *", dense=True)
        self.f_empresa = ft.Dropdown(
            label="Empresa", dense=True,
            options=[ft.dropdown.Option(n) for n in NOMBRES_EMPRESAS])
        self.f_categoria = ft.TextField(label="Categoría", dense=True)
        self.f_ubicacion = ft.TextField(label="Ubicación", dense=True)
        self.f_marca = ft.TextField(label="Marca", dense=True)
        self.f_modelo = ft.TextField(label="Modelo", dense=True)
        self.f_serie = ft.TextField(label="Núm. de serie", dense=True)
        self.f_fecha = ft.TextField(label="Fecha de adquisición", dense=True,
                                    hint_text="DD/MM/AAAA")
        self.f_valor = ft.TextField(label="Valor de adquisición", dense=True,
                                    hint_text="0.00")
        self.f_estatus = ft.Dropdown(
            label="Estatus", dense=True, value=ESTATUS[0],
            options=[ft.dropdown.Option(e) for e in ESTATUS])
        self._titulo_form = ft.Text("Nuevo activo", size=20, weight=ft.FontWeight.BOLD)

        campos = ft.Column(
            [
                self.f_inventario, self.f_descripcion, self.f_empresa,
                self.f_categoria, self.f_ubicacion,
                ft.Row([self.f_marca, self.f_modelo], spacing=10),
                self.f_serie,
                ft.Row([self.f_fecha, self.f_valor], spacing=10),
                self.f_estatus,
            ],
            spacing=10, scroll=ft.ScrollMode.AUTO, tight=True, width=460,
        )
        self.form = ft.AlertDialog(
            modal=True, title=self._titulo_form,
            content=ft.Container(campos, height=440, width=460),
            actions=[
                ft.TextButton("Cancelar", on_click=lambda _e: self.page.pop_dialog()),
                ft.FilledButton("Guardar", icon=ft.Icons.SAVE, on_click=self._guardar),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )

    # --------------------------------------------------------- datos
    def cargar_desde_db(self) -> None:
        """Lee los activos guardados y pinta la lista. Lo invoca el shell al
        arrancar y esta pantalla tras cada alta/edición/baja."""
        activos = db.listar()
        self.lista.controls = [self._fila(a) for a in activos]
        self.txt_vacio.visible = not activos
        self.lista.visible = bool(activos)
        self._safe_update()

    def _fila(self, activo: "db.Activo") -> ft.Control:
        subtitulo = " · ".join(
            p for p in (activo.empresa, activo.categoria, activo.ubicacion) if p)
        return ft.Card(
            content=ft.Container(
                content=ft.Row(
                    [
                        ft.Column(
                            [
                                ft.Text(f"{activo.num_inventario} — {activo.descripcion}",
                                        weight=ft.FontWeight.BOLD, size=14),
                                ft.Text(subtitulo or "Sin clasificación",
                                        size=12, color=GRIS),
                            ],
                            spacing=2, expand=True,
                        ),
                        ft.Container(
                            content=ft.Text(activo.estatus or "—", size=11,
                                            weight=ft.FontWeight.BOLD),
                            padding=ft.Padding.symmetric(horizontal=10, vertical=4),
                            border_radius=12,
                            bgcolor=ft.Colors.SECONDARY_CONTAINER,
                        ),
                        ft.IconButton(
                            icon=ft.Icons.EDIT, tooltip="Editar",
                            on_click=lambda _e, a=activo: self._abrir_form(a)),
                        ft.IconButton(
                            icon=ft.Icons.DELETE_OUTLINE, tooltip="Eliminar",
                            icon_color=ROJO_BOTON,
                            on_click=lambda _e, a=activo: self._confirmar_baja(a)),
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=8,
                ),
                padding=ft.Padding.symmetric(horizontal=14, vertical=10),
            )
        )

    def _abrir_form(self, activo: "db.Activo | None" = None) -> None:
        """Abre el formulario en modo alta (activo=None) o edición."""
        self._editando = activo.id if activo else None
        self._titulo_form.value = "Editar activo" if activo else "Nuevo activo"
        self.f_inventario.value = activo.num_inventario if activo else ""
        self.f_descripcion.value = activo.descripcion if activo else ""
        self.f_empresa.value = activo.empresa if activo else None
        self.f_categoria.value = activo.categoria if activo else ""
        self.f_ubicacion.value = activo.ubicacion if activo else ""
        self.f_marca.value = activo.marca if activo else ""
        self.f_modelo.value = activo.modelo if activo else ""
        self.f_serie.value = activo.num_serie if activo else ""
        self.f_fecha.value = activo.fecha_adquisicion if activo else ""
        self.f_valor.value = (
            "" if not activo or activo.valor_adquisicion is None
            else f"{activo.valor_adquisicion:.2f}")
        self.f_estatus.value = (activo.estatus if activo else ESTATUS[0]) or ESTATUS[0]
        self.page.show_dialog(self.form)

    def _guardar(self, _e=None) -> None:
        inventario = (self.f_inventario.value or "").strip()
        descripcion = (self.f_descripcion.value or "").strip()
        if not inventario or not descripcion:
            self.app.avisar("El número de inventario y la descripción son obligatorios.",
                            ft.Colors.RED_700)
            return
        try:
            valor = parse_monto(self.f_valor.value)
        except ValueError as exc:
            self.app.avisar(str(exc), ft.Colors.RED_700)
            return
        campos = dict(
            num_inventario=inventario, descripcion=descripcion,
            empresa=self.f_empresa.value or "",
            categoria=(self.f_categoria.value or "").strip(),
            ubicacion=(self.f_ubicacion.value or "").strip(),
            marca=(self.f_marca.value or "").strip(),
            modelo=(self.f_modelo.value or "").strip(),
            num_serie=(self.f_serie.value or "").strip(),
            fecha_adquisicion=(self.f_fecha.value or "").strip(),
            valor_adquisicion=valor,
            estatus=self.f_estatus.value or ESTATUS[0],
            ruta_documento=None,
        )
        try:
            if self._editando is None:
                db.guardar(**campos)
            else:
                db.actualizar(self._editando, **campos)
        except db.InventarioDuplicado:
            self.app.avisar(
                f"Ya existe un activo con el inventario «{inventario}».",
                ft.Colors.RED_700)
            return
        self.page.pop_dialog()
        self.cargar_desde_db()
        self.app.avisar("Activo guardado.", VERDE)

    def _confirmar_baja(self, activo: "db.Activo") -> None:
        def eliminar(_e=None) -> None:
            db.eliminar(activo.id)
            self.page.pop_dialog()
            self.cargar_desde_db()
            self.app.avisar("Activo eliminado.", VERDE)

        self.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("Eliminar activo"),
                content=ft.Text(
                    f"¿Eliminar «{activo.num_inventario} — {activo.descripcion}»? "
                    "Esta acción no se puede deshacer."),
                actions=[
                    ft.TextButton("Cancelar", on_click=lambda _e: self.page.pop_dialog()),
                    ft.FilledButton("Eliminar", icon=ft.Icons.DELETE,
                                    on_click=eliminar),
                ],
                actions_alignment=ft.MainAxisAlignment.END,
            )
        )

    # --------------------------------------------------------- utilidades
    def _on_resize(self, _e=None) -> None:
        """La lista es fluida; no requiere recomputar anchos. Presente por
        consistencia con el registro de listeners del shell."""

    def _safe_update(self) -> None:
        try:
            self.contenido.update()
        except (RuntimeError, AssertionError, AttributeError):
            pass  # aún no montado; se reflejará al renderizar
