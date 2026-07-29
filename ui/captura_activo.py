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
from ui.componentes import (CampoEtiquetado, CampoFecha, Modal,
                            boton_herramienta, boton_primario, campo_opciones,
                            campo_texto, icono_accion, seccion_formulario)
from ui.selector_empleado import DialogoSelectorEmpleado
from ui.selector_insumo import DialogoSelectorInsumo

_ANCHO = 760
_ALTO_CAMPOS = 460

# Ícono por grupo de campos (los grupos los define core/tipos_activo.py). Si
# apareciera uno nuevo, cae en el ícono por defecto en vez de romper.
_ICONO_GRUPO = {
    "Identificación": ft.Icons.BADGE,
    "Compra": ft.Icons.SHOPPING_CART,
    "Resguardo": ft.Icons.ASSIGNMENT_IND,
    "Detalles Insumo": ft.Icons.INFO_OUTLINE,
}
_ICONO_GRUPO_DEFECTO = ft.Icons.LIST_ALT
# "Detalles Insumo" son características cortas (marca, modelo…): caben de a 3.
_COLUMNAS_GRUPO = {"Detalles Insumo": 3}


class _CampoSeleccion:
    """Campo que se ELIGE de un catálogo del SIPP (insumo, empleado…), no se teclea.

    Muestra el nombre elegido (solo lectura) + un botón que abre un selector.
    Guarda el ID del SIPP (que el RPA usa para elegirlo por ID exacto en su modal).
    Expone `.value` (nombre) e `.id_sel`. La subclase define cómo abrir el selector."""

    def __init__(self, dialogo, label: str, nombre: str = "", id_sel: str = "",
                 hint: str = ""):
        self._dialogo = dialogo
        self.id_sel = str(id_sel or "")
        # `flotante`: dentro del modal el rótulo va encajado en el borde del
        # campo, como el mockup, no encima.
        self._bloque, self._tf = campo_texto(
            label, valor=nombre or "", hint=hint, read_only=True, flotante=True,
            suffix=icono_accion(ft.Icons.SEARCH, "Buscar", self._abrir))

    @property
    def control(self) -> ft.Control:
        return self._bloque

    @property
    def value(self) -> str:
        return self._tf.value or ""

    @value.setter
    def value(self, v: str) -> None:
        self._tf.value = v or ""

    def set(self, id_sel, nombre: str) -> None:
        self.id_sel = str(id_sel)
        self._tf.value = nombre
        try:
            self._tf.update()
        except (RuntimeError, AssertionError):
            pass

    def _abrir(self, _e=None) -> None:  # la subclase lo implementa
        raise NotImplementedError


class _CampoInsumo(_CampoSeleccion):
    """Campo del insumo (se elige del catálogo del SIPP)."""

    def __init__(self, dialogo, nombre="", id_insumo=""):
        super().__init__(dialogo, "Insumo *", nombre, id_insumo,
                         hint="Elige el insumo del catálogo del SIPP")

    # Compatibilidad: el resto del código lee .id_insumo.
    @property
    def id_insumo(self) -> str:
        return self.id_sel

    def _abrir(self, _e=None) -> None:
        reg = self._dialogo._registro
        sug = self.value or (reg.nombre_insumo if reg else "")
        self._dialogo._campo_insumo = self
        self._dialogo.selector_insumo.abrir(sugerido=sug)


class _CampoEmpleado(_CampoSeleccion):
    """Campo del empleado de resguardo (se elige del catálogo del SIPP)."""

    def __init__(self, dialogo, nombre="", id_empleado=""):
        super().__init__(dialogo, "Empleado (resguardo) *", nombre, id_empleado,
                         hint="Elige el empleado del catálogo del SIPP")

    def _abrir(self, _e=None) -> None:
        reg = self._dialogo._registro
        sug = self.value or (reg.responsable if reg else "")
        self._dialogo._campo_empleado = self
        self._dialogo.selector_empleado.abrir(sugerido=sug)


