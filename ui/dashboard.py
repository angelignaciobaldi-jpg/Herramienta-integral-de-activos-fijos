"""Pantalla "Dashboard activos fijos".

Disposición adaptada del mockup en `ejemplos/code.html`: las tarjetas
(ui/tarjetas.py) se colocan en la rejilla bento (ui/rejilla.py) declarando cuántas
columnas y filas abarca cada una.

Los datos salen del endpoint `/api/activos-fijos/dashboard` a través de
`core/dashboard.py`. La consulta se dispara al abrir la pantalla y cada vez que se
pulsa «Buscar»; nunca al mover un filtro, para no lanzar una ronda de red por cada
combo que se toca.
"""

from __future__ import annotations

import asyncio
import re
from typing import NamedTuple

import flet as ft

from core import api, catalogos, entorno
from core import actividad as actividad_api
from core import dashboard as datos_api
from core import inversion as inversion_api
from core import listado as listado_api
from core import paginado as paginado_api
from core.empresas import ID_POR_EMPRESA, NOMBRES_EMPRESAS
from core.tipos_activo import TIPOS_ACTIVO
from ui.componentes import (GAP_LG, GAP_MD, GAP_SM, GUTTER_SCROLL, RADIO, Modal,
                            SelectCompacto, ancho_util_modal, boton_herramienta,
                            boton_primario_icono, boton_secundario,
                            campo_tabla_texto, tarjeta_seccion)
from ui.comun import CENTRO, GRIS, NARANJA, ROJO
from ui import tema
from ui.rejilla import Bloque, Rejilla
from ui.tabla_responsiva import (DER, IZQ, Cabecera, ColumnaTabla, FilaDatos,
                                 SegmentoCabecera, TablaResponsiva)
from ui.tarjetas import (Esqueleto, TarjetaActividad, TarjetaRanking,
                         TarjetaTotalActivos, TarjetaValor)

# Ícono por tipo de activo. Vive aquí y no en core/tipos_activo.py porque
# `ft.Icons` es de Flet y core/ no importa Flet (ver CLAUDE.md). El NOMBRE de cada
# tipo lo manda el servicio; aquí solo se decide con qué se dibuja.
_ICONO_TIPO: dict[int, str] = {
    1: ft.Icons.DOMAIN,             # Edificios
    2: ft.Icons.LANDSCAPE,          # Terrenos
    3: ft.Icons.SETTINGS_SUGGEST,   # Maquinaria y Equipo
    4: ft.Icons.COMMUTE,            # Vehículos Utilitarios
    5: ft.Icons.LOCAL_SHIPPING,     # Vehículos Pesados
    6: ft.Icons.CHAIR,              # Mobiliario y equipo de Oficina
    7: ft.Icons.COMPUTER,           # Equipo informático
    8: ft.Icons.SMARTPHONE,         # Celulares
    9: ft.Icons.DIRECTIONS_BOAT,    # Embarcaciones
    10: ft.Icons.FLIGHT,            # Aeronaves
    11: ft.Icons.STORAGE,           # Tanques
    12: ft.Icons.COPYRIGHT,         # Activos Intangibles
}
_ICONO_SIN_TIPO = ft.Icons.HELP_OUTLINE

# Ícono por tipo de MOVIMIENTO de la bitácora. El servicio devuelve la CLAVE del
# movimiento en `tipo` y es aquí donde se decide con qué se dibuja: si mandara el
# nombre del ícono, cualquier cambio de estilo obligaría a tocar el backend, y
# además en Flet 0.85 los `ft.Icons` son enteros, no cadenas.
_ICONO_MOVIMIENTO: dict[str, str] = {
    "registro":             ft.Icons.ADD_CIRCLE,            # alta inicial
    "asignacion":           ft.Icons.ASSIGNMENT_IND,
    "asignacion_computo":   ft.Icons.COMPUTER,
    "reasignacion":         ft.Icons.SWAP_HORIZ,
    "modificacion":         ft.Icons.EDIT_NOTE,
    "salida":               ft.Icons.LOGOUT,
    "determinacion":        ft.Icons.GAVEL,
    # Activar/inactivar comparten familia de ícono a propósito: son la misma
    # acción en dos sentidos y el interruptor lo dice sin leer el texto.
    "activacion":           ft.Icons.TOGGLE_ON,
    "inactivacion":         ft.Icons.TOGGLE_OFF,
    "cambio_sucursal_auto": ft.Icons.SYNC_ALT,
    # Ciclo de la carta responsiva: generar, cargar, y su resolución.
    "carta_generada":       ft.Icons.POST_ADD,
    "carta_cargada":        ft.Icons.UPLOAD_FILE,
    "carta_autorizada":     ft.Icons.ASSIGNMENT_TURNED_IN,
    "carta_rechazada":      ft.Icons.THUMB_DOWN,
    "carta_cancelada":      ft.Icons.CANCEL,
}
# Un movimiento que el SIPP agregue mañana no debe romper la pantalla.
_ICONO_MOVIMIENTO_OTRO = ft.Icons.HISTORY

# Rótulos "sin filtro" de cada combo. Son también el valor por defecto, así que
# se comparan por texto al armar el ámbito de la consulta.
_TODAS_EMPRESAS = "Todas las empresas"
_TODAS_SUCURSALES = "Todas las sucursales"
_TODOS_TIPOS = "Todos los tipos"

# Catálogo del filtro de tipo: el oficial del SIPP más el 0, que no está en
# `TIPOS_ACTIVO` porque no es un tipo real sino la ausencia de uno (los activos
# con `id_TipoActivoFijo` nulo). Se compone aquí y no en core/tipos_activo.py
# para no colar un id inventado en el combo del alta, que sí manda al RPA.
_TIPOS_FILTRO: dict[int, str] = {datos_api.ID_SIN_TIPO: "Sin identificar",
                                 **TIPOS_ACTIVO}

_ANCHO_FILTRO = 192   # w-48 del mockup

# Marcador mientras no hay dato: ni un cero (que se leería como una cifra real ya
# consultada) ni un hueco en blanco (que parecería un error de pintado).
_SIN_DATO = "—"

# Aviso cuando una tarjeta no tiene nada que listar. El mismo texto para el podio
# y para el valor porque comparten causa: si no hay activos, no hay ni ranking ni
# importe que calcular.
_SIN_ACTIVOS = "No hay activos registrados"

# Moneda del importe. Va en el renglón de unidad de la tarjeta, DEBAJO de la
# cifra, y no pegada a ella: el ancho es el recurso escaso —a 960px la cifra ya
# encoge para caber— y el alto sobra. Además así queda fuera del conteo animado,
# que solo debe mover números.
_MONEDA = "MXN"

# Aviso al pedir el detalle de una tarjeta que no tiene nada que enseñar. Si la
# previsualización está vacía, el detallado también lo estaría.
_SIN_INFO = "No hay información para mostrar"

# Ancho de los modales, como fracción de la ventana. Es proporcional y no fijo
# porque lo que en una pantalla grande sobra, en una de 960 se aprieta.
#
# Cada modal declara el suyo: el que MANDA es su contenido. El de inversión son
# tres columnas y a pantalla casi completa quedaba desangelado; el de actividad
# son nueve y necesita cuanto haya. Con una sola fracción para todos, ensanchar
# uno ensanchaba el otro.
_FRACCION_MODAL_ESTRECHO = 0.75
_FRACCION_MODAL_ANCHO = 0.95
# Respaldo para cuando todavía no se ha medido la ventana (arranque, pruebas).
# NO es un piso: por debajo de él el modal sigue encogiendo con la ventana, que
# es lo que deja sitio a la barra horizontal de la tabla. Forzarlo como mínimo
# hacía que el modal se saliera y lo recortara el diálogo.
_ANCHO_MODAL_RESPALDO = 900
_ALTO_CUERPO_MODAL = 420
# Esqueleto mientras el modal trae su contenido: dibuja su MISMA disposición
# —dos bloques de resumen arriba y la tabla debajo— en vez de una pila de
# barras iguales. Así lo que aparece al responder ocupa el sitio que ya estaba
# marcado, y la ventana no se reacomoda delante del usuario.
_ALTO_ESQ_RESUMEN = 96    # rótulo + cifra + nota, más el relleno de la tarjeta
_ALTO_ESQ_TABLA = 260     # lo que queda del cuerpo bajo los dos bloques
# Forma del esqueleto de cada modal: `(columnas, alto)` por bloque.
_FORMA_ESQ_INVERSION = ((2, _ALTO_ESQ_RESUMEN), (1, _ALTO_ESQ_TABLA))
# Los dos listados paginados —bitácora e inventario— comparten forma: una sola
# barra alta donde va la tabla.
_FORMA_ESQ_LISTADO = ((1, _ALTO_ESQ_TABLA + _ALTO_ESQ_RESUMEN),)

