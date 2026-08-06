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

import asyncio

import flet as ft

from core import db
from core.empresas import ID_POR_EMPRESA
from core.tipos_activo import ID_POR_NOMBRE, TIPOS_ACTIVO, campos_de_tipo, nombre_tipo
from ui.comun import GRIS, NARANJA, NOMBRES_EMPRESAS, ROJO, VERDE
from ui.componentes import (CampoEtiquetado, CampoFecha, Modal,
                            boton_herramienta, boton_primario, boton_secundario,
                            buscador, campo_opciones, campo_texto, fila_resultado,
                            icono_accion, lista_resultados, seccion_formulario)
from ui.selector_empleado import DialogoSelectorEmpleado
from ui.selector_insumo import DialogoSelectorInsumo

_ANCHO = 760
_ALTO_CAMPOS = 460

# Regla de negocio: los insumos de equipo personal van al grupo de centro de costo
# "CC Empleados" y su centro de costo es el del empleado responsable. Se detecta
# por el NOMBRE del insumo (palabras clave).
_INSUMOS_CC_EMPLEADOS = ("MONITOR", "LAPTOP", "CELULAR", "TABLET", "CPU",
                         "COMPUTADORA DE ESCRITORIO", "MINI PC", "MINIPC")
_GRUPO_CC_EMPLEADOS = "CC EMPLEADOS"


def _norm_txt(texto) -> str:
    """Mayúsculas, sin acentos ni espacios sobrantes (para comparar nombres)."""
    t = str(texto or "").upper()
    for a, b in (("Á", "A"), ("É", "E"), ("Í", "I"), ("Ó", "O"), ("Ú", "U"), ("Ñ", "N")):
        t = t.replace(a, b)
    return " ".join(t.split())

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