class DialogoCapturaActivo:
    """Formulario dinámico por tipo de activo para preparar el alta en el SIPP."""

    def __init__(self, app, al_guardar=None):
        self.app = app
        self.page = app.page
        self.al_guardar = al_guardar          # callback tras guardar (p. ej. refrescar)
        self._registro: "db.Levantamiento | None" = None
        self._controles: dict[str, tuple] = {}  # clave -> (CampoActivo, control)
        self._campo_insumo: "_CampoInsumo | None" = None    # campo de insumo activo
        self._campo_empleado: "_CampoEmpleado | None" = None  # campo de empleado activo
        # Selectores (buscan en la caché local de los catálogos del SIPP).
        self.selector_insumo = DialogoSelectorInsumo(app, al_elegir=self._insumo_elegido)
        self.selector_empleado = DialogoSelectorEmpleado(app, al_elegir=self._empleado_elegido)
        self._construir()

    def _insumo_elegido(self, id_insumo, nombre: str) -> None:
        """Callback del selector: fija el insumo elegido en el campo activo."""
        if self._campo_insumo is not None:
            self._campo_insumo.set(id_insumo, nombre)

    def _empleado_elegido(self, id_empleado, nombre: str) -> None:
        if self._campo_empleado is not None:
            self._campo_empleado.set(id_empleado, nombre)

    # ------------------------------------------------------------ UI
    def _construir(self) -> None:
        # La opción se identifica por el NOMBRE del tipo: es lo que ve el usuario
        # y lo que el RPA busca en el combo del SIPP; el id se resuelve con
        # ID_POR_NOMBRE al guardar.
        _, self.dd_tipo = campo_opciones(
            "Tipo de activo *", list(TIPOS_ACTIVO.values()),
            flotante=True, on_change=self._cambiar_tipo)

        self.modal = Modal(
            self.page, "Capturar datos del activo",
            ancho=_ANCHO, alto_cuerpo=_ALTO_CAMPOS,
            acciones=[
                boton_herramienta("Cancelar", on_click=lambda _e: self.modal.cerrar()),
                boton_primario("Guardar", ft.Icons.SAVE, self._guardar),
            ])
        # El tipo encabeza el cuerpo (fija qué campos se muestran); debajo va la
        # zona dinámica, que se repinta al cambiarlo.
        self._area_campos = ft.Column(spacing=28, tight=True)
        self.modal.cuerpo.controls = [
            # `columnas=1`: es un campo solo y manda en todo el formulario, así
            # que ocupa el ancho completo. Con el 2 por defecto se quedaba en la
            # primera mitad y la otra se rellenaba con un hueco vacío.
            seccion_formulario("Tipo de activo", ft.Icons.CATEGORY,
                               [self.dd_tipo], columnas=1),
            self._area_campos,
        ]

    # ------------------------------------------------------- apertura
    def abrir(self, registro: "db.Levantamiento") -> None:
        """Abre el formulario para `registro`, precargando lo ya capturado."""
        self._registro = registro
        self.modal.subtitulo = (
            f"{registro.nombre_insumo} · Serie: {registro.no_serie or '—'}")
        self.dd_tipo.value = nombre_tipo(registro.id_tipo_activo) or None
        self._render_campos()
        self.modal.abrir()

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
            controles = []
            for campo in campos:
                ctrl = self._control_para(campo, self._valor_inicial(campo, datos))
                self._controles[campo.clave] = (campo, ctrl)
                # Todos los campos son envoltorios con `.control` y `.value`;
                # al layout va siempre `.control`.
                controles.append(ctrl.control)
            secciones.append(seccion_formulario(
                grupo, _ICONO_GRUPO.get(grupo, _ICONO_GRUPO_DEFECTO), controles,
                columnas=_COLUMNAS_GRUPO.get(grupo, 2)))
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

    def _control_para(self, campo, valor):
        """Crea el control adecuado al tipo de campo, precargado con `valor`."""
        etiqueta = campo.etiqueta + (" *" if campo.requerido else "")
        valor = "" if valor is None else str(valor)

        if campo.clave == "nb_NombreInsumo":
            # El insumo se ELIGE del catálogo del SIPP (no se teclea): así el RPA
            # tiene el ID exacto para seleccionarlo en el modal "Buscar Insumo".
            id_ini = (self._registro.datos().get("id_InsumoOrigen", "")
                      if self._registro else "")
            return _CampoInsumo(self, nombre=valor, id_insumo=id_ini)

        if campo.clave == "nb_Empleado":
            # El empleado de resguardo también se elige por catálogo (modal del SIPP).
            id_ini = (self._registro.datos().get("id_EmpleadoResguardo", "")
                      if self._registro else "")
            return _CampoEmpleado(self, nombre=valor, id_empleado=id_ini)

        if campo.control == "select":
            opciones = None
            if campo.opciones:
                # La opción se identifica por su ETIQUETA, que además es el texto
                # que el RPA buscará en el combo equivalente del SIPP.
                opciones = [str(v) for v in campo.opciones.values()]
            elif "empresa" in campo.clave.lower():
                # Catálogo local del Grupo Petroil para los campos de empresa.
                opciones = list(NOMBRES_EMPRESAS)
            if opciones is not None:
                return CampoEtiquetado(*campo_opciones(
                    etiqueta, opciones, valor=valor or None, flotante=True))
            # Catálogo que vive en el SIPP (sucursal, centro de costo, insumo…):
            # se captura como texto y el RPA lo buscará por su nombre.
            return CampoEtiquetado(*campo_texto(
                etiqueta, valor=valor, flotante=True,
                hint="Catálogo del SIPP (se busca por nombre)"))
        if campo.control == "date":
            # Estándar del proyecto: las fechas se eligen por calendario.
            return CampoFecha(self.page, etiqueta, valor, flotante=True)
        if campo.control == "number":
            return CampoEtiquetado(*campo_texto(
                etiqueta, valor=valor, hint="0.00", flotante=True))
        return CampoEtiquetado(*campo_texto(etiqueta, valor=valor, flotante=True))

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
            # Insumo y empleado guardan además su ID del SIPP (lo que usa el RPA).
            if isinstance(ctrl, _CampoInsumo):
                valores["id_InsumoOrigen"] = ctrl.id_sel
            elif isinstance(ctrl, _CampoEmpleado):
                valores["id_EmpleadoResguardo"] = ctrl.id_sel
        if faltantes:
            self.app.avisar(
                "Faltan campos obligatorios: " + ", ".join(faltantes[:5])
                + ("…" if len(faltantes) > 5 else ""), ROJO)
            return
        # Si el activo YA está dado de alta en el SIPP, editar sus datos lo deja
        # marcado como "modificado": así el RPA de modificación sabe cuáles
        # reenviar al portal.
        ya_de_alta = self._registro.estatus_registro == db.EST_DADO_ALTA
        # El No. de serie capturado (nu_Serie) se refleja en la COLUMNA del
        # registro: es la que se muestra en la tabla y con la que se busca el
        # insumo (en el listado y en la bandeja de compras).
        serie = (valores.get("nu_Serie") or "").strip()
        db.actualizar_datos_levantamiento(
            self._registro.id, id_tipo_activo=tipo, datos=valores,
            modificado=True if ya_de_alta else None,
            no_serie=serie if serie else None)
        self.modal.cerrar()
        self.app.avisar("Datos del activo guardados.", VERDE)
        if callable(self.al_guardar):
            self.al_guardar()

    # -------------------------------------------------------- utilidades
    def _safe_update(self) -> None:
        self.modal.refrescar()