# Encabezado del detalle de inversión: los dos grupos que separa el SP.
_GRUPOS_INVERSION = (("Habilitados", True), ("Inhabilitados", False))
# Acentos de cada grupo. Viven en ui/tema.py, que es la fuente única del color.
_ACENTO_VIGENTE = tema.VERDE_DINERO
_ACENTO_BAJA = tema.AMBAR_BAJA
# Hilo de aire entre el contenido y los filos del cuerpo del modal. Es un PADDING
# y no una fracción del ancho: la fracción obligaba a fijar un ancho en píxeles a
# cada bloque, y ese número se quedaba viejo en cuanto la ventana cambiaba de
# tamaño. Con relleno, los bloques son fluidos y siguen al modal solos.
_AIRE_CONTENIDO = 4
# Respiro a la derecha de las columnas numéricas, para que el importe no acabe
# pegado al filo de la tabla.
_PAD_DER_TABLA = 12
# Rótulo del conteo de activos cuyo costo calculado da cero. No es «sin
# inversión»: la inversión existe, lo que falta es el dato.
_SIN_COSTO = "sin costo registrado"

# Tamaños de página que ofrece el detalle de actividad. El rótulo lleva la
# unidad dentro —«25 / pág.»— para no gastar sitio del pie en una etiqueta
# aparte: un select que solo dijera «25» no explicaría de qué.
_TAMANOS_PAGINA = (10, 25, 50, 100)
_ANCHO_SELECT_PAGINA = 104
# Caja del salto de página. Da para cuatro dígitos, que es más de lo que llega
# a hacer falta con los tamaños de página de arriba.
_ANCHO_CAMPO_PAGINA = 56

# Conteo animado de los totales al cargar. Por debajo del segundo a propósito:
# más tiempo y deja de leerse como un remate para convertirse en una espera.
_CONTEO_SEGUNDOS = 0.8
_CONTEO_FPS = 25

# El endpoint de sucursales EXIGE la empresa, así que el combo permanece apagado
# mientras no haya una elegida. Se deja a la vista en vez de ocultarlo: un control
# que aparece y desaparece hace saltar de sitio al resto de la barra.
_AVISO_SUCURSAL = "Elige primero una empresa para ver sus sucursales."


def _desglose_visible(desglose: list[tuple],
                      filtrado: int | None) -> list[tuple]:
    """Recorta el desglose al tipo filtrado.

    El SP arma esa lista con un `UNION`, y el renglón de «sin identificar» entra
    SIEMPRE, se filtre por el tipo que se filtre. Sin recortar aquí, pedir un
    solo tipo pintaba dos renglones: el pedido y ese acompañante.

    Si el tipo filtrado no aparece —el servicio puede omitir los que están en
    cero— se sintetiza su renglón: mejor un «0» explícito que un panel vacío que
    parece un error de carga.
    """
    if filtrado is None:
        return desglose
    solo = [d for d in desglose if d[0] == filtrado]
    if solo:
        return solo
    return [(filtrado, _TIPOS_FILTRO.get(filtrado, "Sin identificar"), 0)]


def _filas_por_tipo(desglose: list[tuple], filtrado: int | None = None) -> list[tuple]:
    """Convierte el desglose del servicio en las filas de la tarjeta.

    `desglose` es [(id_tipo, nombre, total), ...] y el nombre ya viene resuelto
    del servidor, así que aquí solo se elige el ícono por id.

    Con `filtrado` se resalta ese tipo y se atenúan los demás. Se compara contra
    `None` y no por verdad: el 0 («Sin identificar») es un filtro legítimo.
    """
    return [(_ICONO_TIPO.get(id_tipo, _ICONO_SIN_TIPO), nombre, f"{total:,}",
             filtrado is not None and id_tipo == filtrado,
             filtrado is not None and id_tipo != filtrado)
            for id_tipo, nombre, total in desglose]


def _nota_mes(cantidad: int, singular: str, plural: str) -> tuple[str, str]:
    """Cifra y texto de una nota de movimiento del mes.

    Se concuerda el número en vez de usar el «alta(s)» que la app emplea en los
    avisos: aquí el texto es permanente y va bajo una cifra grande, así que un
    paréntesis se lee como descuido.
    """
    return f"{cantidad:,}", f"{singular if cantidad == 1 else plural} este mes"


def _filas_actividad(eventos: list[tuple]) -> list[tuple]:
    """Convierte la actividad del servicio en las filas de la tarjeta.

    Un `tipo` fuera del catálogo no rompe nada: cae al ícono genérico de
    bitácora. La lista de movimientos del SIPP puede crecer sin que esta pantalla
    tenga que enterarse el mismo día.
    """
    return [(_ICONO_MOVIMIENTO.get(tipo, _ICONO_MOVIMIENTO_OTRO), movimiento,
             tiempo)
            for tipo, movimiento, tiempo in eventos]


# Un importe formateado, partido en lo que NO es número («$», « MXN») y lo que
# sí. El grupo del número admite dígitos y separadores, y exige empezar y acabar
# en dígito para no tragarse el símbolo de moneda.
_IMPORTE = re.compile(r"^(?P<pre>\D*?)(?P<num>\d[\d.,\s  ]*\d|\d)"
                      r"(?P<post>\D*)$")


class Importe(NamedTuple):
    """Un importe formateado, partido en su número y su formato."""

    texto: str        # el original, tal cual lo mandó el servicio
    valor: float
    prefijo: str      # "$"
    sufijo: str       # " MXN"
    agrupador: str    # ","
    decimal: str      # "."
    decimales: int


def _descomponer_importe(texto: str) -> Importe | None:
    """Saca el número de un importe ya formateado, junto con SU formato.

    Devuelve `None` si el texto no es un importe reconocible.

    Existe para poder animar el conteo de la tarjeta de valor, que a diferencia
    de los totales recibe TEXTO ya formateado por el servicio. La alternativa
    —pedirle el número crudo y formatearlo aquí— duplicaría una decisión que ya
    tomó quien lo calculó: moneda, decimales y separadores.

    Los separadores se deducen del propio texto en vez de asumir la convención
    mexicana. Si el servicio cambiara a `41.383.453,26`, reformatear a la brava
    convertiría el importe en otro: no es un detalle cosmético.

    Devolver `None` es una salida legítima y frecuente —el marcador «—», por
    ejemplo—: quien llama pinta el valor tal cual y se salta la animación, que
    es un adorno. Lo que nunca puede pasar es enseñar una cifra equivocada.
    """
    m = _IMPORTE.match((texto or "").strip())
    if not m:
        return None
    pre, num, post = m.group("pre"), m.group("num"), m.group("post")
    seps = [c for c in num if c in ".,"]
    if not seps:
        decimal = ""
    elif len(set(seps)) > 1:
        # Con dos separadores distintos, el último es siempre el decimal.
        decimal = seps[-1]
    elif len(num) - num.rfind(seps[-1]) - 1 != 3:
        # Uno solo, y no deja tres dígitos detrás: «1,5» o «1,2345» no son
        # agrupaciones.
        decimal = seps[-1]
    else:
        # Uno solo con tres dígitos detrás («1,234») es ambiguo. Se toma como
        # agrupación, que es la convención y lo normal en un importe: tres
        # decimales en dinero prácticamente no se usan.
        decimal = ""
    entero, _, frac = num.rpartition(decimal) if decimal else (num, "", "")
    agrupador = next((c for c in ".,   " if c != decimal and c in entero), "")
    digitos = re.sub(r"\D", "", entero)
    if not digitos:
        return None
    return Importe(texto=(texto or "").strip(),
                   valor=float(f"{digitos}.{frac or 0}"),
                   prefijo=pre, sufijo=post, agrupador=agrupador,
                   decimal=decimal, decimales=len(frac))


def _formatear_importe(imp: Importe, valor: float) -> str:
    """Reconstruye el importe con el formato del que salió.

    Se arma con la convención de Python —coma para agrupar, punto para decimal—
    y luego se traducen los dos caracteres a los del original, para no depender
    de un `locale` que en Windows no está garantizado.
    """
    cuerpo = f"{valor:,.{imp.decimales}f}"
    # En dos pasos con un marcador intermedio: traducir de una haría que un
    # formato con los separadores intercambiados se pisara a sí mismo.
    cuerpo = cuerpo.replace(",", "\0").replace(".", imp.decimal or "")
    return f"{imp.prefijo}{cuerpo.replace(chr(0), imp.agrupador)}{imp.sufijo}"


def _ancho_modal(page, fraccion: float = _FRACCION_MODAL_ESTRECHO) -> int:
    """Ancho de un modal: la fracción de la ventana que pida, SIN salirse de ella.

    El tope no es cosmético. Pasarse del ancho útil no ensancha el modal: el
    `AlertDialog` lo recorta contra el borde de la ventana, y ese recorte ocurre
    por encima de cualquier scroll del contenido, así que lo que queda fuera es
    inalcanzable —columnas cortadas sin barra con la que llegar a ellas—.

    Con la ventana sin medir —al arrancar, o en una prueba sin página— cae al
    respaldo: un modal de ancho cero no se vería.
    """
    util = ancho_util_modal(page)
    if util <= 0:
        return _ANCHO_MODAL_RESPALDO
    ventana = getattr(page, "width", None) or 0
    return max(1, min(round(ventana * fraccion), util))


