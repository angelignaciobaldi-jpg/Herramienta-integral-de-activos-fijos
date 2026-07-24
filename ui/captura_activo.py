"""Diálogo de captura de los datos del alta de un activo (formulario DINÁMICO).

Al elegir el **tipo de activo**, arma el formulario con los campos que define
`core/tipos_activo.py`: los CAMPOS_COMUNES del alta del SIPP más los campos
particulares del tipo (CAMPOS_POR_TIPO). Los campos marcados con `detalle=True`
son las CARACTERÍSTICAS dinámicas del insumo ("Detalles Insumo" / camposDetalle
en el SIPP) y se agrupan aparte.

Lo capturado se guarda en `levantamiento.datos_json` (junto con el tipo elegido)
y es exactamente lo que consumirá el RPA de alta para llenar el formulario del
portal, emparejando cada campo por su `ng_model` (o por su rótulo, si es de
detalle).

Uso:
    self.dialogo = DialogoCapturaActivo(app, al_guardar=self._refrescar)
    self.dialogo.abrir(registro)   # registro: db.Levantamiento
"""

from __future__ import annotations

import flet as ft

from core import db
from core.tipos_activo import ID_POR_NOMBRE, TIPOS_ACTIVO, campos_de_tipo, nombre_tipo
from ui.comun import GRIS, NOMBRES_EMPRESAS, ROJO, VERDE

_ANCHO = 620
_ALTO_CAMPOS = 420


