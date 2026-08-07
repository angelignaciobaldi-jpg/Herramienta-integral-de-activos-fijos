"""Componentes de interfaz reutilizables (sistema de diseño; ver DISENO.md).

Adaptados del mockup `ejemplos/registro.html`. Esta es la forma ESTÁNDAR de crear
botones, pestañas, campos y tarjetas de sección en la app: al centralizarlos, un
cambio de estilo se aplica en todas las pantallas a la vez, en lugar de tener que
perseguir cada `ft.FilledButton` suelto.

Reglas al ampliar este módulo:

- Los colores se piden por ROL de Material 3 (`PRIMARY_CONTAINER`, `SECONDARY`,
  `OUTLINE_VARIANT`…), nunca en hex, para que ui/tema.py resuelva claro y oscuro.
- Los tamaños salen de los tokens del diseño: radio 8px, padding horizontal 24 y
  vertical 8, separación 8/16/24.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from typing import Callable, Iterable

import flet as ft

# Formato y parseo de fecha viven en ui/comun.py (lógica, sin estilo); aquí solo
# se les pone interfaz. `comun` no importa este módulo, así que no hay ciclo.
from ui.comun import fmt_fecha, parse_fecha


def _refrescar(control: ft.Control) -> None:
    """`update()` tolerante: el control puede no estar montado todavía."""
    try:
        control.update()
    except (RuntimeError, AssertionError, AttributeError):
        pass

# --- Tokens del diseño ---------------------------------------------------
RADIO = 8         # rounded.lg
PAD_H = 24        # spacing.lg
PAD_V = 10        # spacing.sm (+2 para que el alto del botón cuadre con el ícono)
GAP_SM = 8
GAP_MD = 16
GAP_LG = 24
# Ancho a reservar para la barra de scroll. El tema la fija en 12px y siempre
# visible (`ScrollbarTheme` en app.py), y se dibuja SOBRE el borde derecho del
# área con scroll: sin reservarle sitio, se encima al contenido. Mismo criterio
# que `_GUTTER_SCROLL` en ui/tabla_responsiva.py.
GUTTER_SCROLL = 14

_FORMA = ft.RoundedRectangleBorder(radius=RADIO)

# Puntero de "esto se pulsa". Se declara EXPLÍCITO por estado en vez de confiar
# en el de fábrica de Material: así el botón apagado no ofrece una mano que no
# lleva a nada.
CURSOR_BOTON = {ft.ControlState.DEFAULT: ft.MouseCursor.CLICK,
                ft.ControlState.DISABLED: ft.MouseCursor.BASIC}


def puntero_mano(caja: ft.Container) -> ft.Container:
    """Hace que el puntero cambie a la mano sobre una caja pulsable.

    **`ft.Container` NO expone `mouse_cursor`** —solo lo tienen los botones de
    Material y `GestureDetector`—, así que una caja con `on_click` se queda con
    la flecha y no se lee como pulsable.

    El `GestureDetector` va DENTRO, envolviendo el contenido, y no por fuera de
    la caja: un Container pulsable dibuja su PROPIA región de ratón, y cuando
    hay regiones anidadas Flutter se queda con el cursor de la más interna. Por
    fuera, la caja lo pisaba en cuanto el puntero entraba de verdad —en el
    select se alcanzaba a ver el cambio solo en el filo del borde, y sobre los
    chevrones no se veía nunca—.

    Se MUTA la caja y se devuelve la misma, para no meter un control extra en
    el árbol: quien la construye la sigue teniendo tal cual, con su `on_click`,
    su `ink` y su `on_hover` intactos. El envoltorio va sin manejadores, y Flet
    solo registra los reconocedores de gesto de las devoluciones que no son
    `None`, así que tampoco intercepta el clic.

    Queda fuera el RELLENO de la caja (2–8px, según el control): ahí el cursor
    sigue siendo la flecha. Cubrirlo exigiría quitarle el `on_click` al
    Container y con él la tinta, que se nota más que esa orilla.
    """
    caja.content = ft.GestureDetector(content=caja.content,
                                      mouse_cursor=ft.MouseCursor.CLICK)
    return caja
# Elevación nivel 1 del diseño, para tarjetas de sección y el marco de la tabla.
SOMBRA_N1 = ft.BoxShadow(
    blur_radius=4, offset=ft.Offset(0, 2),
    color=ft.Colors.with_opacity(0.05, ft.Colors.BLACK))


# --- Botones -------------------------------------------------------------
def boton_primario(texto: str, icono=None, on_click=None,
                   tooltip: str | None = None, disabled: bool = False) -> ft.Control:
    """Acción principal de la pantalla: relleno azul marino con texto blanco.

    Debe haber UNO por bloque de acciones; el resto van como secundarios.

    Se usa el par PRIMARY/ON_PRIMARY y no PRIMARY_CONTAINER/ON_PRIMARY como el
    mockup: aquel mockup es solo para tema claro, y en oscuro `on_primary` es
    azul marino sobre un contenedor también oscuro (1.42:1, ilegible). El par
    PRIMARY/ON_PRIMARY lo garantiza Material en ambos temas.
    """
    return ft.FilledButton(
        texto, icon=icono, on_click=on_click, tooltip=tooltip, disabled=disabled,
        style=ft.ButtonStyle(
            bgcolor=ft.Colors.PRIMARY,
            color=ft.Colors.ON_PRIMARY,
            shape=_FORMA,
            padding=ft.Padding.symmetric(horizontal=PAD_H, vertical=PAD_V),
            elevation=1,
            mouse_cursor=CURSOR_BOTON,
        ),
    )


def boton_primario_icono(icono, tooltip: str, on_click=None, *,
                         disabled: bool = False) -> ft.Control:
    """Acción principal reducida a su ícono, con el texto en el TOOLTIP.

    Mide `ALTO_CAMPO_TABLA` de lado para casar con `SelectCompacto` en una barra
    de filtros. Aquí sí se puede fijar la altura: un botón respeta
    `width`/`height`, a diferencia de los campos de Material (ver la nota de
    `_estilo_campo`), que la recalculan por su cuenta.

    El tooltip es OBLIGATORIO: sin texto visible, es lo único que dice qué hace.
    """
    return ft.IconButton(
        icon=icono, tooltip=tooltip, on_click=on_click, disabled=disabled,
        width=ALTO_CAMPO_TABLA, height=ALTO_CAMPO_TABLA,
        icon_size=round(ALTO_CAMPO_TABLA * 0.5),
        style=ft.ButtonStyle(
            bgcolor=ft.Colors.PRIMARY,
            color=ft.Colors.ON_PRIMARY,
            shape=_FORMA,
            padding=ft.Padding.all(0),
            elevation=1,
            mouse_cursor=CURSOR_BOTON,
        ),
    )


def boton_secundario(texto: str, icono=None, on_click=None,
                     tooltip: str | None = None, disabled: bool = False) -> ft.Control:
    """Acción de apoyo: fondo de superficie con borde y texto en `secondary`."""
    return ft.OutlinedButton(
        texto, icon=icono, on_click=on_click, tooltip=tooltip, disabled=disabled,
        style=ft.ButtonStyle(
            bgcolor=ft.Colors.SURFACE_CONTAINER_LOWEST,
            color=ft.Colors.SECONDARY,
            side=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT),
            shape=_FORMA,
            padding=ft.Padding.symmetric(horizontal=PAD_H, vertical=PAD_V),
            mouse_cursor=CURSOR_BOTON,
        ),
    )


def boton_herramienta(texto: str, icono=None, on_click=None,
                      tooltip: str | None = None,
                      destructivo: bool = False) -> ft.Control:
    """Acción menor sobre la selección (sin fondo). `destructivo` la pinta en rojo."""
    return ft.TextButton(
        texto, icon=icono, on_click=on_click, tooltip=tooltip,
        style=ft.ButtonStyle(
            color=ft.Colors.ERROR if destructivo else ft.Colors.SECONDARY,
            shape=_FORMA,
            padding=ft.Padding.symmetric(horizontal=GAP_MD, vertical=GAP_SM),
            mouse_cursor=CURSOR_BOTON,
        ),
    )


# --- Campos --------------------------------------------------------------
def _etiqueta(texto: str) -> ft.Control:
    return ft.Text(texto, theme_style=ft.TextThemeStyle.LABEL_MEDIUM,
                   color=ft.Colors.ON_SURFACE_VARIANT)


# Un campo Material outlined mide 56px = 16 de relleno + 24 de CONTENIDO + 16.
# Todo lo que se meta en el `suffix` por encima de esos 24px estira el campo.
_LADO_ICONO_ACCION = 24


def icono_accion(icono, tooltip: str, on_click, *, color=None) -> ft.Container:
    """Ícono pulsable para el `suffix` de un campo (buscar, calendario…).

    Dos reglas que hacen que el campo NO crezca:
      - Nada de `ft.IconButton`: impone un mínimo táctil de 48px.
      - Tamaño fijo de 24px, que es justo la caja de contenido de un campo
        Material. Con 28 (20 de ícono + 4+4 de relleno) el campo se estiraba
        4px y quedaba más alto que sus vecinos.
    """
    return puntero_mano(ft.Container(
        ft.Icon(icono, size=18, color=color or ft.Colors.PRIMARY_CONTAINER),
        tooltip=tooltip, on_click=on_click, ink=True,
        width=_LADO_ICONO_ACCION, height=_LADO_ICONO_ACCION,
        padding=0, border_radius=RADIO,
        alignment=ft.Alignment(0, 0)))


def _bloque_etiquetado(etiqueta: str, campo: ft.Control,
                       width: int | None) -> ft.Column:
    """Etiqueta arriba + campo debajo.

    `STRETCH` es imprescindible: sin él, el campo toma su ancho NATURAL y deja
    hueco a la derecha cuando el bloque es más ancho (p. ej. al repartir el
    panel en tercios con `expand`). Es el mismo motivo por el que un TextField
    no llena un `Column` a menos que se le diga.
    """
    return ft.Column([_etiqueta(etiqueta), campo], spacing=4, tight=True,
                     width=width,
                     horizontal_alignment=ft.CrossAxisAlignment.STRETCH)


def _estilo_campo(control):
    """Aplica el borde/relleno común a un TextField o Dropdown.

    REGLA DE ALTURA (no la rompas): aquí NO se toca `dense`, `height` ni
    `content_padding`. La altura estándar de Material es idéntica entre
    TextField y Dropdown, y en cuanto se fija cualquiera de esos tres en unos
    campos y en otros no —o incluso en todos— el Dropdown deja de alinear con
    los TextField y los bordes inferiores dejan de coincidir. Lo ÚNICO que
    puede variar entre campos de un mismo grupo es el `width`.
    """
    control.filled = True
    # `fill_color` es el relleno del CAMPO y existe en los dos controles.
    # OJO: en `ft.Dropdown`, `bgcolor` NO es eso —es el fondo del MENÚ
    # desplegable—, así que usarlo dejaba el campo con el gris por defecto de
    # Material mientras los TextField salían blancos.
    control.fill_color = ft.Colors.SURFACE_CONTAINER_LOWEST
    # En Dropdown esto pinta el menú desplegable; en TextField, el campo. Se
    # deja el mismo tono en ambos para que campo y menú combinen.
    control.bgcolor = ft.Colors.SURFACE_CONTAINER_LOWEST
    control.border_radius = RADIO
    control.border_color = ft.Colors.OUTLINE_VARIANT
    control.focused_border_color = ft.Colors.SECONDARY
    control.focused_border_width = 2
    # Explícito y NO denso: `Dropdown` trae `False` por defecto y `TextField`
    # lo deja en `None`. Declararlo igual en ambos evita que la densidad se
    # resuelva distinto en cada uno, que es lo que desparejaba las alturas.
    control.dense = False
    return control


class CampoEtiquetado:
    """Empareja el bloque (etiqueta + campo) y el campo en UN objeto.

    Para formularios que construyen sus campos dinámicamente y necesitan
    guardar una sola referencia por campo: `.control` es lo que va al layout y
    `.value` lee o escribe el valor. Mismo contrato que `ui.comun.CampoFecha`,
    de modo que ambos se pueden tratar igual.
    """

    def __init__(self, bloque: ft.Control, campo: ft.Control) -> None:
        self.control = bloque
        self.campo = campo

    @property
    def value(self) -> str:
        return self.campo.value or ""

    @value.setter
    def value(self, v) -> None:
        self.campo.value = v or ""


def campo_texto(etiqueta: str | None = None, *, valor: str = "",
                hint: str | None = None, width: int | None = None,
                on_submit=None, on_blur=None, prefix_icon=None,
                password: bool = False, read_only: bool = False, suffix=None,
                expand: bool = False,
                flotante: bool = False) -> tuple[ft.Control, ft.TextField]:
    """Campo de texto. Devuelve `(bloque, campo)`: el bloque va a la interfaz y
    el campo es el control con `.value`.

    Dos estilos de rótulo, según el mockup de cada pantalla:
      - por defecto, la etiqueta va ARRIBA del campo (paneles y formularios);
      - `flotante=True` usa el rótulo nativo de Material, encajado en el borde
        (modales). Ahí no hay bloque aparte: se devuelve el campo dos veces.
    """
    campo = _estilo_campo(ft.TextField(
        value=valor, hint_text=hint, width=width, expand=expand or None,
        label=etiqueta if flotante else None,
        # `can_reveal_password` va siempre con `password`: un campo oculto sin
        # forma de verificar lo tecleado invita a errores al guardar.
        password=password, can_reveal_password=password,
        read_only=read_only, suffix=suffix,
        prefix_icon=prefix_icon, on_submit=on_submit, on_blur=on_blur))
    if etiqueta is None or flotante:
        return campo, campo
    bloque = _bloque_etiquetado(etiqueta, campo, width)
    return bloque, campo


def campo_opciones(etiqueta: str | None, opciones: Iterable[str], *,
                   valor: str | None = None, width: int | None = None,
                   hint: str | None = None, on_change=None,
                   flotante: bool = False,
                   editable: bool = True) -> tuple[ft.Control, ft.Dropdown]:
    """Selector. Devuelve `(bloque, campo)`; `flotante` como en `campo_texto`.

    Usa `ft.Dropdown` (Material 3), que por dentro es un campo de texto con un
    menú y por eso comparte la altura estándar del `TextField`; el viejo
    `DropdownM2` calculaba la suya aparte y nunca alineaba. El parámetro sigue
    llamándose `on_change` aunque el control exponga `on_select`, para no
    obligar a las pantallas a cambiar.

    `editable=True` (por defecto) permite ESCRIBIR para filtrar las opciones
    (agiliza catálogos largos como empresas/sucursales); pásalo False para un
    selector cerrado.
    """
    campo = _estilo_campo(ft.Dropdown(
        value=valor, width=width, hint_text=hint,
        label=etiqueta if flotante else None,
        editable=editable, enable_filter=editable,
        # Sin `expanded_insets`, un DropdownMenu de Material se dimensiona al
        # ANCHO DE SU OPCIÓN MÁS LARGA, no al del contenedor: por eso quedaba
        # más angosto que su celda por mucho STRETCH que llevara el padre.
        # Solo aplica cuando no se pidió un ancho fijo.
        expanded_insets=ft.Padding.all(0) if width is None else None,
        options=[ft.DropdownOption(key=o, text=o) for o in opciones],
        on_select=on_change))
    if etiqueta is None or flotante:
        return campo, campo
    bloque = _bloque_etiquetado(etiqueta, campo, width)
    return bloque, campo


# --- Campo de fecha ------------------------------------------------------
_FECHA_MIN = datetime(1990, 1, 1)
_FECHA_MAX = datetime(2100, 12, 31)


class CampoFecha:
    """Campo de fecha por CALENDARIO. Estándar del proyecto: las fechas NUNCA
    se teclean, para no depender del formato que escriba cada quien.

    Es un campo de solo lectura con ícono de calendario; al pulsarlo abre el
    DatePicker de Material en español. Expone `.control` y `.value` (texto
    'DD/MM/AAAA'), el mismo contrato que `CampoEtiquetado`, de modo que un
    formulario pueda tratar todos sus campos por igual.

        f = CampoFecha(page, "Fecha de adquisición", valor="01/07/2026")
        columna.controls.append(f.control)
        texto = f.value
    """

    def __init__(self, page, etiqueta: str | None, valor: str = "",
                 on_change=None, flotante: bool = False) -> None:
        self.page = page
        self._on_change = on_change
        self.campo = _estilo_campo(ft.TextField(
            value=valor or "", read_only=True, hint_text="DD/MM/AAAA",
            label=etiqueta if flotante else None,
            suffix=icono_accion(ft.Icons.CALENDAR_MONTH, "Elegir fecha",
                                self._abrir)))
        self.control = (
            self.campo if flotante or etiqueta is None
            else _bloque_etiquetado(etiqueta, self.campo, None))

    @property
    def value(self) -> str:
        return self.campo.value or ""

    @value.setter
    def value(self, v: str) -> None:
        self.campo.value = v or ""

    def _abrir(self, _e=None) -> None:
        _abrir_calendario(self.page, self.campo.value, self._elegido)

    def _elegido(self, texto: str) -> None:
        self.campo.value = texto
        _refrescar(self.campo)
        if callable(self._on_change):
            self._on_change(texto)


def _abrir_calendario(page, valor: str, al_elegir: Callable[[str], None]) -> None:
    """Abre el DatePicker de Material en español y devuelve la fecha formateada.

    Va aparte de `CampoFecha` para que la configuración del calendario —idioma,
    rango admitido, textos de los botones— viva en un solo sitio si vuelve a
    hacer falta otra caja que lo dispare.
    """
    def elegido(e) -> None:
        fecha = getattr(e.control, "value", None)
        if fecha:
            al_elegir(fmt_fecha(fecha))

    page.show_dialog(ft.DatePicker(
        value=parse_fecha(valor),
        first_date=_FECHA_MIN, last_date=_FECHA_MAX,
        locale=ft.Locale("es", "MX"),
        help_text="Selecciona la fecha", cancel_text="Cancelar",
        confirm_text="Aceptar", on_change=elegido))


# --- Campos dentro de una fila de tabla ----------------------------------
# Aquí la CAJA (borde, radio, fondo y alto) la dibujamos nosotros, y dentro va
# solo el contenido. No es un capricho: en una fila compacta ningún campo de
# Material se deja medir.
#
#   - `ft.Dropdown` IGNORA `content_padding` y `dense`: barriendo el padding de
#     2 a 10 su altura no se movía ni un píxel, mientras los TextField sí
#     encogían. La fila quedaba siempre despareja.
#   - `height` y `size_constraints` tampoco funcionan sobre campos de Material:
#     el decorador recalcula su alto por su cuenta.
#
# Con la caja propia, texto y select miden lo mismo POR CONSTRUCCIÓN: comparten
# `_caja_tabla`. Si hay que cambiar el alto, se cambia `ALTO_CAMPO_TABLA` y se
# mueven los dos a la vez. Ojo: `_ALTO_FILA` en ui/tabla_responsiva.py tiene que
# seguir siendo mayor que este valor.
ALTO_CAMPO_TABLA = 35
_RADIO_TABLA = 4
_PAD_H_TABLA = 6
_TEXTO_TABLA = 12
_ICONO_TABLA = 18


def _caja_tabla(contenido: ft.Control, ancho: int | None) -> ft.Container:
    """Caja de un campo de tabla. IDÉNTICA para texto y para select."""
    return ft.Container(
        content=contenido, width=ancho, height=ALTO_CAMPO_TABLA,
        bgcolor=ft.Colors.SURFACE_CONTAINER_LOWEST,
        border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
        border_radius=_RADIO_TABLA,
        padding=ft.Padding.symmetric(horizontal=_PAD_H_TABLA, vertical=0),
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS)


def campo_tabla_texto(*, valor: str = "", on_blur=None, on_submit=None,
                      ancho: int | None = None,
                      alineacion=ft.TextAlign.LEFT) -> ft.Container:
    """Campo de texto de una celda. `on_blur` recibe el TextField en `e.control`.

    `on_submit` es el Enter, para los campos que confirman en vez de esperar a
    que se pierda el foco (el salto de página del paginador). `alineacion`
    centra o alinea a la derecha lo que no sea texto corrido: un número dentro
    de una caja estrecha se lee mejor centrado.

    El campo devuelto es el CONTENIDO de la caja: quien necesite leer `.value`
    tiene que bajar a `caja.content`.
    """
    campo = ft.TextField(
        value=valor, on_blur=on_blur, on_submit=on_submit,
        # `collapsed` quita el decorador de Material —el que imponía su propia
        # altura—, así el alto lo manda la caja y no el control.
        collapsed=True, border=ft.InputBorder.NONE,
        content_padding=ft.Padding.all(0),
        text_size=_TEXTO_TABLA,
        # Por omisión alineado a la IZQUIERDA en horizontal: es lo que hace
        # legible una columna de datos (los valores arrancan todos en el mismo
        # punto). El CENTRADO vertical no se negocia: sin él el texto se pega al
        # borde superior de la caja.
        text_align=alineacion,
        text_vertical_align=ft.VerticalAlignment.CENTER)
    return _caja_tabla(campo, ancho)


_ALTO_LISTA_MENU = 320


def _dialogo_opciones(page, opciones: list[str], titulo: str, actual: str,
                      al_elegir: Callable[[str], None]) -> None:
    """Menú de opciones con buscador (patrón de ui/selector_insumo.py).

    Se construye AL ABRIR, no por cada celda: una tabla de 25 filas crearía 25
    diálogos con sus listas si se armaran por adelantado.

    El armazón —fundido, Esc, botón de cierre— lo aporta `Modal`; aquí solo va
    el buscador y la lista.
    """
    lista = ft.Column(spacing=2, scroll=ft.ScrollMode.AUTO, tight=True,
                      height=_ALTO_LISTA_MENU)
    # Lo que la lista muestra AHORA. Se guarda aparte para que Enter sepa cuál
    # es la primera coincidencia sin tener que releer los controles pintados.
    resultados: list[str] = list(opciones)

    def pintar(filtro: str = "") -> None:
        f = (filtro or "").strip().lower()
        resultados[:] = [o for o in opciones if f in o.lower()]
        encontrados = resultados
        if encontrados:
            lista.controls = [
                puntero_mano(ft.Container(
                    ft.Text(o, size=13,
                            weight=ft.FontWeight.BOLD if o == actual else None,
                            color=ft.Colors.PRIMARY if o == actual else None),
                    # Holgura extra a la derecha: la lista llega al filo del
                    # modal para que su barra de scroll quede ahí, así que el
                    # texto necesita apartarse para no quedar debajo.
                    padding=ft.Padding.only(left=12, right=12 + GUTTER_SCROLL,
                                            top=8, bottom=8),
                    border_radius=RADIO, ink=True,
                    on_click=lambda _e, o=o: _elegir(o)))
                for o in encontrados]
        else:
            lista.controls = [ft.Container(
                ft.Text("Sin coincidencias.", size=12,
                        color=ft.Colors.ON_SURFACE_VARIANT), padding=12)]
        _refrescar(lista)

    campo_buscar = _estilo_campo(ft.TextField(
        hint_text="Buscar… (Enter elige el primero)", autofocus=True,
        prefix_icon=ft.Icons.SEARCH,
        on_change=lambda e: pintar(e.control.value)))

    modal = Modal(page, titulo, ancho=420)
    modal.cuerpo.spacing = GAP_MD   # el STRETCH ya lo trae `Modal.cuerpo`
    modal.cuerpo.controls = [campo_buscar, lista]

    def _elegir(opcion: str) -> None:
        modal.cerrar()
        al_elegir(opcion)

    # Enter elige la primera coincidencia. Se asigna aquí y no al construir el
    # campo porque `_elegir` todavía no existía en ese punto. Sin coincidencias
    # no hace nada, en vez de cerrar sin elegir.
    campo_buscar.on_submit = lambda _e: _elegir(resultados[0]) if resultados else None

    pintar()
    modal.abrir()


class SelectCompacto:
    """Select de `ALTO_CAMPO_TABLA` px con buscador, en vez de los 56 de Material.

    Nació para las celdas de la tabla —donde ningún campo de Material se deja
    medir (ver la nota de arriba)— pero sirve igual en cualquier barra donde un
    campo de alto completo sobra: los filtros del tablero, por ejemplo. Es la
    ÚNICA forma de tener un select bajo en este proyecto; `dense=True` sobre un
    `ft.Dropdown` no hace absolutamente nada.

    Expone `.control` y `.value`, el mismo contrato que `CampoEtiquetado` y
    `CampoFecha`, para poder tratarlo igual desde un formulario.
    """

    def __init__(self, page, opciones: Iterable[str], *, valor: str = "",
                 ancho: int | None = None, titulo: str = "Seleccionar",
                 on_change=None, disabled: bool = False) -> None:
        self.page = page
        self.titulo = titulo
        self._on_change = on_change
        self._opciones = list(opciones)

        # Alineado a la IZQUIERDA en horizontal; el centrado vertical lo aporta
        # el Row del disparador.
        self._etiqueta = ft.Text(valor or "", size=_TEXTO_TABLA, expand=True,
                                 text_align=ft.TextAlign.LEFT, no_wrap=True,
                                 overflow=ft.TextOverflow.ELLIPSIS)
        self.control = _caja_tabla(
            ft.Row([self._etiqueta,
                    ft.Icon(ft.Icons.ARROW_DROP_DOWN, size=_ICONO_TABLA,
                            color=ft.Colors.ON_SURFACE_VARIANT)],
                   spacing=0, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ancho)
        self.control.on_click = self._abrir
        self.control.ink = True
        # El cursor se guarda aparte porque el select se apaga y se enciende: es
        # el `GestureDetector` que `puntero_mano` deja como contenido de la caja.
        self._cursor = puntero_mano(self.control).content
        self.disabled = disabled

    # ------------------------------------------------------------ contenido
    @property
    def value(self) -> str:
        return self._etiqueta.value or ""

    @value.setter
    def value(self, v: str) -> None:
        self._etiqueta.value = v or ""

    @property
    def disabled(self) -> bool:
        return bool(self.control.disabled)

    @disabled.setter
    def disabled(self, v: bool) -> None:
        # `disabled` en un Container solo bloquea el evento: no lo apaga a la
        # vista. La opacidad es lo que comunica que el control no aplica.
        self.control.disabled = bool(v)
        self.control.opacity = 0.5 if v else 1.0
        # `GestureDetector.mouse_cursor` es un valor suelto, sin estados como el
        # de los botones, así que el apagado se hace a mano: un select bloqueado
        # que ofrece la mano promete un menú que no va a abrirse.
        self._cursor.mouse_cursor = (ft.MouseCursor.BASIC if v
                                     else ft.MouseCursor.CLICK)

    def set_opciones(self, opciones: Iterable[str], valor: str = "") -> None:
        """Repuebla el menú (filtros en cascada) y fija el valor mostrado."""
        self._opciones = list(opciones)
        self.value = valor

    # -------------------------------------------------------------- interno
    def _abrir(self, _e=None) -> None:
        if self.page is not None:
            _dialogo_opciones(self.page, self._opciones, self.titulo,
                              self.value, self._elegir)

    def _elegir(self, opcion: str) -> None:
        self.value = opcion
        _refrescar(self._etiqueta)
        if callable(self._on_change):
            # Se imita la forma de un evento de Flet (`e.control.value`) para que
            # quien lo use no note que por dentro no es un control nativo.
            self._on_change(SimpleNamespace(control=SimpleNamespace(value=opcion)))



def campo_tabla_opciones(opciones: Iterable[str], *, valor: str | None = None,
                         on_change=None, ancho: int | None = None,
                         page=None,
                         titulo: str = "Seleccionar") -> ft.Container:
    """Select de una celda, con la MISMA caja que `campo_tabla_texto`.

    No usa `ft.Dropdown` (ver nota arriba). La caja es el disparador y al
    pulsarla abre un diálogo con BUSCADOR, porque los catálogos son largos
    (~58 empresas) y una lista sin filtrar no es práctica.

    `on_change` recibe un evento con `e.control.value`, igual que un campo
    nativo de Flet, para que las pantallas no noten la diferencia.

    Es un atajo sobre `SelectCompacto` que devuelve solo el control: en una celda
    de tabla el valor se lee por el `on_change` de la fila, no consultando el
    campo, así que no hace falta conservar el objeto.
    """
    return SelectCompacto(page, opciones, valor=valor or "", ancho=ancho,
                          titulo=titulo, on_change=on_change).control


def buscador(hint: str, on_submit=None, width: int | None = 420, *,
             expand: bool = False, autofocus: bool = False) -> ft.TextField:
    """Campo de búsqueda con lupa (sin etiqueta: el hint ya lo explica).

    `expand=True` para que ocupe el ancho disponible (en diálogos); en ese caso
    el `width` se ignora.
    """
    return _estilo_campo(ft.TextField(
        hint_text=hint, width=None if expand else width, expand=expand or None,
        prefix_icon=ft.Icons.SEARCH, autofocus=autofocus, on_submit=on_submit))


# --- Contenedores --------------------------------------------------------
def tarjeta_seccion(contenido: ft.Control, *, padding: int = GAP_LG) -> ft.Container:
    """Tarjeta blanca con borde y sombra suave: agrupa un bloque de la pantalla."""
    return ft.Container(
        content=contenido,
        bgcolor=ft.Colors.SURFACE_CONTAINER_LOWEST,
        border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
        border_radius=RADIO,
        padding=padding,
        shadow=SOMBRA_N1,
    )


# --- Modales -------------------------------------------------------------
_FADE_MS = 140
# Reparto del alto de un modal. El cuerpo es lo ÚNICO que se recorta: encabezado
# y pie deben verse siempre, porque el pie es donde viven Guardar y Cancelar.
INSET_MODAL = 24             # margen del AlertDialog contra el borde de la ventana
_ALTO_CROMO_MODAL = 220      # encabezado + pie
_MARGEN_MODAL = 48           # respiro extra, para no depender de la estimación
_ALTO_CUERPO_MIN = 180       # por debajo de esto el formulario ya no se usa


def ancho_util_modal(page) -> int:
    """Ancho MÁXIMO que puede tener un modal sin salirse de la ventana.

    Pedir más no lo ensancha: el `AlertDialog` lo recorta contra el borde, y lo
    que queda fuera no se alcanza con ningún scroll —el recorte ocurre por
    encima de cualquier área desplazable del contenido—. Vive aquí, junto al
    `INSET_MODAL` que descuenta, para que quien calcule un ancho no tenga que
    saberse el margen del diálogo.

    Cero si la ventana todavía no se ha medido; quien llame decide su respaldo.
    """
    ventana = getattr(page, "width", None) or 0
    return max(0, round(ventana) - 2 * INSET_MODAL)


class Modal:
    """Armazón de diálogo del proyecto (ver ejemplos/modal_insumos.html).

    Encabezado con título, subtítulo y botón de cierre; cuerpo con scroll; pie
    con las acciones a la derecha sobre fondo hundido. Entra y sale con
    FUNDIDO y se cierra con **Esc** o con la **X**.

    `AlertDialog` no expone animaciones, así que se deja transparente y la
    tarjeta visible —fondo, borde, radio, sombra— la dibuja el Container
    animado de dentro: el fundido abarca así todo lo visible.

    Uso:
        self.modal = Modal(page, "Título", subtitulo="…", acciones=[...])
        self.modal.cuerpo.controls = [...]
        self.modal.abrir()
    """

    def __init__(self, page, titulo: str, *, subtitulo: str = "",
                 ancho: int = 760, alto_cuerpo: int | None = None,
                 acciones: list | None = None, al_cerrar=None) -> None:
        self.page = page
        self._al_cerrar = al_cerrar
        self._alto_cuerpo = alto_cuerpo   # deseado; se recorta si no cabe

        self._txt_titulo = ft.Text(titulo, size=24, weight=ft.FontWeight.W_600,
                                   color=ft.Colors.ON_SURFACE)
        # El subtítulo va en MAYÚSCULAS con interletraje amplio: identifica el
        # registro que se está editando sin competir con el título.
        self._txt_subtitulo = ft.Text(
            (subtitulo or "").upper(), theme_style=ft.TextThemeStyle.LABEL_LARGE,
            color=ft.Colors.ON_SURFACE_VARIANT, visible=bool(subtitulo))

        encabezado = ft.Container(
            ft.Row(
                [ft.Column([self._txt_titulo, self._txt_subtitulo],
                           spacing=4, tight=True, expand=True),
                 ft.IconButton(ft.Icons.CLOSE, icon_size=20,
                               tooltip="Cerrar (Esc)",
                               on_click=lambda _e: self.cerrar())],
                vertical_alignment=ft.CrossAxisAlignment.START),
            padding=GAP_LG,
            border=ft.Border(bottom=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)))

        # Dos capas a propósito: `self.cuerpo` es lo que llena el llamador, y va
        # dentro de un Container con margen derecho. La barra de scroll se dibuja
        # en el borde de `self._scroll`, así que ese margen es el hueco que evita
        # que la barra se monte sobre los campos de la columna derecha.
        # STRETCH: sin esto los hijos toman su ancho NATURAL y cualquier
        # `expand` que venga más abajo (rejillas, campos) se queda sin ancho
        # contra el que expandirse.
        self.cuerpo = ft.Column(
            spacing=GAP_LG + GAP_SM, tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
        self._scroll = ft.Column(
            # Reserva para la barra de scroll (12px) MÁS una holgura, para que los
            # campos de la columna derecha no queden pegados a la barra.
            [ft.Container(self.cuerpo,
                          padding=ft.Padding.only(right=GUTTER_SCROLL + GAP_MD))],
            scroll=ft.ScrollMode.AUTO, height=alto_cuerpo, spacing=0,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
        cuerpo_env = ft.Container(
            self._scroll,
            padding=ft.Padding.only(left=GAP_LG, top=GAP_LG, right=GAP_SM,
                                    bottom=GAP_LG))

        self._pie = ft.Container(
            ft.Row(acciones or [], spacing=GAP_MD,
                   alignment=ft.MainAxisAlignment.END,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.Padding.symmetric(horizontal=GAP_LG, vertical=GAP_MD),
            bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
            border=ft.Border(top=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)),
            visible=bool(acciones))

        self.tarjeta = ft.Container(
            content=ft.Column([encabezado, cuerpo_env, self._pie],
                              spacing=0, tight=True),
            width=ancho,
            bgcolor=ft.Colors.SURFACE_CONTAINER_LOWEST,
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=RADIO + 4,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            shadow=SOMBRA_N1,
            opacity=0,
            animate_opacity=ft.Animation(_FADE_MS, ft.AnimationCurve.EASE_IN_OUT))

        self.dialogo = ft.AlertDialog(
            modal=True, bgcolor=ft.Colors.TRANSPARENT, elevation=0,
            content_padding=ft.Padding.all(0), content=self.tarjeta,
            # Explícito: el cálculo de alto lo descuenta, y el de Material
            # (40x24) no está garantizado entre versiones.
            inset_padding=ft.Padding.all(INSET_MODAL),
            barrier_color=ft.Colors.with_opacity(0.4, ft.Colors.BLACK),
            on_dismiss=lambda _e: self._soltar_teclado())
        self._tecla_previa = None

    # ------------------------------------------------------------ contenido
    @property
    def subtitulo(self) -> str:
        return self._txt_subtitulo.value or ""

    @subtitulo.setter
    def subtitulo(self, texto: str) -> None:
        self._txt_subtitulo.value = (texto or "").upper()
        self._txt_subtitulo.visible = bool(texto)

    def set_acciones(self, acciones: list) -> None:
        self._pie.content.controls = acciones
        self._pie.visible = bool(acciones)

    # -------------------------------------------------------------- ciclo
    def _ajustar_alto(self) -> None:
        """Recorta el cuerpo a lo que quepa en la ventana.

        Sin esto el modal crece más que la pantalla y, como el pie va al final,
        los botones de acción quedan fuera de vista sin forma de alcanzarlos.
        Se calcula al ABRIR porque es cuando `page.height` ya es real.
        """
        if self._alto_cuerpo is None:
            return
        alto_pagina = getattr(self.page, "height", None) or 800
        # Todo lo que NO es cuerpo se descuenta explícitamente, en vez de
        # aproximarlo con un porcentaje del alto de la ventana.
        disponible = (alto_pagina - 2 * INSET_MODAL - _ALTO_CROMO_MODAL
                      - _MARGEN_MODAL)
        self._scroll.height = max(_ALTO_CUERPO_MIN,
                                  min(self._alto_cuerpo, disponible))

    def abrir(self) -> None:
        self._ajustar_alto()
        # `page.on_keyboard_event` es un slot ÚNICO: se guarda el anterior y se
        # restaura al cerrar, para no dejar el teclado secuestrado.
        self._tecla_previa = getattr(self.page, "on_keyboard_event", None)
        self.page.on_keyboard_event = self._al_teclear
        self.tarjeta.opacity = 0
        self.page.show_dialog(self.dialogo)
        self.page.run_task(self._aparecer)

    def cerrar(self) -> None:
        # Cierre SÍNCRONO e inmediato. `pop_dialog()` (Flet 0.86) retira SIEMPRE el
        # diálogo de ARRIBA, no uno concreto; por eso se retira hacia abajo hasta
        # sacar EL de este modal, descartando lo que haya quedado apilado encima
        # (p. ej. un DatePicker o un sub-selector que no se auto-retiró, o un
        # SnackBar). Sin esto quedaba un barrier gris pegado bloqueando la app.
        self._soltar_teclado()
        self.tarjeta.opacity = 0
        try:
            for _ in range(10):
                d = self.page.pop_dialog()
                if d is None or d is self.dialogo:
                    break
        except Exception:  # noqa: BLE001 — cierre tolerante (ya cerrado, etc.)
            pass
        if callable(self._al_cerrar):
            self._al_cerrar()

    async def _aparecer(self) -> None:
        # Un respiro para que el cliente pinte el primer fotograma en opacidad
        # 0; sin él, el salto a 1 ocurre antes de que haya algo que animar.
        await asyncio.sleep(0.02)
        self.tarjeta.opacity = 1
        _refrescar(self.tarjeta)

    def _soltar_teclado(self) -> None:
        self.page.on_keyboard_event = self._tecla_previa

    def _al_teclear(self, e) -> None:
        if e.key == "Escape":
            self.cerrar()

    def reajustar(self, ancho: int | None = None) -> None:
        """Vuelve a encajar el modal en la ventana. Idempotente.

        Alto y ancho se fijan en PÍXELES —al abrir y al construir—, así que un
        redimensionado de la ventana los deja con la medida de antes: el modal
        queda más grande que su hueco y el `AlertDialog` lo recorta, dejando
        fuera contenido inalcanzable.

        No se resuelve solo porque `page.on_resize` es un slot único que
        multiplexa el shell; quien tenga un modal abierto lo llama desde su
        `_on_resize` (ver el contrato de pantalla en CLAUDE.md).
        """
        if ancho:
            self.tarjeta.width = ancho
        self._ajustar_alto()
        self.refrescar()

    def refrescar(self) -> None:
        _refrescar(self.tarjeta)


# --- Listados de búsqueda ------------------------------------------------
def fila_resultado(clave: str, titulo: str, subtitulo: str = "", *,
                   on_click=None, resaltado: bool = False) -> ft.Container:
    """Fila de un listado de búsqueda: clave, título y detalle.

    La fila ENTERA es pulsable, como el menú de opciones de la tabla: da un
    área de clic mucho mayor que un botón "Elegir" al final y evita el
    recorrido de ratón hasta la esquina derecha.

    El relleno derecho incluye `GUTTER_SCROLL` porque estos listados llegan al
    filo del contenedor para que su barra de scroll no se monte sobre el texto.
    """
    return puntero_mano(ft.Container(
        ft.Row(
            [ft.Container(ft.Text(clave, size=12, weight=ft.FontWeight.BOLD,
                                  color=ft.Colors.PRIMARY_CONTAINER,
                                  no_wrap=True), width=64),
             ft.Column(
                 [ft.Text(titulo, size=13, no_wrap=True,
                          overflow=ft.TextOverflow.ELLIPSIS,
                          weight=ft.FontWeight.BOLD if resaltado else None),
                  ft.Text(subtitulo or "—", size=11, color=ft.Colors.ON_SURFACE_VARIANT,
                          no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS)],
                 spacing=0, tight=True, expand=True)],
            spacing=GAP_SM, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        padding=ft.Padding.only(left=12, right=12 + GUTTER_SCROLL,
                                top=8, bottom=8),
        border_radius=RADIO, ink=True, on_click=on_click))


def lista_resultados(alto: int = _ALTO_LISTA_MENU) -> ft.Column:
    """Columna con scroll para las filas de un listado de búsqueda."""
    return ft.Column(spacing=2, scroll=ft.ScrollMode.AUTO, tight=True, height=alto)


# --- Secciones de formulario ---------------------------------------------
def seccion_formulario(titulo: str, icono, campos: list,
                       columnas: int = 2) -> ft.Control:
    """Bloque de formulario: encabezado con ícono + rejilla de campos.

    Los campos se reparten en filas de `columnas`, todos con el mismo ancho
    (`expand`). La última fila se rellena con huecos vacíos para que sus campos
    no se estiren de más.
    """
    filas = []
    for i in range(0, len(campos), columnas):
        grupo = campos[i:i + columnas]
        # Column con STRETCH, no Container: el Container da al hijo su ancho
        # natural y el Dropdown se quedaba más angosto que su celda. `expand`
        # reparte el ancho entre columnas; `STRETCH` hace que el campo lo llene.
        celdas = [ft.Column([c], expand=True, tight=True,
                            horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
                  for c in grupo]
        celdas += [ft.Container(expand=True)
                   for _ in range(columnas - len(grupo))]
        filas.append(ft.Row(celdas, spacing=GAP_MD,
                            vertical_alignment=ft.CrossAxisAlignment.START))
    return ft.Column(
        [ft.Row([ft.Icon(icono, size=20, color=ft.Colors.PRIMARY_CONTAINER),
                 ft.Text(titulo, theme_style=ft.TextThemeStyle.LABEL_LARGE,
                         color=ft.Colors.PRIMARY_CONTAINER)],
                spacing=GAP_SM, vertical_alignment=ft.CrossAxisAlignment.CENTER),
         *filas],
        spacing=GAP_MD, tight=True,
        # STRETCH también aquí: las filas de campos deben ocupar el ancho de la
        # sección, no el de su contenido.
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH)


# --- Pestañas ------------------------------------------------------------
class Pestanas:
    """Control segmentado: una pista gris con la pestaña activa en relieve.

    Uso:
        tabs = Pestanas([("todos", "Todos", ft.Icons.LIST_ALT), …], al_cambiar=cb)
        fila = tabs.control
        tabs.set_conteo("todos", 42)     # actualiza el número entre paréntesis
        tabs.activa                      # clave de la pestaña seleccionada
    """

    def __init__(self, definiciones: list[tuple], al_cambiar: Callable[[str], None],
                 activa: str | None = None) -> None:
        self._al_cambiar = al_cambiar
        self.activa = activa or (definiciones[0][0] if definiciones else "")
        self._items: dict[str, dict] = {}
        botones = []
        for clave, texto, icono in definiciones:
            ico = ft.Icon(icono, size=18)
            txt = ft.Text(texto, theme_style=ft.TextThemeStyle.LABEL_LARGE,
                          no_wrap=True)
            cont = ft.Container(
                content=ft.Row([ico, txt], spacing=GAP_SM, tight=True,
                               vertical_alignment=ft.CrossAxisAlignment.CENTER),
                padding=ft.Padding.symmetric(horizontal=PAD_H, vertical=GAP_SM),
                border_radius=RADIO,
                on_click=lambda _e, c=clave: self._elegir(c),
                animate=ft.Animation(160, ft.AnimationCurve.EASE_OUT),
            )
            self._items[clave] = {"cont": cont, "ico": ico, "txt": txt,
                                  "base": texto}
            botones.append(puntero_mano(cont))   # muta y devuelve `cont`

        # La PISTA es lo que distingue a un control segmentado de unos botones
        # sueltos: agrupa visualmente las opciones y hace de fondo del activo.
        self.control = ft.Container(
            content=ft.Row(botones, spacing=4, tight=True),
            bgcolor=ft.Colors.SURFACE_CONTAINER,
            padding=4,
            border_radius=RADIO + 4,
        )
        self._pintar()

    def _elegir(self, clave: str) -> None:
        if clave == self.activa:
            return
        self.activa = clave
        self._pintar()
        self._actualizar()
        self._al_cambiar(clave)

    def _pintar(self) -> None:
        for clave, item in self._items.items():
            act = clave == self.activa
            color = ft.Colors.ON_PRIMARY if act else ft.Colors.ON_SURFACE_VARIANT
            item["ico"].color = color
            item["txt"].color = color
            item["txt"].weight = ft.FontWeight.BOLD if act else ft.FontWeight.W_500
            # Mismo criterio que `boton_primario`: PRIMARY/ON_PRIMARY es el par
            # que Material garantiza legible en claro Y en oscuro.
            item["cont"].bgcolor = ft.Colors.PRIMARY if act else None

    def set_conteo(self, clave: str, n: int) -> None:
        item = self._items.get(clave)
        if item:
            item["txt"].value = f"{item['base']} ({n})"

    def _actualizar(self) -> None:
        _refrescar(self.control)