def _ancho_contenido(modal: Modal) -> int:
    """Ancho al que llega el contenido del modal, para quien necesite un número.

    Solo lo usan las medidas de ARRANQUE —el ancho inicial de una tabla, el de
    las barras del esqueleto—, que se corrigen solas al medirse. El contenido ya
    montado NO se dimensiona con esto: es fluido (ver `_acotado`) y sigue al
    modal sin que nadie le pase un ancho.

    Del ancho del modal se descuentan los rellenos que él mismo pone —izquierda
    `GAP_LG`, derecha `GAP_SM`—, el canalón de su barra de scroll y el aire
    lateral del propio acotado.
    """
    ancho = getattr(modal.tarjeta, "width", None) or _ANCHO_MODAL_RESPALDO
    return max(1, ancho - GAP_LG - GAP_SM - GUTTER_SCROLL - 2 * _AIRE_CONTENIDO)


def _mensaje_modal(texto: str, icono, *, color: str | None = None) -> ft.Control:
    """Aviso centrado dentro de un modal (sin datos, o error al traerlos)."""
    return ft.Container(
        ft.Column(
            [ft.Icon(icono, size=36, color=color or ft.Colors.OUTLINE_VARIANT),
             ft.Text(texto, theme_style=ft.TextThemeStyle.BODY_MEDIUM,
                     color=color or ft.Colors.ON_SURFACE_VARIANT,
                     text_align=ft.TextAlign.CENTER)],
            spacing=GAP_SM, tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER),
        alignment=ft.Alignment(0, 0), padding=GAP_MD)


def _resumen_inversion(rotulo: str, resumen, acento: str) -> ft.Control:
    """Bloque de cabecera de un grupo: cuánto vale y cuántos no lo dicen.

    Las dos cifras van juntas a propósito: el importe se lee distinto sabiendo
    que hay 22 mil activos sin costo capturado detrás.
    """
    return ft.Container(
        ft.Column(
            [_rotulo_grupo(rotulo, acento),
             ft.Text(resumen.total or _SIN_DATO, size=26,
                     weight=ft.FontWeight.W_700, color=ft.Colors.ON_SURFACE,
                     no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS),
             ft.Text(f"{resumen.sin_costo:,} {_SIN_COSTO}",
                     theme_style=ft.TextThemeStyle.BODY_MEDIUM,
                     color=ft.Colors.ON_SURFACE_VARIANT, no_wrap=True,
                     overflow=ft.TextOverflow.ELLIPSIS)],
            spacing=2, tight=True),
        # El fondo se tiñe con el propio acento en vez de usar la superficie
        # neutra: es lo que de verdad separa los dos bloques de un vistazo. La
        # banda izquierda sola, sobre gris, apenas se leía.
        bgcolor=tema.tenue(acento), padding=GAP_MD,
        border_radius=RADIO, expand=True,
        border=ft.Border(left=ft.BorderSide(4, acento)))


def _total_banda(texto: str) -> ft.Text:
    """Cifra de una banda de grupo: en negrita, para separarla de sus renglones."""
    return ft.Text(texto, size=12, weight=ft.FontWeight.BOLD,
                   color=ft.Colors.ON_SURFACE, no_wrap=True)


def _rotulo_grupo(texto: str, color: str) -> ft.Text:
    return ft.Text(texto.upper(), theme_style=ft.TextThemeStyle.LABEL_MEDIUM,
                   color=color, no_wrap=True)


def _tabla_columnas_inversion() -> list:
    """Columnas del detalle de inversión.

    El tipo va CENTRADO —no a la izquierda— porque a lo ancho del modal su
    columna es bastante más larga que los nombres, y pegado al filo dejaba una
    franja muerta entre él y la cifra que le corresponde.

    Las dos numéricas llevan `padding_der`: alineadas a la derecha, su texto
    acababa justo en el borde de la tabla.
    """
    return [
        ColumnaTabla("Tipo de activo", 46, ancho_min_px=170),
        ColumnaTabla("Inversión", 33, alineacion=DER, ancho_min_px=120,
                     padding_der=_PAD_DER_TABLA),
        ColumnaTabla("Sin costo", 20, alineacion=DER, ancho_min_px=80,
                     padding_der=_PAD_DER_TABLA),
    ]


def _tabla_filas_inversion(datos) -> list:
    """Filas del detalle: una banda por grupo y un renglón por tipo."""
    filas: list = []
    for rotulo, vigente in _GRUPOS_INVERSION:
        resumen = datos.vigentes if vigente else datos.bajas
        detalle = datos.detalle_vigentes if vigente else datos.detalle_bajas
        if not detalle:
            continue
        # La banda repite los DOS totales del grupo, cada uno en su columna: son
        # la referencia contra la que se leen los renglones de abajo, y con el
        # de «sin costo» fuera de la banda había que ir a buscarlo a la cabecera
        # del modal para saber cuánto pesaba cada tipo en él.
        filas.append(Cabecera(
            [SegmentoCabecera(1, _rotulo_grupo(rotulo, ft.Colors.ON_SURFACE),
                              alineacion=CENTRO),
             SegmentoCabecera(1, _total_banda(resumen.total or _SIN_DATO),
                              alineacion=DER,
                              # Mismo respiro que las columnas numéricas, para
                              # que cada total caiga a plomo con la columna que
                              # encabeza.
                              padding=ft.Padding.only(right=_PAD_DER_TABLA)),
             SegmentoCabecera(1, _total_banda(f"{resumen.sin_costo:,}"),
                              alineacion=DER,
                              padding=ft.Padding.only(right=_PAD_DER_TABLA))],
            bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST, alto=32))
        filas.extend(
            FilaDatos([linea.nombre, linea.total or _SIN_DATO,
                       f"{linea.sin_costo:,}"])
            for linea in detalle)
        # Un separador de aire entre grupos, sin fondo: la banda siguiente ya
        # marca el corte y dos bandas pegadas se leen como una sola.
        filas.append(Cabecera([SegmentoCabecera(3, ft.Text(""))],
                              bgcolor=ft.Colors.TRANSPARENT, alto=GAP_SM))
    if filas:
        filas.pop()          # el aire sobrante del último grupo
    return filas


def _tabla_inversion(page, datos, ancho: int) -> ft.Control:
    """Detalle por tipo, con una banda por grupo.

    Los dos grupos van en UNA tabla con bandas de sección y no en dos tablas ni
    con una columna de estatus: así los tipos quedan alineados entre grupos y se
    comparan de un vistazo, que es para lo que se abre este modal.
    """
    tabla = TablaResponsiva(
        page, _tabla_columnas_inversion(),
        # El modal ya tiene su propio scroll vertical, así que la tabla no fija
        # alto: crece con las filas y es el cuerpo del modal el que se desplaza.
        # El ancho de arranque es el de su caja, no el del modal: la tabla se
        # mide sola después, pero con el del modal la primera pintada saldría
        # con las columnas anchas y se vería el reajuste.
        ancho_inicial=ancho)
    tabla.set_contenido(_tabla_filas_inversion(datos), refrescar=False)
    return tabla.control


def _acotado(contenido: ft.Control) -> ft.Control:
    """Bloque del modal con su aire lateral, a ancho FLUIDO.

    Se usa para TODOS los bloques, no solo para la tabla: con la tabla acotada y
    las tarjetas a ancho completo, sus filos quedaban desalineados por unos
    píxeles, que es de lo que más se nota en una ventana estrecha.

    No fija ancho a propósito. `Modal.cuerpo` es un `Column` con `STRETCH`, así
    que este contenedor recibe el ancho vigente del modal y se lo pasa a su
    contenido; cuando el modal cambia de tamaño, el bloque le sigue. Con un
    ancho en píxeles —como estaba— el bloque conservaba el que se calculó al
    abrir: la tabla nunca se enteraba de que la ventana había encogido, sus
    columnas no se recomputaban y su scroll horizontal no llegaba a aparecer.
    """
    return ft.Container(contenido,
                        padding=ft.Padding.symmetric(
                            horizontal=_AIRE_CONTENIDO))


def _esqueleto_modal(forma: tuple) -> tuple[list, tuple]:
    """Esqueleto con la MISMA disposición que el contenido que va a relevarlo.

    `forma` es `((columnas, alto), ...)`, un bloque por elemento. Devuelve
    `(controles, grupos)`: lo que se monta en el cuerpo y los `Esqueleto` que
    hay que pasear y detener.

    Se dibuja la disposición y no una pila de barras iguales porque un esqueleto
    genérico anuncia que algo viene, pero no QUÉ, y al llegar la respuesta el
    modal se reacomodaba entero delante del usuario.
    """
    grupos = []
    for columnas, alto in forma:
        grupo = Esqueleto(1, columnas=columnas, ancho_completo=True)
        grupo.medir(alto)
        grupo.control.visible = True
        grupo.arrancar()
        grupos.append(grupo)
    # Acotados igual que el contenido real, para que las barras caigan justo
    # donde va a caer lo que las sustituye.
    return [_acotado(g.control) for g in grupos], tuple(grupos)


def _detener(grupos: tuple) -> None:
    """Corta el brillo de todos los grupos. Idempotente, como `Esqueleto`."""
    for grupo in grupos:
        grupo.detener()


def _cuerpo_inversion(page, datos, ancho: int) -> list:
    """Contenido completo del modal: cabecera de totales y tabla de detalle."""
    return [
        _acotado(ft.Row(
            [_resumen_inversion("habilitados", datos.vigentes, _ACENTO_VIGENTE),
             _resumen_inversion("inhabilitados", datos.bajas, _ACENTO_BAJA)],
            spacing=GAP_MD)),
        _acotado(_tabla_inversion(page, datos, ancho)),
    ]