class DialogoCapturaActivo:
    """Formulario dinámico por tipo de activo para preparar el alta en el SIPP."""

    def __init__(self, app, al_guardar=None):
        self.app = app
        self.page = app.page
        self.al_guardar = al_guardar          # callback tras guardar (p. ej. refrescar)
        self._registro: "db.Levantamiento | None" = None
        self._controles: dict[str, tuple] = {}  # clave -> (CampoActivo, control)
        self._construir()

    # ------------------------------------------------------------ UI
    def _construir(self) -> None:
        # OJO: DropdownM2 muestra el `key` de la opción, no el `text`. Por eso el
        # key es el NOMBRE del tipo (lo que ve el usuario y lo que el RPA busca en
        # el combo del SIPP); el id se resuelve con ID_POR_NOMBRE al guardar.
        self.dd_tipo = ft.DropdownM2(
            label="Tipo de activo *", dense=True, width=_ANCHO - 40,
            options=[ft.dropdownm2.Option(key=n, text=n)
                     for n in TIPOS_ACTIVO.values()],
            on_change=self._cambiar_tipo)
        self._subtitulo = ft.Text("", size=12, color=GRIS)
        self._area_campos = ft.Column(
            spacing=12, scroll=ft.ScrollMode.AUTO, tight=True)
        self._titulo = ft.Text("Capturar datos del activo", size=20,
                               weight=ft.FontWeight.BOLD)

        self.dialogo = ft.AlertDialog(
            modal=True,
            title=ft.Column([self._titulo, self._subtitulo], spacing=2, tight=True),
            content=ft.Container(
                ft.Column(
                    [self.dd_tipo, ft.Divider(),
                     ft.Container(self._area_campos, height=_ALTO_CAMPOS)],
                    spacing=10, tight=True),
                width=_ANCHO),
            actions=[
                ft.TextButton("Cancelar", on_click=lambda _e: self.page.pop_dialog()),
                ft.FilledButton("Guardar", icon=ft.Icons.SAVE, on_click=self._guardar),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )

    # ------------------------------------------------------- apertura
    def abrir(self, registro: "db.Levantamiento") -> None:
        """Abre el formulario para `registro`, precargando lo ya capturado."""
        self._registro = registro
        self._subtitulo.value = (
            f"{registro.nombre_insumo} · Serie: {registro.no_serie or '—'}")
        self.dd_tipo.value = nombre_tipo(registro.id_tipo_activo) or None
        self._render_campos()
        self.page.show_dialog(self.dialogo)

    def _cambiar_tipo(self, _e=None) -> None:
        self._render_campos()
        self._safe_update()

    # -------------------------------------------------- render dinámico
    def _tipo_actual(self) -> "int | None":
        """id del tipo seleccionado (el combo maneja NOMBRES; se traduce a id)."""
        return ID_POR_NOMBRE.get(self.dd_tipo.value) if self.dd_tipo.value else None

    def _render_campos(self) -> None:
        """Arma los campos del tipo elegido, agrupados, precargando valores."""
        tipo = self._tipo_actual()
        datos = self._registro.datos() if self._registro else {}
        self._controles = {}

        if tipo is None:
            self._area_campos.controls = [
                ft.Container(
                    ft.Text("Elige un tipo de activo para ver los campos requeridos.",
                            size=13, color=GRIS, text_align=ft.TextAlign.CENTER),
                    alignment=ft.Alignment(0, 0), padding=30)
            ]
            return

        # Agrupa conservando el orden de aparición de cada grupo. Se omite
        # id_TipoActivo: ya lo controla el selector de arriba del diálogo.
        grupos: dict[str, list] = {}
        for campo in campos_de_tipo(tipo):
            if campo.clave == "id_TipoActivo":
                continue
            grupos.setdefault(campo.grupo, []).append(campo)

        secciones = []
        for grupo, campos in grupos.items():
            filas = []
            for campo in campos:
                ctrl = self._control_para(campo, self._valor_inicial(campo, datos))
                self._controles[campo.clave] = (campo, ctrl)
                filas.append(ctrl)
            secciones.append(
                ft.Column(
                    [ft.Text(grupo, size=13, weight=ft.FontWeight.BOLD,
                             color=ft.Colors.PRIMARY),
                     *filas],
                    spacing=8, tight=True))
        self._area_campos.controls = secciones

    def _valor_inicial(self, campo, datos: dict) -> str:
        """Valor con el que se precarga un campo: lo ya capturado si existe; si no,
        lo que YA sabemos del registro del levantamiento (serie, insumo, empresa,
        sucursal y departamento), para no recapturarlo a mano."""
        if datos.get(campo.clave):
            return str(datos[campo.clave])
        r = self._registro
        if r is None:
            return ""
        clave = campo.clave.lower()
        if campo.clave == "nu_Serie":
            return r.no_serie or ""
        if campo.clave == "nb_NombreInsumo":
            return r.nombre_insumo or ""
        if "empresa" in clave:
            return r.empresa or ""
        if "sucursal" in clave:
            return r.sucursal or ""
        if "departamento" in clave:
            return r.departamento or ""
        return ""

    def _control_para(self, campo, valor) -> ft.Control:
        """Crea el control adecuado al tipo de campo, precargado con `valor`."""
        etiqueta = campo.etiqueta + (" *" if campo.requerido else "")
        valor = "" if valor is None else str(valor)

        if campo.control == "select":
            opciones = None
            if campo.opciones:
                # El key es la ETIQUETA (DropdownM2 muestra el key) y además es el
                # texto que el RPA buscará en el combo equivalente del SIPP.
                opciones = [ft.dropdownm2.Option(key=str(v), text=str(v))
                            for v in campo.opciones.values()]
            elif "empresa" in campo.clave.lower():
                # Catálogo local del Grupo Petroil para los campos de empresa.
                opciones = [ft.dropdownm2.Option(key=n, text=n) for n in NOMBRES_EMPRESAS]
            if opciones is not None:
                return ft.DropdownM2(
                    label=etiqueta, value=valor or None, options=opciones,
                    dense=True, data=campo.clave)
            # Catálogo que vive en el SIPP (sucursal, centro de costo, insumo…):
            # se captura como texto y el RPA lo buscará por su nombre.
            return ft.TextField(label=etiqueta, value=valor, dense=True,
                                hint_text="Catálogo del SIPP (se busca por nombre)",
                                data=campo.clave)
        if campo.control == "date":
            return ft.TextField(label=etiqueta, value=valor, dense=True,
                                hint_text="DD/MM/AAAA", data=campo.clave)
        if campo.control == "number":
            return ft.TextField(label=etiqueta, value=valor, dense=True,
                                hint_text="0.00", data=campo.clave)
        return ft.TextField(label=etiqueta, value=valor, dense=True, data=campo.clave)

    # ---------------------------------------------------------- guardar
    def _guardar(self, _e=None) -> None:
        if self._registro is None:
            return
        tipo = self._tipo_actual()
        if tipo is None:
            self.app.avisar("Elige el tipo de activo.", ROJO)
            return
        valores, faltantes = {}, []
        for clave, (campo, ctrl) in self._controles.items():
            valor = (getattr(ctrl, "value", "") or "").strip()
            if campo.requerido and not valor:
                faltantes.append(campo.etiqueta)
            valores[clave] = valor
        if faltantes:
            self.app.avisar(
                "Faltan campos obligatorios: " + ", ".join(faltantes[:5])
                + ("…" if len(faltantes) > 5 else ""), ROJO)
            return
        # Si el activo YA está dado de alta en el SIPP, editar sus datos lo deja
        # marcado como "modificado": así el RPA de modificación sabe cuáles
        # reenviar al portal.
        ya_de_alta = self._registro.estatus_registro == db.EST_DADO_ALTA
        db.actualizar_datos_levantamiento(
            self._registro.id, id_tipo_activo=tipo, datos=valores,
            modificado=True if ya_de_alta else None)
        self.page.pop_dialog()
        self.app.avisar("Datos del activo guardados.", VERDE)
        if callable(self.al_guardar):
            self.al_guardar()

    # -------------------------------------------------------- utilidades
    def _safe_update(self) -> None:
        try:
            self.dialogo.update()
        except (RuntimeError, AssertionError, AttributeError):
            pass  # aún no montado; se reflejará al renderizar