class _CampoCatalogo:
    """Campo que se elige de un catálogo LOCAL (lista de nombres) mediante un
    diálogo con buscador. Es para catálogos largos (centros de costo pueden ser
    miles) que como <select> congelan la app: el diálogo filtra en vivo y solo
    pinta un tope de resultados.

    `opciones_fn()` devuelve la lista de nombres AL ABRIR (dinámica, para la
    dependencia grupo->centro). `al_cambiar(nombre)` se llama al elegir."""

    def __init__(self, dialogo, label: str, valor: str, opciones_fn,
                 al_cambiar=None):
        self._dialogo = dialogo
        self._opciones_fn = opciones_fn
        self._al_cambiar = al_cambiar
        self._titulo = label.replace(" *", "")
        self._bloque, self._tf = campo_texto(
            label, valor=valor or "", read_only=True, flotante=True,
            hint="Elegir del catálogo",
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
        try:
            self._tf.update()
        except (RuntimeError, AssertionError):
            pass

    def _abrir(self, _e=None) -> None:
        self._dialogo._abrir_selector_catalogo(
            self._titulo, self._opciones_fn(), self._tf.value or "", self._elegido)

    def _elegido(self, opcion: str) -> None:
        self.value = opcion
        if callable(self._al_cambiar):
            self._al_cambiar(opcion)


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
        # Catálogos de centro de costo (para el selector dependiente grupo->centro).
        self._campo_grupo: "_CampoCatalogo | None" = None
        self._campo_centro: "_CampoCatalogo | None" = None
        self._mapa_grupos: dict[str, int] = {}   # nb_grupo -> id_grupo
        self._id_empresa_cap: "int | None" = None
        self._sucursal_cap: str = ""
        # Selectores (buscan en la caché local de los catálogos del SIPP).
        self.selector_insumo = DialogoSelectorInsumo(app, al_elegir=self._insumo_elegido)
        self.selector_empleado = DialogoSelectorEmpleado(app, al_elegir=self._empleado_elegido)
        self._construir()

    def _empresa_id_actual(self) -> "int | None":
        return ID_POR_EMPRESA.get(self.dd_empresa.value or "")

    def _sucursales_empresa(self) -> list[str]:
        idemp = self._empresa_id_actual()
        return db.listar_sucursales_sipp(idemp) if idemp is not None else []

    def _departamentos_empresa(self) -> list[str]:
        idemp = self._empresa_id_actual()
        return db.listar_departamentos(idemp) if idemp is not None else []

    def _cambiar_contexto(self, _e=None) -> None:
        """Al cambiar empresa o sucursal, se repintan los campos: el grupo y el
        centro de costo dependen de la sucursal, y los desplegables de la ubicación
        dependen de la empresa."""
        self._render_campos()
        self._safe_update()

    def _insumo_elegido(self, id_insumo, nombre: str) -> None:
        """Callback del selector: fija el insumo elegido en el campo activo."""
        if self._campo_insumo is not None:
            self._campo_insumo.set(id_insumo, nombre)
        self._aplicar_regla_cc()

    def _empleado_elegido(self, id_empleado, nombre: str) -> None:
        if self._campo_empleado is not None:
            self._campo_empleado.set(id_empleado, nombre)
        self._aplicar_regla_cc()

    def _valor_control(self, clave: str) -> str:
        """Valor actual de un control ya renderizado (por su clave), o ''."""
        par = self._controles.get(clave)
        return (getattr(par[1], "value", "") or "") if par else ""

    def _aplicar_regla_cc(self) -> None:
        """Si el insumo es equipo personal (monitor/laptop/celular/tablet/cpu/mini
        pc), asigna el grupo 'CC Empleados' y el centro de costo del EMPLEADO (por
        nombre). Solo rellena lo que esté vacío (no pisa una elección previa)."""
        if self._id_empresa_cap is None or self._campo_grupo is None:
            return
        insumo = _norm_txt(self._valor_control("nb_NombreInsumo")
                           or (self._registro.nombre_insumo if self._registro else ""))
        kw = next((k for k in _INSUMOS_CC_EMPLEADOS if k in insumo), None)
        if kw is None:
            return
        gcc = next((g for g in db.listar_grupos_cc(self._id_empresa_cap, self._sucursal_cap)
                    if _GRUPO_CC_EMPLEADOS in _norm_txt(g["nb_grupo"])), None)
        if gcc is None:
            return
        if not self._campo_grupo.value:
            self._campo_grupo.value = gcc["nb_grupo"]
            self._mapa_grupos.setdefault(gcc["nb_grupo"], gcc["id_grupo"])
        # Centro de costo del empleado (el que contiene su nombre); si hay varios,
        # se prefiere el que además coincide con el tipo de equipo.
        if self._campo_centro is not None and not self._campo_centro.value:
            emp = _norm_txt(self._valor_control("nb_Empleado")
                            or (self._registro.responsable if self._registro else ""))
            if emp:
                centros = db.listar_centros_cc(self._id_empresa_cap, gcc["id_grupo"])
                cand = [c for c in centros if emp in _norm_txt(c)]
                mejor = next((c for c in cand if kw in _norm_txt(c)), None) \
                    or (cand[0] if cand else None)
                if mejor:
                    self._campo_centro.value = mejor

    # ------------------------------------------------------------ UI
    def _construir(self) -> None:
        # La opción se identifica por el NOMBRE del tipo: es lo que ve el usuario
        # y lo que el RPA busca en el combo del SIPP; el id se resuelve con
        # ID_POR_NOMBRE al guardar.
        _, self.dd_tipo = campo_opciones(
            "Tipo de activo *", list(TIPOS_ACTIVO.values()),
            flotante=True, on_change=self._cambiar_tipo)
        # Empresa/sucursal/departamento del LEVANTAMIENTO (las columnas del
        # registro). Misma fuente que el listado general: empresa del catálogo del
        # Grupo Petroil; sucursal y departamento se ELIGEN del catálogo del SIPP de
        # esa empresa (selector con buscador, como en la tabla). Al cambiar empresa
        # o sucursal se repintan los campos (grupo/centro dependen de la sucursal).
        _, self.dd_empresa = campo_opciones(
            "Empresa", list(NOMBRES_EMPRESAS), flotante=True,
            on_change=self._cambiar_contexto)
        self.campo_sucursal = _CampoCatalogo(
            self, "Sucursal", "", opciones_fn=self._sucursales_empresa,
            al_cambiar=lambda _v: self._cambiar_contexto())
        self.campo_departamento = _CampoCatalogo(
            self, "Departamento", "", opciones_fn=self._departamentos_empresa)
        # Etiqueta: la genera el SIPP al dar de alta (botón "Generar Etiqueta" del
        # portal); aquí es de SOLO LECTURA y se llena solo tras el alta.
        _, self.tf_etiqueta = campo_texto(
            "Etiqueta", flotante=True, read_only=True,
            hint="Se genera en el SIPP al dar de alta")

        self.modal = Modal(
            self.page, "Capturar datos del activo",
            ancho=_ANCHO, alto_cuerpo=_ALTO_CAMPOS,
            acciones=[
                boton_herramienta("Cancelar", on_click=lambda _e: self.modal.cerrar()),
                boton_primario("Guardar", ft.Icons.SAVE, self._guardar),
            ])
        # Ubicación del levantamiento + tipo encabezan el cuerpo; debajo va la
        # zona dinámica, que se repinta al cambiar el tipo.
        self._area_campos = ft.Column(spacing=28, tight=True)
        # Imágenes/soporte del insumo (se suben al SIPP con el RPA al dar de alta).
        self._imagenes_insumo: list[str] = []
        self._area_imagenes = ft.Column(spacing=4, tight=True)
        seccion_imagenes = seccion_formulario(
            "Imágenes del insumo (PDF, JPG o PNG · máx 3)", ft.Icons.PHOTO_LIBRARY,
            [ft.Column(
                [ft.Row([boton_secundario("Agregar imágenes",
                                          ft.Icons.ADD_PHOTO_ALTERNATE,
                                          self._agregar_imagenes)]),
                 self._area_imagenes,
                 ft.Text("Se subirán al SIPP junto con el activo al darlo de alta.",
                         size=11, color=GRIS)],
                spacing=8, tight=True)],
            columnas=1)
        self.modal.cuerpo.controls = [
            seccion_formulario(
                "Ubicación del levantamiento", ft.Icons.PLACE,
                [self.dd_empresa, self.campo_sucursal.control,
                 self.campo_departamento.control, self.tf_etiqueta]),
            # `columnas=1`: es un campo solo y manda en todo el formulario, así
            # que ocupa el ancho completo. Con el 2 por defecto se quedaba en la
            # primera mitad y la otra se rellenaba con un hueco vacío.
            seccion_formulario("Tipo de activo", ft.Icons.CATEGORY,
                               [self.dd_tipo], columnas=1),
            self._area_campos,
            seccion_imagenes,
        ]

    # ------------------------------------------------------- apertura
    def abrir(self, registro: "db.Levantamiento") -> None:
        """Abre el formulario para `registro`, precargando lo ya capturado."""
        self._registro = registro
        self.modal.subtitulo = (
            f"{registro.nombre_insumo} · Serie: {registro.no_serie or '—'}")
        self.dd_tipo.value = nombre_tipo(registro.id_tipo_activo) or None
        self.dd_empresa.value = registro.empresa or None
        self.campo_sucursal.value = registro.sucursal or ""
        self.campo_departamento.value = registro.departamento or ""
        self.tf_etiqueta.value = registro.etiqueta or ""
        self._imagenes_insumo = list(registro.datos().get("imagenes_insumo") or [])
        self._pintar_imagenes()
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
        # Empresa/sucursal del activo: definen qué departamentos/grupos/centros de
        # costo se ofrecen en sus desplegables (se toman de los campos de ubicación).
        self._id_empresa_cap = ID_POR_EMPRESA.get(self.dd_empresa.value or "")
        self._sucursal_cap = (self.campo_sucursal.value or "").strip()
        self._campo_grupo = self._campo_centro = None
        self._mapa_grupos = {}
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
            seccion = seccion_formulario(
                grupo, _ICONO_GRUPO.get(grupo, _ICONO_GRUPO_DEFECTO), controles,
                columnas=_COLUMNAS_GRUPO.get(grupo, 2))
            # En "Compra": botón para autollenar costo/factura/proveedor desde la
            # factura de la bandeja de compras (búsqueda por serie).
            if grupo == "Compra":
                seccion = ft.Column([self._barra_traer_factura(), seccion],
                                    spacing=8, tight=True)
            secciones.append(seccion)
        self._area_campos.controls = secciones
        # Regla de negocio: equipo personal -> grupo "CC Empleados" + centro del
        # empleado (solo si esos campos quedaron vacíos).
        self._aplicar_regla_cc()

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

    def _centros_del_grupo(self) -> list[str]:
        """Centros de costo del grupo ELEGIDO ahora (para el selector dependiente)."""
        gid = self._mapa_grupos.get(self._campo_grupo.value if self._campo_grupo else "")
        return (db.listar_centros_cc(self._id_empresa_cap, gid)
                if gid and self._id_empresa_cap is not None else [])

    def _grupo_cambio(self, _nombre: str) -> None:
        """Al cambiar el grupo, se limpia el centro (dependiente) para que el usuario
        elija uno del nuevo grupo."""
        if self._campo_centro is not None:
            self._campo_centro.value = ""

    def _abrir_selector_catalogo(self, titulo: str, opciones: list, actual: str,
                                 al_elegir) -> None:
        """Diálogo con buscador para elegir de una lista larga (evita el <select>
        gigante que congela). Filtra en vivo y solo pinta un tope de resultados."""
        _MAX = 100
        lista = lista_resultados()
        modal = Modal(self.page, titulo, ancho=560)

        def pintar(filtro: str = "") -> None:
            f = (filtro or "").strip().lower()
            res = [o for o in opciones if f in o.lower()]
            lista.controls = [
                fila_resultado("", o, resaltado=(o == actual),
                               on_click=lambda _e, o=o: elegir(o))
                for o in res[:_MAX]]
            if not res:
                lista.controls = [ft.Container(
                    ft.Text("Sin coincidencias.", size=12,
                            color=ft.Colors.ON_SURFACE_VARIANT), padding=12)]
            elif len(res) > _MAX:
                lista.controls.append(ft.Container(
                    ft.Text(f"…y {len(res) - _MAX} más. Afina la búsqueda.",
                            size=11, color=ft.Colors.ON_SURFACE_VARIANT), padding=8))
            modal.refrescar()

        def elegir(opcion: str) -> None:
            modal.cerrar()
            al_elegir(opcion)

        tf = buscador("Buscar… (Enter elige el primero)", expand=True, autofocus=True)
        tf.on_change = lambda e: pintar(e.control.value)
        tf.on_submit = lambda e: (
            elegir(next((o for o in opciones
                         if (tf.value or "").strip().lower() in o.lower()), None))
            if any((tf.value or "").strip().lower() in o.lower() for o in opciones)
            else None)
        modal.cuerpo.controls = [tf, lista]
        pintar()
        modal.abrir()

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

        # Catálogos descargados del SIPP (por empresa/sucursal). Se eligen por un
        # diálogo con buscador (NO <select>: los centros de costo pueden ser miles
        # y un combo gigante congela la app). Si no hay caché para esta empresa, se
        # cae al campo de texto (comportamiento previo).
        if campo.clave == "id_Departamento" and self._id_empresa_cap is not None:
            if db.listar_departamentos(self._id_empresa_cap):
                return _CampoCatalogo(
                    self, etiqueta, valor,
                    opciones_fn=lambda: db.listar_departamentos(self._id_empresa_cap))

        # Sucursal de COMPRA: mismo desplegable que la sucursal de la ubicación del
        # levantamiento (sucursales de la empresa, cacheadas del SIPP).
        if campo.clave == "id_SucursalAgregar" and self._id_empresa_cap is not None:
            if self._sucursales_empresa():
                return _CampoCatalogo(
                    self, etiqueta, valor or self._sucursal_cap,
                    opciones_fn=self._sucursales_empresa)

        if campo.clave == "id_GrupoCentroCosto" and self._id_empresa_cap is not None:
            grupos = db.listar_grupos_cc(self._id_empresa_cap, self._sucursal_cap)
            if grupos:
                self._mapa_grupos = {g["nb_grupo"]: g["id_grupo"] for g in grupos}
                self._campo_grupo = _CampoCatalogo(
                    self, etiqueta, valor,
                    opciones_fn=lambda: [g["nb_grupo"] for g in db.listar_grupos_cc(
                        self._id_empresa_cap, self._sucursal_cap)],
                    al_cambiar=self._grupo_cambio)
                return self._campo_grupo

        if campo.clave == "id_CentroCosto" and self._id_empresa_cap is not None:
            # Solo si hay grupos cacheados (el centro depende del grupo elegido).
            if self._mapa_grupos:
                self._campo_centro = _CampoCatalogo(
                    self, etiqueta, valor, opciones_fn=self._centros_del_grupo)
                return self._campo_centro

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

    # ------------------------------------------ datos de factura (autollenar)
    def _barra_traer_factura(self) -> ft.Control:
        """Fila con el botón que trae costo/factura/proveedor desde la bandeja."""
        self._ring_factura = ft.ProgressRing(width=18, height=18, stroke_width=2,
                                             visible=False)
        self._btn_factura = boton_secundario(
            "Traer datos de la factura", ft.Icons.RECEIPT_LONG,
            self._traer_datos_factura,
            tooltip="Busca en la bandeja de compras la factura del activo (por su "
                    "No. de serie) y llena Costo, Factura y Proveedor")
        return ft.Row([self._btn_factura, self._ring_factura], spacing=10,
                      vertical_alignment=ft.CrossAxisAlignment.CENTER)

    def _fijar_valor(self, clave: str, valor: str) -> None:
        """Fija el valor de un control ya renderizado (si existe y hay valor)."""
        par = self._controles.get(clave)
        if par and valor:
            par[1].value = valor

    async def _traer_datos_factura(self, _e=None) -> None:
        """Busca la factura del activo por su serie en la bandeja de compras y
        autollena Costo (precio del CFDI), Factura (folio) y Proveedor."""
        from core import compras_sipp as compras, credenciales

        r = self._registro
        serie = (self._valor_control("nu_Serie") or (r.no_serie if r else "") or "").strip()
        etiqueta = (r.etiqueta if r else "") or (self.tf_etiqueta.value or "")
        if not compras.serie_valida(serie, etiqueta):
            self.app.avisar("El activo no tiene un No. de serie válido (o coincide con "
                            "la etiqueta) para buscar su factura.", NARANJA)
            return
        creds = credenciales.cargar()
        if not creds or not creds[0]:
            self.app.avisar("Configura primero las credenciales del SIPP (botón ⚙).", ROJO)
            return
        usuario, contrasena = creds
        id_empresa = ID_POR_EMPRESA.get(self.dd_empresa.value or "")

        self._ring_factura.visible = True
        self._btn_factura.disabled = True
        self._safe_update()

        entrada, info, error = None, None, None

        async def flujo() -> None:
            nonlocal entrada, info, error
            from core.rpa_sipp import SesionSipp
            try:
                async with SesionSipp(headless=True) as sipp:
                    await sipp.login(usuario, contrasena)
                    entrada = await compras.buscar_entrada_por_serie(
                        sipp, serie, id_empresa)
                    if entrada is not None:
                        info = await compras.datos_factura(sipp, entrada)
            except Exception as exc:  # noqa: BLE001 — se reporta al usuario
                error = str(exc)

        from core.rpa_sipp import BucleRpa
        bucle = BucleRpa()
        try:
            await asyncio.wrap_future(bucle.enviar(flujo()))
        finally:
            bucle.cerrar()
            self._ring_factura.visible = False
            self._btn_factura.disabled = False

        if error:
            self._safe_update()
            self.app.avisar(f"No se pudo consultar la factura: {error}", ROJO,
                           duracion=8000)
            return
        if entrada is None:
            self._safe_update()
            self.app.avisar(f"No se encontró entrada de compra para la serie «{serie}».",
                           NARANJA, duracion=7000)
            return
        precio = (info or {}).get("precio")
        folio = (info or {}).get("folio")
        if precio is not None:
            self._fijar_valor("im_Costo", f"{precio:.2f}")
        self._fijar_valor("nb_Factura", folio or "")
        self._fijar_valor("nb_Proveedor", entrada.proveedor)
        self._safe_update()
        detalle = []
        if precio is not None:
            detalle.append(f"costo {precio:.2f}")
        if folio:
            detalle.append(f"factura {folio}")
        if entrada.proveedor:
            detalle.append(entrada.proveedor)
        self.app.avisar("Datos de la factura traídos" + (": " + ", ".join(detalle)
                        if detalle else "") + ".", VERDE, duracion=7000)

    # ----------------------------------------------- imágenes del insumo
    async def _agregar_imagenes(self, _e=None) -> None:
        import os
        import shutil

        from core import rutas
        if len(self._imagenes_insumo) >= 3:
            self.app.avisar("Máximo 3 imágenes por activo.", NARANJA)
            return
        archivos = await self.app.picker.pick_files(
            dialog_title="Selecciona imágenes del insumo (PDF, JPG o PNG)",
            allowed_extensions=["pdf", "jpg", "jpeg", "png"], allow_multiple=True)
        if not archivos:
            return
        # Se copian a DATOS para que sigan disponibles al correr el RPA aunque el
        # usuario mueva los originales.
        carpeta = os.path.join(rutas.DATOS, "imagenes_insumo")
        os.makedirs(carpeta, exist_ok=True)
        for a in archivos:
            if len(self._imagenes_insumo) >= 3:
                self.app.avisar("Solo se toman las primeras 3 imágenes.", NARANJA)
                break
            destino = os.path.join(carpeta, os.path.basename(a.path))
            try:
                if os.path.abspath(a.path) != os.path.abspath(destino):
                    shutil.copy2(a.path, destino)
            except Exception:  # noqa: BLE001 — si no se pudo copiar, se usa el original
                destino = a.path
            if destino not in self._imagenes_insumo:
                self._imagenes_insumo.append(destino)
        self._pintar_imagenes()

    def _pintar_imagenes(self) -> None:
        import os
        if not self._imagenes_insumo:
            self._area_imagenes.controls = [
                ft.Text("Sin imágenes.", size=11, color=GRIS)]
        else:
            self._area_imagenes.controls = [
                ft.Row([ft.Icon(ft.Icons.INSERT_DRIVE_FILE, size=16, color=GRIS),
                        ft.Text(os.path.basename(p), size=12, expand=True, no_wrap=False),
                        icono_accion(ft.Icons.CLOSE, "Quitar",
                                     lambda _e, p=p: self._quitar_imagen(p))],
                       spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)
                for p in self._imagenes_insumo]
        self._safe_update()

    def _quitar_imagen(self, p: str) -> None:
        if p in self._imagenes_insumo:
            self._imagenes_insumo.remove(p)
        self._pintar_imagenes()

    # ---------------------------------------------------------- guardar
    def _guardar(self, _e=None) -> None:
        if self._registro is None:
            return
        # Si el activo YA está dado de alta, cualquier edición lo marca como
        # "modificado" (para el RPA de modificación).
        ya_de_alta = self._registro.estatus_registro == db.EST_DADO_ALTA
        # Ubicación del levantamiento (empresa/sucursal/departamento): se guarda
        # SIEMPRE, no depende del tipo de activo.
        db.actualizar_ubicacion_levantamiento(
            self._registro.id, empresa=self.dd_empresa.value or "",
            sucursal=(self.campo_sucursal.value or "").strip(),
            departamento=(self.campo_departamento.value or "").strip())

        tipo = self._tipo_actual()
        if tipo is None:
            # Sin tipo: solo se actualizó la ubicación (no hay detalle que capturar).
            if ya_de_alta:
                db.actualizar_datos_levantamiento(self._registro.id, modificado=True)
            self.modal.cerrar()
            self.app.avisar("Ubicación actualizada.", VERDE)
            if callable(self.al_guardar):
                self.al_guardar()
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
        # El No. de serie capturado (nu_Serie) se refleja en la COLUMNA del
        # registro: es la que se muestra en la tabla y con la que se busca el
        # insumo (en el listado y en la bandeja de compras).
        serie = (valores.get("nu_Serie") or "").strip()
        # El nombre del insumo elegido en "Identificación" se refleja en la COLUMNA
        # del registro (la que se ve en el listado): es el insumo REAL del SIPP.
        insumo_cap = (valores.get("nb_NombreInsumo") or "").strip()
        # Imágenes del insumo (rutas): se guardan en datos_json para que el RPA las
        # suba al alta.
        valores["imagenes_insumo"] = list(self._imagenes_insumo)
        db.actualizar_datos_levantamiento(
            self._registro.id, id_tipo_activo=tipo, datos=valores,
            modificado=True if ya_de_alta else None,
            no_serie=serie if serie else None,
            nombre_insumo=insumo_cap or None)
        self.modal.cerrar()
        self.app.avisar("Datos del activo guardados.", VERDE)
        if callable(self.al_guardar):
            self.al_guardar()

    # -------------------------------------------------------- utilidades
    def _safe_update(self) -> None:
        self.modal.refrescar()