def _tabla_columnas_actividad() -> list:
    """Columnas del listado de movimientos.

    Son nueve —todo lo que manda el servicio menos `num` y `total`, que son del
    paginado y no del movimiento—, así que ninguna sobra de ancho: los textos
    largos se recortan con elipsis y llevan tooltip, que es lo que
    `TablaResponsiva` ya hace por su cuenta.

    Los porcentajes suman 100 exacto: pasarse haría aparecer scroll horizontal
    dentro de un modal que ya tiene el suyo vertical, y dos barras cruzadas en
    una ventana de 900px se vuelven incómodas.
    """
    return [
        # Empresa, sucursal y empleado CENTRADAS: sus valores son mucho más
        # cortos que la columna, y pegados al filo izquierdo dejaban una franja
        # muerta entre ellos y el dato siguiente.
        ColumnaTabla("Empresa", 10, ancho_min_px=70),
        ColumnaTabla("Sucursal", 10, ancho_min_px=70),
        ColumnaTabla("Empleado", 17, ancho_min_px=110),
        ColumnaTabla("Fecha", 8, ancho_min_px=68),
        ColumnaTabla("Etiqueta", 9, ancho_min_px=70),
        ColumnaTabla("Serie", 11, ancho_min_px=80),
        ColumnaTabla("Activo", 15, alineacion=IZQ, ancho_min_px=100),
        ColumnaTabla("Observaciones", 10, alineacion=IZQ, ancho_min_px=80),
        ColumnaTabla("Precio", 7, alineacion=DER, ancho_min_px=60,
                     padding_der=_PAD_DER_TABLA),
    ]


def _tabla_actividad(page, pagina, ancho: int) -> ft.Control:
    """Listado de movimientos de UNA página.

    Con `tooltips`: nueve columnas en el ancho de un modal recortan casi todo, y
    el criterio normal —globo solo si no cabe— depende de una estimación de
    píxeles por carácter que se queda corta con nombres y observaciones. Aquí el
    texto completo está siempre a un roce del ratón.
    """
    tabla = TablaResponsiva(page, _tabla_columnas_actividad(),
                            ancho_inicial=ancho, tooltips=True)
    tabla.set_contenido(
        [FilaDatos([m.empresa, m.sucursal, m.empleado, m.fecha, m.etiqueta,
                    m.serie, m.nombre, m.observaciones, m.precio])
         for m in pagina.elementos],
        refrescar=False)
    return tabla.control


def _tabla_columnas_listado() -> list:
    """Columnas del inventario. Ocho: lo que manda el servicio menos `num` y
    `total`, que son del paginado y no del activo.

    Mismo criterio de alineación que la bitácora: centradas las que traen
    valores más cortos que su columna, a la izquierda el nombre del activo
    —texto largo, donde el filo izquierdo da el punto de partida— y a la derecha
    el precio.
    """
    return [
        ColumnaTabla("Empresa", 11, ancho_min_px=70),
        ColumnaTabla("Sucursal", 11, ancho_min_px=70),
        ColumnaTabla("Resguardo", 18, ancho_min_px=110),
        ColumnaTabla("Fecha", 9, ancho_min_px=68),
        ColumnaTabla("Etiqueta", 10, ancho_min_px=70),
        ColumnaTabla("Serie", 12, ancho_min_px=80),
        ColumnaTabla("Activo", 18, alineacion=IZQ, ancho_min_px=100),
        ColumnaTabla("Precio", 10, alineacion=DER, ancho_min_px=60,
                     padding_der=_PAD_DER_TABLA),
    ]


def _tabla_listado(page, pagina, ancho: int) -> ft.Control:
    """Inventario de UNA página."""
    tabla = TablaResponsiva(page, _tabla_columnas_listado(),
                            ancho_inicial=ancho, tooltips=True)
    tabla.set_contenido(
        # La fecha puede venir NULA —un activo que nunca se ha movido—, y ahí
        # el marcador dice «no hay» en vez de dejar el hueco en blanco, que se
        # lee como un fallo de pintado.
        [FilaDatos([a.empresa, a.sucursal, a.empleado, a.fecha or _SIN_DATO,
                    a.etiqueta, a.serie, a.nombre, a.precio])
         for a in pagina.elementos],
        refrescar=False)
    return tabla.control


class _PaginadorTabla:
    """Gobierna un modal de listado paginado: navega y pinta cada página.

    Lo usan la bitácora y el inventario, que solo se diferencian en QUÉ traen y
    CÓMO se tabula; el paginado es el mismo y no se duplica.

    El paginado es del SERVIDOR: cada página es una consulta, y el número de
    ellas sale de dividir el `total` que viene en la respuesta entre el tamaño
    de página. No se trae todo para partirlo aquí —son miles de renglones— ni se
    puede, porque el endpoint exige `page` y `pageSize`.

    Sus controles viven en el PIE del modal, que no se toca al cambiar de
    página: el cuerpo se sustituye entero por el esqueleto y luego por la tabla
    nueva, y un paginador ahí dentro desaparecería justo cuando hace falta.

    Vive mientras el modal esté abierto —lo sostienen los manejadores de sus
    botones— y guarda el ámbito de la consulta CONGELADO: los filtros del
    tablero se leen al abrir y no se vuelven a mirar, para que navegar no acabe
    mezclando páginas de dos ámbitos si alguien toca un combo por detrás.
    """

    def __init__(self, seccion: "SeccionDashboard", modal: Modal, obtener,
                 tabla, *, extras: dict | None = None) -> None:
        """`obtener(pagina, tam, **ambito)` trae una `paginado.Pagina`, y
        `tabla(page, pagina, ancho)` la convierte en controles.

        `extras` son filtros propios del listado que no salen del tablero —el
        `activo` del inventario— y viajan con el resto del ámbito.
        """
        self.seccion = seccion
        self.modal = modal
        self._obtener = obtener
        self._tabla = tabla
        self.ambito = {"empresa": seccion._empresa,
                       "sucursal": seccion._sucursal,
                       "tipo": seccion._tipo,
                       **(extras or {})}
        self.pagina = 1
        self.paginas = 1
        self.tam = paginado_api.TAM_PAGINA
        self._cargando = False
        self._anterior = boton_herramienta("Anterior", ft.Icons.CHEVRON_LEFT,
                                           on_click=self._ir_atras)
        self._siguiente = boton_herramienta("Siguiente", ft.Icons.CHEVRON_RIGHT,
                                            on_click=self._ir_adelante)
        # El indicador de página se parte en tres: el rango a la izquierda, un
        # campo con la página vigente —escribible, para saltar— y el total a la
        # derecha. Con 1,205 páginas, llegar a una concreta a golpe de
        # «Siguiente» no es navegación, es resignación.
        self._rango = ft.Text("", theme_style=ft.TextThemeStyle.BODY_MEDIUM,
                              color=GRIS, no_wrap=True)
        self._caja_pagina = campo_tabla_texto(
            valor="1", ancho=_ANCHO_CAMPO_PAGINA,
            alineacion=ft.TextAlign.CENTER,
            on_submit=self._saltar_a_escrita, on_blur=self._restaurar_pagina)
        # `campo_tabla_texto` devuelve la CAJA; el campo, que es quien tiene
        # `.value`, es su contenido.
        self._campo_pagina = self._caja_pagina.content
        self._de_paginas = ft.Text("de 1",
                                   theme_style=ft.TextThemeStyle.BODY_MEDIUM,
                                   color=GRIS, no_wrap=True)
        self._tamanos = {f"{n} / pág.": n for n in _TAMANOS_PAGINA}
        self._select = SelectCompacto(
            seccion.page, list(self._tamanos),
            valor=next(k for k, v in self._tamanos.items() if v == self.tam),
            ancho=_ANCHO_SELECT_PAGINA, titulo="Elementos por página",
            on_change=self._cambio_tam)
        self._botones()

    def pie(self) -> list:
        """Acciones del modal: paginador a la izquierda, «Cerrar» a la derecha.

        El `expand` del primer bloque es lo que separa ambos: el pie alinea sus
        acciones a la derecha, así que sin él el paginador se pegaría a Cerrar.
        """
        return [
            ft.Container(
                ft.Row([self._select.control, self._anterior, self._rango,
                        ft.Text("·", color=GRIS),
                        ft.Text("Página",
                                theme_style=ft.TextThemeStyle.BODY_MEDIUM,
                                color=GRIS, no_wrap=True),
                        self._caja_pagina, self._de_paginas, self._siguiente],
                       spacing=GAP_SM,
                       vertical_alignment=ft.CrossAxisAlignment.CENTER),
                expand=True),
            boton_secundario("Cerrar", on_click=lambda _e: self.modal.cerrar()),
        ]

    async def ir_a(self, pagina: int, grupos: tuple | None = None) -> None:
        """Trae una página y la pinta. `grupos` es el esqueleto ya montado.

        Sin `grupos` se monta uno nuevo: al cambiar de página la consulta tarda
        lo mismo que la primera, y dejar la tabla anterior a la vista haría
        dudar de si el clic hizo algo.
        """
        if self._cargando:
            return
        self._cargando = True
        self._botones()
        if grupos is None:
            grupos = self.seccion._mostrar_esqueleto(
                self.modal, _FORMA_ESQ_LISTADO)
        datos, error = await self.seccion._traer(
            self._obtener, pagina, self.tam, **self.ambito)
        self._cargando = False
        if error:
            self._botones()
            self.seccion._fallar_modal(self.modal, grupos, error)
            return
        self.pagina, self.paginas = datos.pagina, datos.paginas
        _detener(grupos)
        ancho = _ancho_contenido(self.modal)
        self.modal.cuerpo.controls = (
            [_mensaje_modal(_SIN_INFO, ft.Icons.INBOX)] if datos.vacia
            else [_acotado(self._tabla(self.seccion.page, datos, ancho))])
        self._rango.value = f"{datos.desde:,}–{datos.hasta:,} de {datos.total:,}"
        self._de_paginas.value = f"de {datos.paginas:,}"
        self._restaurar_pagina()
        self._botones()
        self.modal.refrescar()

    # ------------------------------------------------------------- interno
    def _botones(self) -> None:
        """Único punto que decide si se puede navegar.

        Las tres condiciones —consulta en vuelo, primera página, última— se
        resuelven juntas: repartidas, una salida de error dejaba «Anterior»
        encendido en la página uno.

        El select se apaga con ellos: cambiar de tamaño a mitad de consulta
        dejaría dos peticiones en vuelo pidiendo cosas distintas.
        """
        self._anterior.disabled = self._cargando or self.pagina <= 1
        self._siguiente.disabled = (self._cargando
                                    or self.pagina >= self.paginas)
        self._select.disabled = self._cargando
        # El campo se apaga solo mientras hay consulta; con una sola página
        # sigue escribible, porque escribir «1» ahí es inofensivo.
        self._campo_pagina.disabled = self._cargando

    def _saltar_a_escrita(self, _e=None) -> None:
        """Enter en la caja de página: va a la que se escribió.

        Fuera de rango o ilegible, se devuelve la página vigente sin avisar ni
        consultar: el propio campo, volviendo a su valor, ya dice que lo tecleado
        no valía, y un aviso encima de un modal sobra.
        """
        pagina = self._pagina_escrita()
        if pagina is None or pagina == self.pagina:
            self._restaurar_pagina()
            self.modal.refrescar()
            return
        self._navegar(pagina)

    def _pagina_escrita(self) -> int | None:
        """La página tecleada, o None si no sirve.

        Se tolera la COMA porque el total que hay al lado la lleva («de 1,205»)
        y copiar de ahí es lo primero que uno hace. El punto NO: es el separador
        decimal, y admitirlo convertía «12.5» en la página 125 en vez de
        rechazarlo.
        """
        crudo = (self._campo_pagina.value or "").strip()
        for separador in (",", " ", " "):
            crudo = crudo.replace(separador, "")
        if not crudo.isdigit():
            return None
        pagina = int(crudo)
        return pagina if 1 <= pagina <= self.paginas else None

    def _restaurar_pagina(self, _e=None) -> None:
        """Devuelve la caja a la página vigente. Sin separador de millar: es un
        campo de entrada, y lo que se escribe ahí se vuelve a leer."""
        self._campo_pagina.value = str(self.pagina)

    def _cambio_tam(self, _e=None) -> None:
        """Cambia el tamaño de página y vuelve a la PRIMERA.

        Volver es obligado, no una comodidad: con 1,205 páginas de diez, pasar a
        cien deja la página vigente fuera de rango y el servidor devolvería una
        lista vacía.
        """
        nuevo = self._tamanos.get(self._select.value or "", self.tam)
        if nuevo == self.tam:
            return
        self.tam = nuevo
        # La página 1 siempre está en rango, así que no hace falta saltarse la
        # comprobación de `_navegar`.
        self._navegar(1)

    def _ir_atras(self, _e=None) -> None:
        self._navegar(self.pagina - 1)

    def _ir_adelante(self, _e=None) -> None:
        self._navegar(self.pagina + 1)

    def _navegar(self, pagina: int) -> None:
        """Los manejadores de clic son SÍNCRONOS, así que la consulta se lanza
        con `run_task`, como el resto del trabajo async de esta pantalla."""
        if not 1 <= pagina <= self.paginas:
            return
        try:
            self.seccion.page.run_task(self.ir_a, pagina)
        except Exception:  # noqa: BLE001 — sin bucle no hay navegación
            pass


class SeccionDashboard:
    """Tablero de activos fijos."""

    def __init__(self, app):
        self.app = app
        self.page = app.page
        # Guardia de reentrada: dos clics seguidos dejarían dos respuestas
        # compitiendo por pintar, y gana la que llegue tarde, no la última pedida.
        self._cargando = False
        # Generación del conteo animado, para que una carga nueva invalide el
        # conteo que siga corriendo de la anterior.
        self._conteo = 0
        # Lo último que se pintó, que es contra lo que se decide si un detalle
        # tiene algo que enseñar. `None` mientras no haya habido una consulta
        # buena: al arrancar y tras un primer fallo de red.
        self._datos: datos_api.DatosDashboard | None = None
        # Nombre -> id de cada catálogo. Los combos muestran nombres y los
        # endpoints esperan ids; esta es la correspondencia entre ambos.
        self._ids_empresa: dict[str, int] = {}
        self._ids_sucursal: dict[str, int] = {}
        # Sucursales ya consultadas, por empresa.
        self._cache_sucursales: dict[int, list] = {}
        # Modal de detalle a la vista, con la fracción de ventana que pidió. Se
        # guarda porque su ancho es un número en píxeles que hay que rehacer
        # cuando la ventana cambia de tamaño (ver `_on_resize`). Solo puede
        # haber uno: se abren desde las tarjetas, que quedan detrás del diálogo.
        self._modal_abierto: Modal | None = None
        self._fraccion_abierta = _FRACCION_MODAL_ESTRECHO
        self._construir()

    def _construir(self) -> None:
        # --- Tarjetas -----------------------------------------------------
        self.tar_total = TarjetaTotalActivos(
            _SIN_DATO, _SIN_DATO, ft.Icons.INVENTORY_2,
            titulo_bajas="inhabilitados",
            titulo_panel="Por tipo (solo habilitados)",
            # Las dos cifras abren el MISMO modal, pero por manejadores
            # distintos: cada una se guarda contra su propio conteo, para que un
            # tablero con activos y cero inactivos deje entrar por la primera.
            tooltip_desglose="Ver detalle de activos",
            on_total=self._detalle_activos, on_bajas=self._detalle_inactivos)

        # Verde en vez del azul de la paleta: es la única tarjeta que habla de
        # dinero y el color la separa de las tres que hablan de conteos, sin
        # tener que leer el rótulo. El tono vive en ui/tema.py.
        self.tar_valor = TarjetaValor(
            "Total de inversión", _SIN_DATO, ft.Icons.PAYMENTS,
            vacio=_SIN_ACTIVOS,
            accion="Ver detalle de inversión", on_accion=self._detalle_inversion,
            color_icono=tema.VERDE_DINERO,
            fondo_icono=tema.VERDE_DINERO_FONDO,
            color_acento=tema.VERDE_DINERO)

        self.tar_ranking = TarjetaRanking(
            "Top empresas (activos)", ft.Icons.LEADERBOARD,
            vacio=_SIN_ACTIVOS)

        self.tar_actividad = TarjetaActividad(
            "Actividad reciente",
            accion="Ver todo el historial", on_accion=self._detalle_actividad,
            vacio="No se encontraron movimientos")

        # --- Rejilla ------------------------------------------------------
        # 24 columnas: el cuadrante es la MITAD del de 12, para poder afinar
        # tamaños en medios pasos. `alto_fila=28` no es arbitrario: con
        # `espacio=16` el paso vertical queda en 44px, justo la mitad de los 88px
        # que daba `alto_fila=72`, así la subdivisión es exacta en ambos ejes.
        #
        # Los altos NO son redondos por gusto: la tarjeta grande (11 filas =
        # 468px) mide exactamente lo mismo que la pila de la derecha
        # (4 + 7 filas más el espacio entre ambas = 160 + 16 + 292 = 468). Si se
        # tocan, hay que rehacer la cuenta o el bloque queda desalineado abajo.
        self.rejilla = Rejilla(columnas=24, alto_fila=28, espacio=16)
        self.rejilla.agregar_todos([
            Bloque(self.tar_total.control,     col=0,  fila=0, ancho=12, alto=11),
            Bloque(self.tar_valor.control,     col=12, fila=0, ancho=6,  alto=4),
            Bloque(self.tar_ranking.control,   col=18, fila=0, ancho=6,  alto=4),
            Bloque(self.tar_actividad.control, col=12, fila=4, ancho=12, alto=7),
        ])

        # STRETCH: sin él un Column da a sus hijos el ancho NATURAL de su
        # contenido, y la barra de filtros se quedaría del ancho de los combos
        # en vez de ocupar la pantalla. Es la causa recurrente de que algo no
        # llene su caja (ver ui/COMPONENTES.md).
        self.contenido = ft.Column(
            [self._construir_filtros(), self.rejilla.contenido],
            expand=True, spacing=24,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH)

    # ------------------------------------------------------------- filtros
    def _construir_filtros(self) -> ft.Control:
        """Barra de filtros: empresa y sucursal, más el botón que consulta."""
        # `SelectCompacto` y no `campo_opciones`: un campo de Material mide 56px
        # de alto y en una barra de filtros eso pesa más que los propios datos.
        # No hay forma de bajarlo —`ft.Dropdown` ignora `dense`, `height` y
        # `content_padding`—, así que se usa el select de caja propia (35px), que
        # además trae buscador: con 58 empresas, una lista sin filtrar no sirve.
        # Nace con el catálogo estático; `_cargar_empresas` lo sustituye por el
        # de la API en cuanto arranca la pantalla. Así el combo nunca está vacío,
        # ni siquiera durante esa primera consulta.
        self.fil_empresa = SelectCompacto(
            self.page, [_TODAS_EMPRESAS, *NOMBRES_EMPRESAS],
            valor=_TODAS_EMPRESAS, ancho=_ANCHO_FILTRO, titulo="Empresa",
            on_change=self._cambio_empresa)
        self._ids_empresa = dict(ID_POR_EMPRESA)
        # La sucursal cuelga de la empresa: el endpoint la exige, así que hasta
        # que no haya una elegida no hay nada que ofrecer.
        self.fil_sucursal = SelectCompacto(
            self.page, [_TODAS_SUCURSALES], valor=_TODAS_SUCURSALES,
            ancho=_ANCHO_FILTRO, titulo="Sucursal", disabled=True)
        self.fil_sucursal.control.tooltip = _AVISO_SUCURSAL
        # El tipo no cuelga de nada: su catálogo es fijo y está en el repo, así
        # que nace poblado y no necesita consulta ni manejador.
        self.fil_tipo = SelectCompacto(
            self.page, [_TODOS_TIPOS, *_TIPOS_FILTRO.values()],
            valor=_TODOS_TIPOS, ancho=_ANCHO_FILTRO, titulo="Tipo de activo")
        self._ids_tipo = {nombre: id_tipo
                          for id_tipo, nombre in _TIPOS_FILTRO.items()}

        self.btn_buscar = boton_primario_icono(
            ft.Icons.SEARCH, "Aplicar los filtros y actualizar el tablero",
            on_click=self._aplicar_filtros)

        # Señal de espera: el patrón de la casa (ver ui/registro_activos.py). El
        # diseño contempla un skeleton shimmer que todavía no está definido;
        # cuando lo esté, `_set_cargando` es lo ÚNICO que hay que reescribir.
        self.progreso = ft.ProgressRing(width=22, height=22, stroke_width=3,
                                        visible=False)
        self.estado = ft.Text("", size=12, color=GRIS)

        rotulo = ft.Row(
            [ft.Icon(ft.Icons.FILTER_LIST, size=18,
                     color=ft.Colors.ON_SURFACE_VARIANT),
             ft.Text("Filtros", theme_style=ft.TextThemeStyle.LABEL_MEDIUM,
                     color=ft.Colors.ON_SURFACE_VARIANT, no_wrap=True)],
            spacing=4, tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER)

        # `wrap`: con la ventana en su ancho mínimo (960px) el rótulo, los combos
        # y el botón no caben en una línea, y sin envolver el último se corta.
        return tarjeta_seccion(
            ft.Row([rotulo, self.fil_empresa.control, self.fil_sucursal.control,
                    self.fil_tipo.control,
                    self.btn_buscar, self.progreso, self.estado],
                   spacing=GAP_MD, run_spacing=GAP_SM, wrap=True,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=GAP_MD)

    # ----------------------------------------------------------- catálogos
    @property
    def _empresa(self) -> int | None:
        """Id de la empresa elegida, o None cuando el combo está en 'todas'."""
        return self._ids_empresa.get(self.fil_empresa.value or "")

    @property
    def _sucursal(self) -> int | None:
        """Id de la sucursal elegida, o None cuando está en 'todas'."""
        return self._ids_sucursal.get(self.fil_sucursal.value or "")

    @property
    def _tipo(self) -> int | None:
        """Id del tipo elegido, o None cuando está en 'todos'.

        OJO: devuelve 0 para «Sin identificar», que es un filtro válido. Quien lo
        use tiene que comparar contra None, nunca por verdad.
        """
        return self._ids_tipo.get(self.fil_tipo.value or "")

    async def _cargar_empresas(self) -> None:
        """Trae el catálogo de empresas de la API.

        Si falla, cae al catálogo ESTÁTICO de `core/empresas.py`: es una réplica
        versionada y algo desactualizada, pero deja la pantalla usable sin red,
        que es mejor que un combo vacío.
        """
        try:
            lista = await asyncio.to_thread(catalogos.empresas)
            self._ids_empresa = {e.nombre: e.id for e in lista}
            nombres = [e.nombre for e in lista]
        except Exception:  # noqa: BLE001 — hay plan B, no se reporta
            self._ids_empresa = dict(ID_POR_EMPRESA)
            nombres = list(NOMBRES_EMPRESAS)
        self.fil_empresa.set_opciones([_TODAS_EMPRESAS, *nombres],
                                      valor=_TODAS_EMPRESAS)
        self._set_sucursales([])
        self._safe_update()

    def _cambio_empresa(self, _e=None) -> None:
        """Al cambiar la empresa hay que traer SUS sucursales.

        El manejador del combo es síncrono, así que la consulta se lanza con
        `run_task`. Si no hay bucle, la sucursal se queda deshabilitada en vez de
        ofrecer las de la empresa anterior.
        """
        self._set_sucursales([])
        self._safe_update()
        try:
            self.page.run_task(self._cargar_sucursales)
        except Exception:  # noqa: BLE001 — el filtro es de apoyo
            pass

    async def _cargar_sucursales(self) -> None:
        """Trae las sucursales de la empresa elegida.

        El endpoint EXIGE la empresa, así que sin una elegida ni se llama: el
        combo se queda deshabilitado, que es lo que corresponde.
        """
        empresa = self._empresa
        if empresa is None:
            return
        if empresa in self._cache_sucursales:
            # Alternar entre dos empresas es lo normal al comparar; sin caché,
            # cada vuelta repetiría la misma consulta con su espera.
            self._set_sucursales(self._cache_sucursales[empresa])
            self._safe_update()
            return
        self._set_cargando(True, "Cargando sucursales…")
        try:
            lista = await asyncio.to_thread(catalogos.sucursales, empresa)
        except Exception as exc:  # noqa: BLE001 — se reporta al usuario
            self._set_cargando(False)
            self.app.avisar(f"No se pudieron cargar las sucursales: {exc}",
                            ROJO, duracion=9000)
            return
        self._cache_sucursales[empresa] = lista
        self._set_sucursales(lista)
        self._set_cargando(False)

    def _set_sucursales(self, lista: list) -> None:
        """Repuebla el combo de sucursales y lo habilita solo si hay de dónde
        elegir. Vuelve siempre a «todas»: la sucursal anterior era de otra
        empresa y dejarla mostraría un filtro que ya no aplica."""
        self._ids_sucursal = {s.nombre: s.id for s in lista}
        self.fil_sucursal.set_opciones(
            [_TODAS_SUCURSALES, *(s.nombre for s in lista)],
            valor=_TODAS_SUCURSALES)
        self.fil_sucursal.disabled = not lista
        self.fil_sucursal.control.tooltip = None if lista else _AVISO_SUCURSAL

    # --------------------------------------------------------------- carga
    def _set_cargando(self, cargando: bool, texto: str = "", *,
                      tarjetas: bool = False) -> None:
        """Enciende o apaga la señal de espera y bloquea la barra.

        Único punto que define CÓMO se ve la espera. `tarjetas` añade el
        esqueleto sobre el contenido de las tarjetas, y solo lo pide la consulta
        del tablero: al traer el catálogo de sucursales no es ese dato el que se
        está esperando, y esqueletearlo daría a entender que va a cambiar.

        El anillo de la barra se queda: de momento solo una tarjeta tiene
        esqueleto, y sin él la espera de las otras tres no se anunciaría.
        """
        self.progreso.visible = cargando
        self.estado.value = texto
        self.estado.color = GRIS
        self.btn_buscar.disabled = cargando
        self.fil_empresa.disabled = cargando
        self.fil_tipo.disabled = cargando
        # Al APAGAR se apaga siempre, valga lo que valga `tarjetas`: es lo que
        # permite que `_fallo` y las salidas de `_cargar_sucursales` cierren el
        # esqueleto con un `_set_cargando(False)` a secas.
        self._esqueletos(cargando and tarjetas)
        self._safe_update()

    def _esqueletos(self, activo: bool) -> None:
        """Pone o quita el esqueleto de las tarjetas y pasea su brillo.

        Cada tarjeta lleva su bucle porque cada una refresca lo suyo; dentro de
        una, todas sus barras comparten el suyo (ver `Esqueleto`), incluso
        cuando están repartidas en sitios distintos como en la de totales.

        El bucle va en `run_task` dentro de un `try`, como el resto de las
        animaciones de la pantalla: sin bucle de eventos las barras se quedan
        quietas —un adorno menos— pero visibles, que es lo que importa.
        """
        tarjetas = (self.tar_total, self.tar_valor, self.tar_ranking,
                    self.tar_actividad)
        for tarjeta in tarjetas:
            tarjeta.cargando(activo)
        if not activo:
            return
        for tarjeta in tarjetas:
            try:
                self.page.run_task(tarjeta.animar_carga)
            except Exception:  # noqa: BLE001 — el brillo no es crítico
                pass

    async def _aplicar_filtros(self, _e=None) -> None:
        """Botón «Buscar»: punto único donde el tablero consulta el endpoint."""
        if self._cargando:
            return
        self._cargando = True
        self._set_cargando(True, "Consultando el tablero…", tarjetas=True)
        try:
            # `api.solicitar` es urllib BLOQUEANTE con 30s de timeout: llamarlo
            # en el hilo de la interfaz congela la ventana hasta medio minuto.
            datos = await asyncio.to_thread(datos_api.obtener, self._empresa,
                                            self._sucursal, self._tipo)
        except entorno.FaltaVariableEntorno:
            # Su mensaje habla de variables de entorno y de un `.env`; en esta
            # app la URL se captura en Configuración, así que se reescribe.
            self._fallo("Falta configurar la URL de la API en Configuración.")
            return
        except api.ErrorAPI as exc:
            # Sus mensajes ya están redactados para el usuario final.
            self._fallo(str(exc))
            return
        except Exception as exc:  # noqa: BLE001 — se reporta al usuario
            self._fallo(f"No se pudo cargar el tablero: {exc}")
            return
        self._cargando = False
        # `_pintar` quita los esqueletos él mismo, en el mismo refresco en que
        # puebla las tarjetas. Apagarlos aquí, antes, dejaría un fotograma con el
        # contenido de la consulta ANTERIOR a la vista; y apagarlos después
        # taparía el conteo animado del importe, que arranca dentro de `_pintar`.
        self._pintar(datos)
        self._set_cargando(False)

    def _fallo(self, mensaje: str) -> None:
        """Cierra la espera y avisa. Se llama desde TODAS las salidas de error."""
        self._cargando = False
        self._set_cargando(False)
        self.app.avisar(mensaje, ROJO, duracion=9000)

    def _pintar(self, datos: datos_api.DatosDashboard) -> None:
        """Reparte los datos a las tarjetas. Único método que toca controles.

        Los setters de las tarjetas NO refrescan: se llenan todas y se manda un
        solo `update()` sobre la pantalla, que es el ancestro común.
        """
        # Se guarda ANTES de repartir: es la única copia de la respuesta, y de
        # ella salen los candados de los modales de detalle.
        self._datos = datos
        # Los esqueletos se van AQUÍ, no en `_set_cargando`: así se apagan y se
        # puebla en el mismo refresco, sin un fotograma intermedio en el que se
        # vieran las tarjetas con lo de la consulta anterior.
        self._esqueletos(False)
        # Las cifras arrancan en cero y las suben `_contar`; ponerlas ya en su
        # valor haría que se vieran un instante antes de saltar de vuelta a 0.
        self.tar_total.set_totales("0", "0")
        cifra_altas, texto_altas = _nota_mes(datos.altas_mes, "alta", "altas")
        cifra_bajas, texto_bajas = _nota_mes(datos.bajas_mes, "baja", "bajas")
        self.tar_total.set_notas(cifra_altas, texto_altas,
                                 cifra_bajas, texto_bajas)
        desglose = _desglose_visible(datos.desglose, self._tipo)
        self.tar_total.set_tipos(_filas_por_tipo(desglose, self._tipo))
        self.tar_total.set_conteos(["0"] * len(desglose))
        self.tar_ranking.set_filas([(nombre, f"{total:,}")
                                    for nombre, total in datos.top_empresas])
        self.tar_actividad.set_eventos(_filas_actividad(datos.actividad))
        # El importe viene YA FORMATEADO del servicio; aquí solo se pinta. Si no
        # llegara —un SP viejo, o un ámbito sin importe que calcular— se cae al
        # marcador en vez de dejar la cifra en blanco.
        # La moneda solo acompaña a un importe REAL: «— MXN» calificaría en
        # pesos un dato que no llegó.
        self.tar_valor.set_valor(datos.valor or _SIN_DATO,
                                 _MONEDA if datos.valor else "")
        # Sin activos el guion no explicaría nada: un importe en blanco se lee
        # como «no llegó el dato», no como «no hay nada que valuar».
        self.tar_valor.mostrar_vacio(datos.total == 0)
        # El importe se deja en cero para que lo suba `_contar`, pero el TAMAÑO
        # ya quedó calculado arriba contra el valor final: `set_cifra` no lo
        # vuelve a tocar. Si no es un importe reconocible se queda como está.
        importe = _descomponer_importe(datos.valor)
        if importe:
            self.tar_valor.set_cifra(_formatear_importe(importe, 0))
        self._safe_update()
        # Se pasa el desglose YA RECORTADO, no el de `datos`: el conteo animado
        # escribe por posición contra las filas montadas, y con el filtro puesto
        # esas dos listas tienen distinta longitud. Usando la de `datos` el panel
        # acababa mostrando el conteo del renglón equivocado.
        self._arrancar_animaciones(datos, desglose, importe)

    # ------------------------------------------------------------ animación
    def _arrancar_animaciones(self, datos: datos_api.DatosDashboard,
                              desglose: list[tuple],
                              importe: Importe | None = None) -> None:
        """Lanza el conteo y las entradas de las listas.

        Si no hay bucle donde correrlas, deja todo en su estado final: las
        animaciones son un adorno, pero las listas quedan PREPARADAS invisibles y
        quedarse así por falta de bucle sí sería un problema.
        """
        try:
            self.page.run_task(self._animar, datos, desglose, importe)
        except Exception:  # noqa: BLE001 — la animación no es crítica
            self._sin_animar(datos, desglose, importe)

    def _sin_animar(self, datos: datos_api.DatosDashboard,
                    desglose: list[tuple],
                    importe: Importe | None = None) -> None:
        """Estado final de golpe, sin transiciones."""
        self.tar_total.set_totales(f"{datos.activos:,}", f"{datos.inactivos:,}")
        self.tar_total.set_conteos([f"{t:,}" for _, _, t in desglose])
        if importe:
            # `_pintar` lo dejó en cero contando con que alguien lo subiera.
            self.tar_valor.set_cifra(datos.valor)
        self.tar_ranking.mostrar_entrada()
        self.tar_actividad.mostrar_entrada()
        self._safe_update()

    async def _animar(self, datos: datos_api.DatosDashboard,
                      desglose: list[tuple],
                      importe: Importe | None = None) -> None:
        """Corre las tres animaciones EN PARALELO.

        En serie sumarían más del segundo que marca el diseño; a la vez, el
        tablero entero termina de armarse en lo que dura la más larga.
        """
        await asyncio.gather(
            self._contar(datos.activos, datos.inactivos,
                         [t for _, _, t in desglose], importe),
            self.tar_ranking.animar_entrada(),
            self.tar_actividad.animar_entrada(),
        )

    async def _contar(self, activos: int, inactivos: int,
                      por_tipo: list[int],
                      importe: Importe | None = None) -> None:
        """Sube los totales, los conteos por tipo y el importe, de 0 a su valor.

        Se refrescan SOLO las tarjetas, nunca la pantalla: son veinte fotogramas
        en menos de un segundo, y mandar el árbol completo en cada uno es justo
        lo que salía caro en la tabla de registro. Los totales y el desglose
        viven en la MISMA tarjeta, así que entran en un único refresco; el
        importe está en otra y lleva el suyo, solo cuando hay algo que contar.
        """
        self._conteo += 1
        mio = self._conteo
        pasos = max(1, round(_CONTEO_SEGUNDOS * _CONTEO_FPS))
        intervalo = _CONTEO_SEGUNDOS / pasos
        for i in range(1, pasos + 1):
            await asyncio.sleep(intervalo)
            # Una carga más nueva invalida este conteo. Sin esto, dos consultas
            # seguidas dejan dos animaciones pisándose y la cifra que queda es la
            # de la que terminó de contar al final, no la que se pidió al final.
            if mio != self._conteo:
                return
            # Ease-out cúbico: arranca rápido y asienta, que es como se lee un
            # contador. Lineal parece una barra de progreso.
            avance = 1 - (1 - i / pasos) ** 3
            self.tar_total.set_totales(f"{round(activos * avance):,}",
                                       f"{round(inactivos * avance):,}")
            self.tar_total.set_conteos([f"{round(t * avance):,}"
                                        for t in por_tipo])
            self.tar_total.refrescar()
            if importe:
                # Sube con los MISMOS pasos y la misma curva que los totales,
                # así que las dos tarjetas asientan a la vez. El último
                # fotograma se pinta con el TEXTO del servicio, no con una
                # reconstrucción: reformatear acierta, pero lo que queda en
                # pantalla al final debe ser exactamente lo que él mandó.
                self.tar_valor.set_cifra(
                    importe.texto if i == pasos
                    else _formatear_importe(importe, importe.valor * avance))
                self.tar_valor.refrescar()

    # ------------------------------------------------- detalle (modales)
    def _abrir_detalle(self, titulo: str, hay_datos: bool, cargar,
                       forma: tuple = _FORMA_ESQ_INVERSION,
                       fraccion: float = _FRACCION_MODAL_ESTRECHO) -> None:
        """Punto ÚNICO de apertura de los modales de detalle.

        `hay_datos` se mide contra lo que la tarjeta está mostrando: si su
        previsualización viene vacía, el detallado también lo estaría, así que
        en vez de abrir un modal en blanco se avisa y no se abre nada. El aviso
        va en `NARANJA` y no en `ROJO`: no falló nada, simplemente no hay qué
        enseñar.

        `cargar` es la corrutina que recibe el modal y el esqueleto y lo llena.
        Es obligatoria: los cuatro detalles están cableados, y un modal que
        abriera sin nada que traer no tendría por qué abrirse.

        El modal se construye AL ABRIR, como `_dialogo_opciones` de
        ui/componentes.py: su contenido depende de los filtros vigentes, y
        montarlo una sola vez obligaría a vaciarlo y rellenarlo en cada clic.
        """
        if not hay_datos:
            self.app.avisar(_SIN_INFO, NARANJA)
            return
        modal = Modal(self.page, titulo,
                      ancho=_ancho_modal(self.page, fraccion),
                      alto_cuerpo=_ALTO_CUERPO_MODAL,
                      al_cerrar=self._soltar_modal)
        # Las acciones se ponen DESPUÉS de construir: el manejador de «Cerrar»
        # necesita el modal, que todavía no existía al pasar el constructor.
        modal.set_acciones([boton_secundario(
            "Cerrar", on_click=lambda _e: modal.cerrar())])
        self._modal_abierto, self._fraccion_abierta = modal, fraccion
        # Se abre YA, con el esqueleto puesto: esperar a la respuesta con la
        # ventana quieta deja al usuario sin saber si el clic se registró.
        grupos = self._mostrar_esqueleto(modal, forma)
        modal.abrir()
        self.page.run_task(cargar, modal, grupos)

    def _soltar_modal(self, *_a) -> None:
        """Olvida el modal cerrado, para no reajustar uno que ya no se ve."""
        self._modal_abierto = None

    def _mostrar_esqueleto(self, modal: Modal, forma: tuple) -> tuple:
        """Pone el esqueleto en el cuerpo del modal y lo echa a andar.

        Devuelve los grupos, que quien cargue tendrá que detener. Se usa tanto
        al abrir como al cambiar de página, que es la misma espera.
        """
        controles, grupos = _esqueleto_modal(forma)
        modal.cuerpo.controls = controles
        modal.refrescar()
        try:
            self.page.run_task(self._animar_modal, modal, grupos)
        except Exception:  # noqa: BLE001 — el brillo no es crítico
            pass
        return grupos

    async def _animar_modal(self, modal: Modal, grupos: tuple) -> None:
        """Pasea el brillo de los grupos del modal con UN bucle y UN refresco.

        El refresco es el del modal y no el de cada grupo: sus barras están en
        bloques distintos del cuerpo, y refrescarlos por separado mandaría un
        mensaje por bloque en cada fotograma.
        """
        await grupos[0].animar(tambien=tuple(grupos[1:]),
                               refrescar=modal.refrescar)

    @staticmethod
    async def _traer(fn, *args, **kwargs) -> tuple:
        """Llama a `fn` en un hilo y traduce sus fallos a texto para el usuario.

        Devuelve `(datos, None)` o `(None, mensaje)`. Existe para que los
        modales no repitan las tres capturas —cada una con su matiz— en cada
        consulta: `api.solicitar` es urllib BLOQUEANTE con 30s de timeout, así
        que todas tienen que salir del hilo de la interfaz igual.
        """
        try:
            return await asyncio.to_thread(fn, *args, **kwargs), None
        except entorno.FaltaVariableEntorno:
            # Su mensaje habla de variables de entorno y de un `.env`; en esta
            # app la URL se captura en Configuración.
            return None, "Falta configurar la URL de la API en Configuración."
        except api.ErrorAPI as exc:
            # Sus mensajes ya están redactados para el usuario final.
            return None, str(exc)
        except Exception as exc:  # noqa: BLE001 — se reporta al usuario
            return None, f"No se pudo cargar el detalle: {exc}"

    async def _cargar_inversion(self, modal: Modal, grupos: tuple) -> None:
        """Trae el detalle de inversión y lo pinta dentro del modal ya abierto.

        Los errores se cuentan DENTRO del modal, no en un aviso: el usuario está
        mirando esa ventana, y un mensaje que se desvanece detrás de ella no lo
        vería.
        """
        datos, error = await self._traer(inversion_api.obtener, self._empresa,
                                         self._sucursal, self._tipo)
        if error:
            self._fallar_modal(modal, grupos, error)
            return
        _detener(grupos)
        modal.cuerpo.controls = (
            [_mensaje_modal(_SIN_INFO, ft.Icons.INBOX)] if datos.vacio
            else _cuerpo_inversion(self.page, datos, _ancho_contenido(modal)))
        modal.refrescar()

    async def _cargar_actividad(self, modal: Modal, grupos: tuple) -> None:
        await self._cargar_listado(modal, grupos, actividad_api.obtener,
                                   _tabla_actividad)

    async def _cargar_activos(self, modal: Modal, grupos: tuple) -> None:
        await self._cargar_listado(modal, grupos, listado_api.obtener,
                                   _tabla_listado, {"activo": True})

    async def _cargar_inactivos(self, modal: Modal, grupos: tuple) -> None:
        await self._cargar_listado(modal, grupos, listado_api.obtener,
                                   _tabla_listado, {"activo": False})

    async def _cargar_listado(self, modal: Modal, grupos: tuple, obtener,
                              tabla, extras: dict | None = None) -> None:
        """Arma el paginador y trae la primera página.

        El paginador se guarda solo: sus botones viven en el pie del modal y
        son ellos los que lo sostienen mientras la ventana esté abierta.
        """
        paginador = _PaginadorTabla(self, modal, obtener, tabla, extras=extras)
        modal.set_acciones(paginador.pie())
        await paginador.ir_a(1, grupos)

    def _fallar_modal(self, modal: Modal, grupos: tuple,
                      mensaje: str) -> None:
        _detener(grupos)
        modal.cuerpo.controls = [_mensaje_modal(mensaje, ft.Icons.ERROR_OUTLINE,
                                                color=ROJO)]
        modal.refrescar()

    def _detalle_activos(self, _e=None) -> None:
        self._abrir_detalle("Activos habilitados",
                            bool(self._datos and self._datos.activos),
                            self._cargar_activos, _FORMA_ESQ_LISTADO,
                            _FRACCION_MODAL_ANCHO)

    def _detalle_inactivos(self, _e=None) -> None:
        # Título propio, no el mismo que el de arriba: son dos mitades distintas
        # del inventario y el encabezado es lo único que las distingue una vez
        # abierto el modal.
        self._abrir_detalle("Activos inhabilitados",
                            bool(self._datos and self._datos.inactivos),
                            self._cargar_inactivos, _FORMA_ESQ_LISTADO,
                            _FRACCION_MODAL_ANCHO)

    def _detalle_actividad(self, _e=None) -> None:
        self._abrir_detalle("Actividad reciente",
                            bool(self._datos and self._datos.actividad),
                            self._cargar_actividad, _FORMA_ESQ_LISTADO,
                            _FRACCION_MODAL_ANCHO)

    def _detalle_inversion(self, _e=None) -> None:
        # Se cuelga del TOTAL de activos y no del importe: sin activos no hay
        # nada que valuar, y con ellos el detalle vale la pena aunque el importe
        # venga en cero (justo eso es lo que cuenta «sin costo registrado»).
        self._abrir_detalle("Detalle de inversión",
                            bool(self._datos and self._datos.total),
                            self._cargar_inversion)

    # -------------------------------------------------------------- datos
    def cargar_desde_db(self) -> None:
        """Arranque de la pantalla: catálogo de empresas y primera consulta.

        El shell llama a este método de forma SÍNCRONA, así que el trabajo se
        lanza con `run_task`, que es como el resto de la app arranca trabajo
        async desde un callback que no puede esperar.
        """
        self.page.run_task(self._arrancar)

    async def _arrancar(self) -> None:
        """Catálogo de empresas y primera consulta, EN ESE ORDEN.

        En paralelo no: las dos llamadas comparten la señal de espera y el
        tablero acabaría pintándose mientras el combo todavía se está poblando.
        """
        await self._cargar_empresas()
        await self._aplicar_filtros()

    def _on_resize(self, _e=None) -> None:
        """La rejilla y las tarjetas se remiden solas con `on_size_change`.

        El modal de detalle NO: su ancho y su alto son píxeles calculados al
        abrirlo, así que al encoger la ventana se quedaba más grande que ella y
        el diálogo lo recortaba por la derecha. Con el ancho al día, su
        contenido —que es fluido— se reacomoda solo, y la tabla recupera su
        barra horizontal en cuanto sus columnas dejan de caber.
        """
        modal = self._modal_abierto
        if modal is None:
            return
        modal.reajustar(_ancho_modal(self.page, self._fraccion_abierta))

    def _safe_update(self) -> None:
        try:
            self.contenido.update()
        except (RuntimeError, AssertionError, AttributeError):
            pass
