"""Tarjetas reutilizables del tablero (sistema de diseño; ver DISENO.md).

La regla de estas tarjetas es que **NO fijan su tamaño**: llenan la caja que les
den —una celda de la rejilla o un bloque de varias— y adaptan su densidad interna
al espacio disponible. Así la MISMA clase sirve para un recuadro 1x1 y para uno
3x2, sin duplicar componentes por tamaño.

Cómo lo consiguen:

- La superficie raíz no lleva `width`/`height`; hereda las restricciones del
  padre.
- Se miden con `on_size_change` y eligen un NIVEL de densidad (completo / medio /
  compacto). Al bajar de nivel se ocultan los elementos accesorios y encoge el
  valor, en vez de desbordar o recortar.
- Al remedir solo se MUTAN los controles ya montados (nunca se recrean), igual
  que en ui/tabla_responsiva.py.

Los colores se piden siempre por ROL de Material 3, nunca en hex, para que la
paleta de ui/tema.py se aplique sola en claro y en oscuro. El mockup distingue
las tarjetas con colores de Tailwind (emerald, blue, slate) que no existen en la
paleta del proyecto; aquí se traducen al rol más cercano y se pasan por
`color_acento`.
"""

from __future__ import annotations

import asyncio

import flet as ft

from ui.componentes import puntero_mano
from ui.comun import puntero_encima

# Espaciado y forma del sistema de diseño.
_RADIO = 12          # rounded.md — "elementos grandes"
_PAD = 24            # spacing.lg
_GAP = 16            # spacing.md
_GAP_SM = 8          # spacing.sm
_GAP_XS = 4          # spacing.xs
_RADIO_INTERNO = 8   # rounded.lg — paneles y pastillas dentro de la tarjeta
_ACENTO = 4          # ancho de la banda vertical de la tarjeta destacada

# Elevación: nivel 1 en reposo, nivel 2 al pasar el cursor.
#
# La sombra de hover se TIÑE con el color primario en vez de ser un negro más
# denso. Una sombra neutra más grande se lee como "la tarjeta está más lejos del
# fondo" y poco más; con el tinte, el borde superior de la sombra recoge el color
# de la marca y el cambio se nota sin tener que agrandarla tanto.
_SOMBRA_N1 = ft.BoxShadow(
    blur_radius=4, offset=ft.Offset(0, 2),
    color=ft.Colors.with_opacity(0.05, ft.Colors.BLACK))
# La sombra del hover se construye POR TARJETA con su propio color de acento
# (`_sombra_hover`), no como una constante compartida: así el halo es el de la
# tarjeta —verde el de valor, azul el de totales— y el realce refuerza a qué
# pertenece cada bloque en vez de teñirlos todos igual.
_HOVER_DIFUMINADO = 22
_HOVER_EXPANSION = 1
_HOVER_DESPLAZAMIENTO = 8
_HOVER_OPACIDAD = 0.28

# Al pasar el cursor el borde también sube de tono, al mismo color de acento. Es
# lo que hace que la tarjeta se sienta "viva" en el tema OSCURO, donde una sombra
# sobre fondo casi negro apenas se percibe.
_BORDE_REPOSO = ft.Colors.OUTLINE_VARIANT


def _sombra_hover(color: str) -> ft.BoxShadow:
    """Halo del hover en el color que se le pase."""
    return ft.BoxShadow(
        blur_radius=_HOVER_DIFUMINADO, spread_radius=_HOVER_EXPANSION,
        offset=ft.Offset(0, _HOVER_DESPLAZAMIENTO),
        color=ft.Colors.with_opacity(_HOVER_OPACIDAD, color))

# Un pelo de escala al pasar el cursor. Es lo que de verdad hace evidente el
# hover: la sombra sola compite con el fondo y en pantallas claras casi no se ve,
# mientras que el movimiento se percibe aunque no se esté mirando la tarjeta.
# Puede escalar sin descuadrar nada porque las tarjetas van posicionadas en un
# `Stack` (ui/rejilla.py) y no reflujan a sus vecinas.
_ESCALA_HOVER = 1.012

# Realce de una fila pulsable dentro de una tarjeta (los eventos de la bitácora).
_FONDO_FILA_HOVER = ft.Colors.SURFACE_CONTAINER_LOW

# --- Entrada escalonada de las listas ------------------------------------
# Las filas aparecen subiendo desde un poco más abajo mientras se funden. El
# escalonado es lo que da la sensación de que la lista "se arma": si entraran
# todas a la vez sería un solo parpadeo y no se leería como movimiento.
_ENTRADA_MS = 260              # fundido de CADA fila
_ENTRADA_ESCALON = 0.07        # retraso entre una fila y la siguiente
_ENTRADA_DESPLAZAMIENTO = 0.35  # fracción de su propio alto que sube al entrar
# El escalonado completo nunca pasa de esto, pase lo que pase con el número de
# filas: con el escalón fijo, una lista larga se saldría del segundo que marca el
# diseño. Sumado al fundido de la última fila el total queda en ~0.96s.
_ENTRADA_TOTAL_MAX = 0.7


def preparar_entrada(control: ft.Control) -> None:
    """Deja un control listo para entrar: invisible y desplazado hacia abajo.

    Se llama al POBLAR la lista; el encendido lo hace `entrar_escalonado`. Quien
    prepare una lista y no la anime la dejaría invisible, así que las tarjetas
    exponen también `mostrar_entrada` para el camino sin animación.
    """
    control.opacity = 0
    control.offset = ft.Offset(0, _ENTRADA_DESPLAZAMIENTO)
    control.animate_opacity = ft.Animation(_ENTRADA_MS, ft.AnimationCurve.EASE_OUT)
    control.animate_offset = ft.Animation(_ENTRADA_MS, ft.AnimationCurve.EASE_OUT)


def mostrar_entrada(controles: list) -> None:
    """Deja los controles en su estado final, sin animar."""
    for c in controles:
        c.opacity = 1
        c.offset = ft.Offset(0, 0)


async def entrar_escalonado(controles: list, refrescar) -> None:
    """Enciende los controles uno tras otro, de arriba abajo."""
    if not controles:
        return
    escalon = min(_ENTRADA_ESCALON, _ENTRADA_TOTAL_MAX / len(controles))
    for c in controles:
        c.opacity = 1
        c.offset = ft.Offset(0, 0)
        refrescar()
        await asyncio.sleep(escalon)

# --- Esqueleto de carga ---------------------------------------------------
# Una barra del color de una superficie hundida con un brillo que la recorre de
# izquierda a derecha. Dice dos cosas que un `ProgressRing` no dice: que la
# espera es por ESTE dato, y qué forma va a tener cuando llegue.
_ESQUELETO_MS = 1200        # lo que tarda el brillo en cruzar la barra
_ESQUELETO_FPS = 15         # suficiente para que se lea fluido sin cargar el hilo
_ESQUELETO_FONDO = ft.Colors.SURFACE_CONTAINER_HIGHEST
# El brillo sale de la superficie MÁS clara del tema. En claro queda por encima
# del fondo de la barra y se lee como un reflejo; en oscuro esa misma superficie
# es la más profunda y el brillo pasa a ser una sombra que recorre la barra. Se
# acepta la inversión a cambio de no escribir un hex: no hay rol de Material que
# sea «siempre más claro», y el movimiento se percibe igual en ambos sentidos.
_ESQUELETO_BRILLO = ft.Colors.with_opacity(0.55,
                                           ft.Colors.SURFACE_CONTAINER_LOWEST)
# Ancho de la barra como fracción del espacio útil, y piso en px para cuando la
# tarjeta todavía no se ha medido.
_ESQUELETO_FRACCION = 0.78
_ESQUELETO_ANCHO_MIN = 160
# Piso al MEDIR, mucho más bajo que el de arranque: las barras de una métrica
# comparten la mitad de una tarjeta y con el piso alto se desbordarían.
_ESQUELETO_ANCHO_PISO = 24
_ESQUELETO_ALTO_MIN = 20
# Cuántas barras pone cada tarjeta de lista: las que va a llenar el servicio.
# El podio es de tres puestos (`TOP 3` en el SP) y la bitácora enseña cuatro
# movimientos, que es lo que cabe en su tarjeta.
_PUESTOS_PODIO = 3
_EVENTOS_BITACORA = 4
# El desglose por tipo va en dos columnas: 7 renglones x 2 = 14 barras, que es
# lo que ocupan los trece tipos del catálogo más el «sin identificar».
_FILAS_TIPO_ESQUELETO = 7
# Alto de cada barra, a juego con el renglón al que sustituye: la medalla del
# podio mide 20px, y un evento 44 (ícono de 32 más su relleno de 6+6).
_ALTO_BARRA_PODIO = 20
_ALTO_BARRA_EVENTO = 36
# Alto de una línea de texto respecto a su tamaño de fuente. Es lo que hace que
# la barra ocupe el mismo hueco que la cifra a la que sustituye.
_ALTO_LINEA = 1.2


def _gradiente_esqueleto(pos: float) -> ft.LinearGradient:
    """Brillo centrado en `pos`, medido en el eje de alineación del contenedor.

    Los topes de la barra son -1 y 1, y el degradado abarca justo su ancho, así
    que `pos = -2` lo deja entero fuera por la izquierda y `pos = 2` fuera por la
    derecha. Es lo que hace que el salto del final del ciclo al principio sea
    invisible: en los dos extremos la barra se ve lisa.
    """
    return ft.LinearGradient(
        begin=ft.Alignment(pos - 1, 0), end=ft.Alignment(pos + 1, 0),
        colors=[_ESQUELETO_FONDO, _ESQUELETO_BRILLO, _ESQUELETO_FONDO])


class Esqueleto:
    """Grupo de barras de carga con un brillo que las recorre en horizontal.

    Sustituye al contenido mientras se consulta. Dice dos cosas que un
    `ProgressRing` no dice: que la espera es por ESE dato y no por la pantalla
    entera, y qué forma va a tener cuando llegue —de ahí que se pida una barra
    por renglón que va a aparecer: tres para el podio, cuatro para la bitácora—.

    **Un solo bucle para todas las barras del grupo.** Se mueven en el mismo
    fotograma y se refresca su columna, no cada una: con un bucle por barra,
    siete barras a 15 fps serían siete refrescos por fotograma en vez de uno.

    El brillo se mueve por FOTOGRAMAS desde Python, igual que el conteo de los
    totales (`SeccionDashboard._contar`), y no dejándoselo a la animación de
    Flutter. Un `Container.animate` sobre el degradado sale más barato —una
    actualización por vuelta— pero el salto del final del ciclo al principio se
    animaría hacia atrás, y el barrido dejaría de ser de un solo sentido.

    Uso:
        esq = Esqueleto(3, ancho_completo=True)
        columna.controls.append(esq.control)
        esq.medir(alto=20)
        esq.arrancar()
        page.run_task(esq.animar)   # ... y `esq.detener()` al terminar
    """

    def __init__(self, filas: int = 1, *, columnas: int = 1,
                 espacio: int = _GAP_SM, radio: int = _RADIO_INTERNO,
                 ancho_completo: bool = False, expandir: bool = False,
                 alineacion=ft.MainAxisAlignment.START) -> None:
        """`filas` x `columnas` es la rejilla de barras: el desglose por tipo se
        reparte en dos columnas, así que su esqueleto también.

        `ancho_completo` estira las barras a lo ancho de su caja, para las que
        sustituyen a un renglón de lista; sin él cada barra usa el ancho que le
        dé `medir`, como la cifra de un KPI.

        `expandir` hace que el grupo se quede con el alto sobrante, y
        `alineacion` reparte las barras dentro de él: son los dos ajustes que
        hacen que el esqueleto ocupe exactamente el hueco de la lista a la que
        sustituye, sin descuadrar la tarjeta al aparecer.
        """
        self._ancho_completo = ancho_completo
        columnas = max(1, columnas)
        self.barras = [
            ft.Container(
                height=_ESQUELETO_ALTO_MIN,
                # Con `ancho_completo` el ancho lo manda el contenedor: en una
                # sola columna, el STRETCH; en varias, el `expand` que reparte
                # la fila a partes iguales, como hacen las celdas del desglose.
                width=None if ancho_completo else _ESQUELETO_ANCHO_MIN,
                expand=True if (ancho_completo and columnas > 1) else None,
                border_radius=radio, gradient=_gradiente_esqueleto(-2.0))
            for _ in range(max(1, filas) * columnas)]
        hijos: list = self.barras
        if columnas > 1:
            hijos = [ft.Row(self.barras[i:i + columnas], spacing=_GAP,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER)
                     for i in range(0, len(self.barras), columnas)]
        # Un solo control para montar, y un solo `update` por fotograma.
        self.control = ft.Column(
            hijos, spacing=espacio, visible=False,
            expand=expandir or None, tight=not expandir, alignment=alineacion,
            horizontal_alignment=(ft.CrossAxisAlignment.STRETCH if ancho_completo
                                  else ft.CrossAxisAlignment.START))
        # Dos guardas, y las dos hacen falta:
        #
        # `_activo` es la INTENCIÓN, y se apaga en `detener`. Sin ella, una
        # consulta que responde antes de que el planificador llegue a lanzar el
        # bucle dejaba el brillo girando para siempre: `detener` ya había pasado
        # y `animar` arrancaba después, sin enterarse.
        #
        # `_vuelta` es la GENERACIÓN, como en el conteo animado: dos cargas
        # encimadas dejarían dos bucles pintando las mismas barras a distinto
        # ritmo.
        self._activo = False
        self._vuelta = 0

    def arrancar(self) -> None:
        """Declara que las barras deben animarse. NO lanza el bucle: eso lo hace
        quien tenga página, con `run_task(esq.animar)`."""
        self._activo = True
        self._vuelta += 1

    def medir(self, alto: float, ancho: float | None = None) -> None:
        """Fija el tamaño de las barras.

        `ancho=None` deja el que tuvieran, que es lo que corresponde cuando lo
        manda el contenedor. El piso es pequeño a propósito: solo evita que una
        cuenta a la baja deje una barra invisible, sin llegar a desbordar la
        tarjeta —el ancho de arranque, mucho mayor, lo fija el constructor para
        cuando todavía no se ha medido nada—.
        """
        for barra in self.barras:
            barra.height = max(_ESQUELETO_ALTO_MIN, round(alto))
            if not self._ancho_completo and ancho is not None:
                barra.width = max(_ESQUELETO_ANCHO_PISO, round(ancho))

    def paso(self, pos: float) -> None:
        """Coloca el brillo, SIN refrescar.

        Separado de `animar` para que una tarjeta con varios grupos —la de
        totales tiene tres— los mueva todos en el mismo fotograma y mande un
        solo refresco, en vez de un bucle y un mensaje por grupo.
        """
        gradiente = _gradiente_esqueleto(pos)
        for barra in self.barras:
            barra.gradient = gradiente

    def set_espacio(self, espacio: int) -> None:
        """Separación entre barras, para seguir a la lista que sustituyen."""
        self.control.spacing = espacio

    async def animar(self, *, tambien: tuple = (), refrescar=None) -> None:
        """Pasea el brillo hasta que alguien llame a `detener`.

        ADOPTA la generación vigente en vez de reclamar una nueva: así, si
        `detener` llegó entre el `arrancar` y esta corrutina, no entra al bucle.

        `tambien` son otros grupos que se mueven EN EL MISMO fotograma, y
        `refrescar` el repintado que los abarca a todos —normalmente el de la
        tarjeta entera—. Sin eso, una tarjeta con tres grupos necesitaría tres
        bucles y mandaría tres mensajes por fotograma en vez de uno.
        """
        mia = self._vuelta
        pasos = max(2, round(_ESQUELETO_MS / 1000 * _ESQUELETO_FPS))
        intervalo = _ESQUELETO_MS / 1000 / pasos
        pintar = refrescar or (lambda: Tarjeta._actualizar(self.control))
        i = 0
        while self._activo and mia == self._vuelta:
            # De -2 a +2: en los dos extremos el degradado queda entero fuera de
            # la barra, así que el salto de la última posición a la primera no se
            # ve. Es lo que permite repetir sin animar la vuelta atrás.
            #
            # Todas las barras van EN FASE, que es lo estándar en un esqueleto:
            # desfasarlas obligaría a alargar el recorrido para que siguieran
            # quedando lisas a la vez en los extremos, y con ello a que el salto
            # volviera a notarse.
            pos = -2.0 + 4.0 * i / pasos
            self.paso(pos)
            for otro in tambien:
                otro.paso(pos)
            pintar()
            await asyncio.sleep(intervalo)
            # `pasos + 1` posiciones, no `pasos`: el ciclo tiene que INCLUIR el
            # +2 final. Cortando en el paso anterior el brillo aún asomaba por el
            # borde derecho al reiniciar, y ahí el salto sí se veía.
            i = (i + 1) % (pasos + 1)

    def detener(self) -> None:
        """Corta el bucle, esté corriendo o por arrancar.

        Idempotente: se llama desde todas las salidas, con dato y con error, y
        llamarlo de más no cuesta nada.
        """
        self._activo = False


# Por debajo de este ancho, el ícono en pastilla estorba más de lo que aporta.
_ANCHO_CON_ICONO = 200

# Ancho medio de un carácter de la cifra, como fracción de su tamaño de fuente.
# Flet no expone medición de texto, así que el ajuste al ancho se estima; el
# valor va HOLGADO respecto al avance real de un dígito (~0.55em en la
# tipografía del tema) porque comas y puntos son bastante más angostos: así el
# error cae siempre del lado de encoger de más, nunca del de recortar.
_RATIO_CARACTER = 0.62
# Piso de la cifra. Por debajo deja de leerse como el dato principal de la
# tarjeta, y a esa altura conviene más que el contenedor crezca.
_TAM_VALOR_MIN = 16

# Relleno vertical de un evento de la bitácora. Con 6 el evento mide 48px
# (36 de contenido + 12), que sigue el ritmo de 4px aunque el relleno en sí no lo
# haga; con los 8 de `_GAP_SM` medía 52 y el cuarto movimiento ya no entraba en
# la tarjeta. Ver TarjetaActividad.
_PAD_EVENTO = 6

# Métricas del desglose por tipo, para repartir sus filas por el alto del panel.
_ALTO_FILA_TIPO = 20      # una línea: ícono de 16 y texto de 12
_LINEA_ROTULO = 16        # LABEL_MEDIUM del tema (12px x 1.333)
_ESPACIO_TIPO_MAX = 16    # tope: más separación y las filas dejan de leerse
                          # como un grupo y parecen listas sueltas

# No repintar por cambios menores a esto (evita trabajo durante el arrastre),
# mismo criterio que _UMBRAL_REMEDIR en ui/tabla_responsiva.py.
_UMBRAL = 2.0


class Tarjeta:
    """Superficie base: fondo, borde, radio, elevación y hover.

    No fija tamaño. El contenido lo arma la subclase en `self._cuerpo`.
    `acento=True` agrega la banda vertical izquierda de 4px que el diseño usa
    para distinguir unas tarjetas de otras; `color_acento` elige su rol.
    """

    def __init__(self, *, acento: bool = False, padding: int = _PAD,
                 color_acento: str = ft.Colors.PRIMARY_CONTAINER) -> None:
        self._cuerpo = ft.Column(spacing=_GAP, expand=True)
        self._ancho = 0.0
        self._alto = 0.0
        # El realce toma el color de la tarjeta, aunque no lleve banda visible:
        # `color_acento` es su identidad cromática, la pinte o no.
        self._color_hover = color_acento
        self._sombra_hover = _sombra_hover(color_acento)

        # Se guarda la referencia: el padding es una de las cosas que encoge al
        # adaptar la densidad (ver `_chrome`).
        interior = self._interior = ft.Container(
            content=self._cuerpo, padding=padding, expand=True)
        if acento:
            # La banda va DENTRO y recortada por el radio, no como un borde
            # izquierdo grueso: Flutter ignora `border_radius` cuando el borde no
            # es uniforme, y la tarjeta saldría con las esquinas rectas.
            banda = ft.Container(width=_ACENTO, bgcolor=color_acento)
            contenido: ft.Control = ft.Row(
                [banda, interior], spacing=0,
                vertical_alignment=ft.CrossAxisAlignment.STRETCH)
        else:
            contenido = interior

        self.control = ft.Container(
            content=contenido,
            bgcolor=ft.Colors.SURFACE_CONTAINER_LOWEST,
            # Borde UNIFORME: es la condición para que el radio se aplique.
            border=ft.Border.all(1, _BORDE_REPOSO),
            border_radius=_RADIO,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            shadow=_SOMBRA_N1,
            on_hover=self._hover,
            on_size_change=self._al_medir,
            size_change_interval=50,
            # `animate` sobre un Container anima su DECORACIÓN completa —fondo,
            # borde, radio y sombra—, así que el realce del hover entra y sale
            # suave sin tener que animar cada propiedad por separado.
            animate=ft.Animation(160, ft.AnimationCurve.EASE_OUT),
            # La escala va por su propio canal: `animate` no la cubre.
            scale=1.0,
            animate_scale=ft.Animation(160, ft.AnimationCurve.EASE_OUT),
        )

    def _hover(self, e) -> None:
        encima = puntero_encima(e)
        self.control.shadow = self._sombra_hover if encima else _SOMBRA_N1
        self.control.border = ft.Border.all(
            1, self._color_hover if encima else _BORDE_REPOSO)
        self.control.scale = _ESCALA_HOVER if encima else 1.0
        self._actualizar(self.control)

    def refrescar(self) -> None:
        """Repinta la tarjeta. Para quien la llena desde fuera y no quiere
        refrescar la pantalla entera solo por una de ellas."""
        self._actualizar(self.control)

    # ------------------------------------------------------------ medición
    def _al_medir(self, e: ft.LayoutSizeChangeEvent) -> None:
        if abs(e.width - self._ancho) < _UMBRAL and abs(e.height - self._alto) < _UMBRAL:
            return
        self._ancho, self._alto = e.width, e.height
        self._adaptar(e.width, e.height)
        # Se refresca la RAÍZ, no `self._cuerpo`: `_chrome` toca el padding de
        # `self._interior`, que es su PADRE, y un update sobre el cuerpo no
        # arrastra al padre. Si no, la tarjeta se queda con el padding viejo
        # hasta que otro evento (el hover) refresque la raíz.
        self._actualizar(self.control)

    def _adaptar(self, ancho: float, alto: float) -> None:
        """Ajusta la densidad al espacio disponible. Lo implementa la subclase."""

    def _chrome(self, padding, gap: int) -> None:
        """Ajusta el 'aire' de la tarjeta: margen interno y separación vertical.

        `padding` acepta un número (todos los lados por igual) o un `ft.Padding`,
        para las tarjetas que necesitan recortar SOLO el eje vertical y conservar
        el horizontal alineado con el resto del tablero.
        """
        self._interior.padding = padding
        self._cuerpo.spacing = gap

    @staticmethod
    def _actualizar(control: ft.Control) -> None:
        """`update()` tolerante: la tarjeta puede no estar montada todavía."""
        try:
            control.update()
        except (RuntimeError, AssertionError, AttributeError):
            pass


# --- Piezas compartidas ---------------------------------------------------
def _pastilla(icono, color: str, fondo: str, *, lado: int = 36,
              tam: int = 20) -> ft.Container:
    """Ícono sobre un cuadro redondeado. El encabezado de casi toda tarjeta."""
    return ft.Container(
        content=ft.Icon(icono, size=tam, color=color), bgcolor=fondo,
        width=lado, height=lado, border_radius=_RADIO_INTERNO,
        alignment=ft.Alignment(0, 0))


def _rotulo(texto: str, color: str = ft.Colors.ON_SURFACE_VARIANT,
            estilo=ft.TextThemeStyle.LABEL_MEDIUM,
            expandir: bool = True) -> ft.Text:
    """Rótulo en mayúsculas: identifica la tarjeta sin competir con su valor.

    `expandir` SOLO vale dentro de un `Row`, donde estira el rótulo a lo ancho
    para que la elipsis le toque a él y no empuje al ícono fuera de la tarjeta.

    Dentro de un `Column` hay que pasarlo en False: ahí `expand` es VERTICAL y el
    rótulo se queda con todo el alto sobrante, empujando lo que venga debajo
    hasta el fondo del contenedor. Es exactamente lo que hacía que el desglose
    por tipo apareciera pegado al borde inferior del panel.
    """
    return ft.Text(texto.upper(), theme_style=estilo, color=color,
                   no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS,
                   expand=expandir or None)


class TarjetaTotalActivos(Tarjeta):
    """Widget principal: total de activos, dados de baja y desglose por tipo.

    Es la tarjeta grande del mockup (`ejemplos/code.html`), la única que abarca
    dos filas. Su contenido está ordenado por importancia y `_adaptar` lo suelta
    en ese orden inverso: primero el desglose por tipo (lo más alto), luego las
    notas de variación, y por último encoge los números.
    """

    # Alturas a las que deja de caber cada parte. El umbral del panel sale de
    # sumar su contenido con el interlineado MÍNIMO: relleno (2x16) + rótulo (16)
    # + separación (8) + 7 filas de 20 + 6 huecos de 4 = 220px de panel, y el
    # panel arranca 200px por debajo del borde de la tarjeta (relleno 2x24 +
    # encabezado 36 + métricas 84 + dos gaps de 16). De ahí los 420, redondeados
    # a 440 de margen: por debajo, las filas desbordarían por abajo en vez de
    # apretarse.
    _ALTO_CON_PANEL = 440
    _ALTO_CON_NOTAS = 190

    def __init__(self, total: str, bajas: str, icono, *,
                 variacion_total: str = "", nota_total: str = "",
                 variacion_bajas: str = "", nota_bajas: str = "",
                 titulo: str = "Total de Activos Fijos",
                 titulo_bajas: str = "Dados de baja",
                 titulo_panel: str = "Por tipo",
                 tipos: list[tuple] | None = None,
                 on_total=None, on_bajas=None,
                 tooltip_desglose: str = "Mostrar desglose del total") -> None:
        """`on_total` / `on_bajas` hacen pulsable cada cifra junto a su chevron.

        Sin manejador no aparece el chevron: un ícono de "ir a" que no lleva a
        ningún lado es peor que no tenerlo.
        """
        super().__init__(acento=True)

        self._encabezado = ft.Row(
            [_rotulo(titulo, ft.Colors.PRIMARY, ft.TextThemeStyle.LABEL_LARGE),
             _pastilla(icono, ft.Colors.ON_SECONDARY_CONTAINER,
                       ft.Colors.SECONDARY_FIXED)],
            vertical_alignment=ft.CrossAxisAlignment.CENTER)
        self._pastilla_enc = self._encabezado.controls[1]

        # --- Métricas: total a la izquierda, bajas a la derecha ------------
        # Cada cifra va dentro de una zona pulsable junto a su chevron, para que
        # el número TAMBIÉN sea el disparador: un blanco de 48px de alto se
        # acierta sin apuntar, y un ícono suelto de 26 obliga a buscarlo.
        self._total = ft.Text(total, size=48, weight=ft.FontWeight.W_900,
                              color=ft.Colors.PRIMARY, no_wrap=True,
                              overflow=ft.TextOverflow.ELLIPSIS)
        self._chevron_total = ft.Icon(ft.Icons.CHEVRON_RIGHT, size=26,
                                      color=ft.Colors.PRIMARY)
        self._disparador_total = _zona_pulsable(
            [self._total, self._chevron_total], tooltip_desglose, on_total)
        self._nota_total = _nota(variacion_total, nota_total, ft.Colors.SECONDARY)

        # También va en un Column, junto a la cifra y su nota.
        self._rotulo_bajas = _rotulo(titulo_bajas, expandir=False)
        self._bajas = ft.Text(bajas, size=32, weight=ft.FontWeight.W_700,
                              color=ft.Colors.ON_SURFACE_VARIANT, no_wrap=True,
                              overflow=ft.TextOverflow.ELLIPSIS)
        self._chevron_bajas = ft.Icon(ft.Icons.CHEVRON_RIGHT, size=20,
                                      color=ft.Colors.ON_SURFACE_VARIANT)
        self._disparador_bajas = _zona_pulsable(
            [self._bajas, self._chevron_bajas], tooltip_desglose, on_bajas)
        self._nota_bajas = _nota(variacion_bajas, nota_bajas, ft.Colors.ERROR)

        # El separador es un borde izquierdo sobre la columna derecha, no un
        # VerticalDivider: este último exige alto acotado y aquí la fila lo toma
        # de su contenido.
        # Un esqueleto por cifra, en el hueco de su zona pulsable.
        self._esq_total = Esqueleto()
        self._esq_bajas = Esqueleto()

        derecha = ft.Container(
            ft.Column([self._rotulo_bajas, self._disparador_bajas,
                       self._esq_bajas.control, self._nota_bajas],
                      spacing=_GAP_XS, tight=True, expand=True),
            padding=ft.Padding.only(left=_GAP), expand=True,
            border=ft.Border(left=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)))
        self._metricas = ft.Row(
            [ft.Column([self._disparador_total, self._esq_total.control,
                        self._nota_total],
                       spacing=_GAP_XS, tight=True, expand=True),
             derecha],
            spacing=_GAP, vertical_alignment=ft.CrossAxisAlignment.START)

        # --- Panel "Por tipo" ----------------------------------------------
        # `expandir=False`: va dentro de un Column (ver `_rotulo`).
        self._rotulo_panel = _rotulo(titulo_panel, expandir=False)
        # Las filas van pegadas arriba y es el INTERLINEADO el que se estira para
        # repartirlas (`_al_medir_panel`). No se usa `MainAxisAlignment`: en
        # cuanto el contenido pasa del alto de la caja, el reparto se anula y las
        # filas se apelmazan, que es peor que no repartir.
        self._filas_tipo = ft.Column(spacing=_GAP_XS, tight=True)
        # Los `Text` de los conteos, en el mismo orden que las filas: es lo que
        # reescribe `set_conteos` durante la animación.
        self._conteos_tipo: list[ft.Text] = []
        # `expand`: el panel se queda con TODO el alto que sobre tras el
        # encabezado y las métricas, en vez de medir lo que ocupen sus filas y
        # dejar un hueco muerto al pie de la tarjeta. Como el sobrante cambia con
        # el tamaño de la celda, fijarlo en píxeles obligaría a recalcularlo cada
        # vez que se toque la rejilla.
        # Sin `tight`: la columna llena el panel y el rótulo queda arriba.
        # El panel se mide APARTE de la tarjeta: cuánto sobra para las filas
        # depende del encabezado, las métricas y el rótulo, y estimarlos desde
        # `_adaptar` dejaba las filas desbordando por abajo.
        # 14 barras en dos columnas de 7: la misma rejilla en la que `set_tipos`
        # reparte los trece tipos del catálogo más el «sin identificar». El
        # rótulo del panel NO se esqueletea —es rótulo, no dato—, así que solo
        # se releva a `_filas_tipo`.
        self._esq_tipos = Esqueleto(
            _FILAS_TIPO_ESQUELETO, columnas=2, espacio=_GAP_XS,
            ancho_completo=True)
        self._panel = ft.Container(
            # `alignment=START` en el eje VERTICAL y nada en el horizontal: el
            # contenido se pega arriba, pero las filas siguen ocupando todo el
            # ancho. Fijar `horizontal_alignment` aquí —o `alignment` en el
            # Container— les daría su ancho natural y el conteo dejaría de irse a
            # la derecha (ver la trampa del STRETCH en ui/COMPONENTES.md).
            ft.Column([self._rotulo_panel, self._filas_tipo,
                       self._esq_tipos.control],
                      spacing=_GAP_SM, alignment=ft.MainAxisAlignment.START),
            bgcolor=ft.Colors.SURFACE_CONTAINER_LOW, padding=_GAP,
            border_radius=_RADIO_INTERNO, expand=True,
            on_size_change=self._al_medir_panel, size_change_interval=50)

        self._cuerpo.controls.extend(
            [self._encabezado, self._metricas, self._panel])
        # Estado de carga y si el alto da para notas: los dos deciden qué se ve,
        # así que se guardan y los aplica `_pintar_notas`.
        self._carga_activa = False
        self._con_notas = True
        self.set_tipos(tipos or [])

    # ------------------------------------------------------------- carga
    def cargando(self, activo: bool) -> None:
        """Pone los esqueletos en lugar de las cifras y del desglose.

        NO arranca el brillo: el bucle es asíncrono y lo lanza quien tenga
        página (`page.run_task(tarjeta.animar_carga)`).
        """
        self._carga_activa = activo
        for esq in self._esqueletos():
            esq.arrancar() if activo else esq.detener()
            esq.control.visible = activo
        self._disparador_total.visible = not activo
        self._disparador_bajas.visible = not activo
        self._filas_tipo.visible = not activo
        # Las notas también son dato —«20 altas este mes»—, así que se van con
        # las cifras: dejarlas sería afirmar del ámbito nuevo algo que se midió
        # sobre el anterior.
        self._pintar_notas()

    async def animar_carga(self) -> None:
        """Pasea el brillo de los TRES grupos con un solo bucle.

        Uno por grupo mandaría tres mensajes por fotograma para repintar partes
        de la misma tarjeta; así van en fase y con un refresco.
        """
        await self._esq_total.animar(
            tambien=(self._esq_bajas, self._esq_tipos),
            refrescar=self.refrescar)

    def _esqueletos(self) -> tuple:
        return (self._esq_total, self._esq_bajas, self._esq_tipos)

    def _pintar_notas(self) -> None:
        """Único punto que decide si se ven las notas de variación.

        Dos condiciones independientes: `_con_notas` dice si CABEN (lo mide
        `_adaptar`), `_hay_nota` si hay algo que decir, y la carga las tapa
        aunque las otras dos den el visto bueno.
        """
        for nota in (self._nota_total, self._nota_bajas):
            nota.visible = (self._con_notas and _hay_nota(nota)
                            and not self._carga_activa)

    def set_totales(self, total: str, bajas: str) -> None:
        """Actualiza las dos cifras. No toca sus tamaños: los manda `_adaptar`
        según el alto de la tarjeta, y fijarlos aquí desharía ese cálculo.

        NO refresca, igual que `set_tipos` y los setters de las demás tarjetas:
        quien reparte los datos suele llenar varias de una vez y le sale más
        barato un solo `update()` sobre la pantalla que uno por tarjeta.
        """
        self._total.value = total
        self._bajas.value = bajas

    def set_notas(self, variacion_total: str = "", nota_total: str = "",
                  variacion_bajas: str = "", nota_bajas: str = "") -> None:
        """Reescribe las dos notas que acompañan a las cifras.

        Cada una es una cifra resaltada más su explicación («147» + «altas este
        mes»). Con ambas vacías la nota se oculta, que es lo que corresponde
        mientras no hay datos. Tampoco refresca, como el resto de setters.
        """
        _pintar_nota(self._nota_total, variacion_total, nota_total)
        _pintar_nota(self._nota_bajas, variacion_bajas, nota_bajas)

    def set_tipos(self, tipos: list[tuple]) -> None:
        """Repuebla el desglose.

        `tipos` es [(icono, nombre, cantidad), ...] y admite dos elementos más
        por fila —`destacado` y `atenuado`— para marcar el tipo por el que se
        filtra (ver `_fila_tipo`).

        Se reparte en DOS columnas como el mockup: con 13 tipos, una sola
        columna duplicaría el alto de la tarjeta.
        """
        filas = []
        self._conteos_tipo = []
        for i in range(0, len(tipos), 2):
            celdas = []
            for t in tipos[i:i + 2]:
                celda, conteo = _fila_tipo(*t)
                celdas.append(celda)
                self._conteos_tipo.append(conteo)
            filas.append(ft.Row(
                # Relleno para que la última fila impar no estire su única celda.
                celdas + ([ft.Container(expand=True)] if len(celdas) == 1 else []),
                spacing=_GAP, vertical_alignment=ft.CrossAxisAlignment.CENTER))
        self._filas_tipo.controls = filas

    def set_conteos(self, cantidades: list[str]) -> None:
        """Reescribe SOLO los conteos del desglose, sin tocar las filas.

        Es lo que usa el conteo animado. `zip` corta por el más corto, así que un
        desajuste entre la lista y las filas montadas deja el resto como estaba en
        vez de reventar a mitad de la animación.
        """
        for texto, cantidad in zip(self._conteos_tipo, cantidades):
            texto.value = cantidad

    def _al_medir_panel(self, e: ft.LayoutSizeChangeEvent) -> None:
        """Estira el INTERLINEADO del desglose hasta llenar el panel.

        Se mide el panel y no la tarjeta a propósito: cambiar el interlineado
        altera el alto de las filas, pero no el del panel —lo fija su `expand`—,
        así que no hay realimentación y la medida no oscila.
        """
        # El esqueleto tiene un número FIJO de renglones, así que su reparto se
        # calcula aparte y no depende de cuántos tipos haya traído la consulta:
        # es lo que hace que al relevar a las filas nada se mueva de sitio.
        self._esq_tipos.set_espacio(
            self._espacio_tipos(_FILAS_TIPO_ESQUELETO, e.height))

        filas = self._filas_tipo.controls
        espacio = self._espacio_tipos(len(filas), e.height)
        # Mismo criterio que `_al_medir`: no repintar por menos de un píxel.
        if abs(espacio - (self._filas_tipo.spacing or 0)) < 1:
            return
        self._filas_tipo.spacing = espacio
        self._actualizar(self._panel)

    @staticmethod
    def _espacio_tipos(cuantas: int, alto: float) -> float:
        """Interlineado que reparte `cuantas` filas por el alto del panel.

        Con menos de dos filas no hay huecos que repartir, pero SÍ hay que
        devolver el interlineado a su piso: si venimos de un desglose de trece
        tipos, quedó estirado a una decena de píxeles y el renglón único
        aparecería separado del rótulo, como colgando del panel.
        """
        if cuantas < 2 or not alto:
            return _GAP_XS
        # Lo que queda para las filas: el panel menos su relleno y su rótulo.
        disponible = alto - 2 * _GAP - _LINEA_ROTULO - _GAP_SM
        libre = disponible - cuantas * _ALTO_FILA_TIPO
        return max(_GAP_XS, min(_ESPACIO_TIPO_MAX, libre / (cuantas - 1)))

    def _adaptar(self, ancho: float, alto: float) -> None:
        con_panel = alto >= self._ALTO_CON_PANEL
        con_notas = alto >= self._ALTO_CON_NOTAS

        if con_panel:
            pad, gap, total, bajas = _PAD, _GAP, 48, 32
        elif con_notas:
            pad, gap, total, bajas = 18, _GAP_SM, 40, 26
        else:
            pad, gap, total, bajas = 12, _GAP_XS, 30, 22
        self._chrome(pad, gap)

        self._panel.visible = con_panel
        self._con_notas = con_notas
        self._pintar_notas()
        self._rotulo_bajas.visible = con_notas
        self._pastilla_enc.visible = ancho >= _ANCHO_CON_ICONO
        self._total.size = total
        self._bajas.size = bajas
        # El chevron se escala CON su cifra: fijo, en una tarjeta baja acabaría
        # siendo más alto que el número al que acompaña.
        self._chevron_total.size = round(total * 0.55)
        self._chevron_bajas.size = round(bajas * 0.62)
        # Cada barra de métrica ocupa el hueco de SU cifra. Las dos columnas se
        # reparten el ancho útil menos la separación, y la de la derecha suma su
        # relleno; la barra se queda por debajo para no rozar el separador.
        util = ancho - 2 * pad
        columna = max(_ESQUELETO_ANCHO_PISO, (util - _GAP) / 2 - _GAP)
        self._esq_total.medir(total * _ALTO_LINEA, columna * 0.9)
        self._esq_bajas.medir(bajas * _ALTO_LINEA, columna * 0.75)
        # Las del desglose miden EXACTAMENTE lo que un renglón de tipo: es la
        # misma altura con la que `_espacio_tipos` reparte el panel, así que el
        # relevo no mueve nada.
        self._esq_tipos.medir(_ALTO_FILA_TIPO)


def _mensaje_vacio(texto: str, icono=ft.Icons.INBOX, *,
                   tam_icono: int = 22, tam_texto: int = 12) -> ft.Control:
    """Aviso centrado para cuando una tarjeta no tiene nada que listar.

    Una tarjeta en blanco se lee como un fallo de carga: el usuario no sabe si
    la consulta falló, si sigue cargando o si de verdad no hay datos. El texto
    responde eso último de forma explícita.

    Va envuelto en un `Row` para CENTRARLO a lo ancho. Un `Column` toma su ancho
    natural —el del texto— y dentro de una lista se queda pegado a la izquierda:
    su `horizontal_alignment` solo centra el ícono sobre el texto, no el bloque
    dentro de la tarjeta. Un `Row`, en cambio, ocupa todo el ancho disponible.

    Sin `expand` en el eje vertical: quien lo coloque decide si centra la caja.
    Estirarlo aquí lo convertiría en el que se come el alto sobrante de su
    columna, que es la trampa que empujaba el desglose al fondo (ver `_rotulo`).
    """
    return ft.Row(
        [ft.Column(
            [ft.Icon(icono, size=tam_icono, color=ft.Colors.OUTLINE_VARIANT),
             ft.Text(texto, size=tam_texto, color=ft.Colors.ON_SURFACE_VARIANT,
                     text_align=ft.TextAlign.CENTER)],
            spacing=_GAP_XS, tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER)],
        alignment=ft.MainAxisAlignment.CENTER)


def _zona_pulsable(controles: list, tooltip: str, on_click) -> ft.Control:
    """Envuelve una cifra y su chevron en UNA sola zona pulsable.

    Sin manejador devuelve la cifra tal cual, sin chevron ni tooltip.

    El relleno no lleva lado izquierdo a propósito: cualquier valor ahí correría
    la cifra respecto al rótulo y la nota que la acompañan en su columna.
    """
    if not callable(on_click):
        return controles[0]
    return puntero_mano(ft.Container(
        ft.Row(controles, spacing=_GAP_XS, tight=True,
               vertical_alignment=ft.CrossAxisAlignment.CENTER),
        on_click=on_click, ink=True, tooltip=tooltip,
        border_radius=_RADIO_INTERNO,
        padding=ft.Padding.only(right=_GAP_SM, top=2, bottom=2)))


def _nota(variacion: str, texto: str, color: str) -> ft.Text:
    """Nota de variación: el porcentaje resaltado y el resto en tono secundario.

    Va en un `TextSpan` aparte para poder darle color y peso propios sin partir
    la frase en dos controles que luego habría que alinear.
    """
    return ft.Text(
        spans=[ft.TextSpan(variacion or "",
                           ft.TextStyle(color=color, weight=ft.FontWeight.BOLD)),
               ft.TextSpan(f" {texto}" if texto else "")],
        theme_style=ft.TextThemeStyle.BODY_MEDIUM,
        color=ft.Colors.ON_SURFACE_VARIANT,
        no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS,
        visible=bool(variacion or texto))


def _pintar_nota(nota: ft.Text, variacion: str, texto: str) -> None:
    """Reescribe una nota ya montada, conservando el color de su construcción.

    Se MUTAN los spans en vez de rehacerlos, igual que en el resto del módulo: el
    color y el peso viven en el `TextStyle` del primer span y recrearlos obligaría
    a que quien actualiza los datos supiera de qué color es cada nota.
    """
    nota.spans[0].text = variacion or ""
    nota.spans[1].text = f" {texto}" if texto else ""
    nota.visible = _hay_nota(nota)


def _hay_nota(nota: ft.Text) -> bool:
    """Si la nota tiene algo que decir. Se mira el CONTENIDO, nunca `visible`:
    ese es el efecto que decide `_adaptar`, y leerlo sería leer su propia salida."""
    return bool(nota.spans[0].text or nota.spans[1].text)


def _fila_tipo(icono, nombre: str, cantidad: str,
               destacado: bool = False,
               atenuado: bool = False) -> tuple[ft.Control, ft.Text]:
    """Una línea del desglose: ícono + nombre a la izquierda, conteo a la derecha.

    `destacado` marca el tipo por el que se está filtrando y `atenuado` apaga a
    los demás. Se apagan en vez de esconderse para no perder la comparación: el
    valor de este panel es ver cuánto pesa un tipo FRENTE al resto.

    Devuelve también el `Text` del conteo para que el conteo animado pueda
    reescribirlo sin rehacer la fila: con 13 tipos, recrearlas en cada fotograma
    serían cientos de controles nuevos en menos de un segundo.
    """
    tinta = (ft.Colors.PRIMARY if destacado
             else ft.Colors.OUTLINE if atenuado
             else ft.Colors.ON_SURFACE)
    peso = ft.FontWeight.BOLD if destacado else None
    conteo = ft.Text(cantidad, size=12, weight=ft.FontWeight.BOLD, color=tinta)
    fila = ft.Row(
        [ft.Icon(icono, size=16,
                 color=ft.Colors.OUTLINE if atenuado else ft.Colors.PRIMARY),
         ft.Text(nombre, size=12, color=tinta, weight=peso, expand=True,
                 no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS, tooltip=nombre),
         conteo],
        spacing=_GAP_XS, expand=True,
        vertical_alignment=ft.CrossAxisAlignment.CENTER)
    return fila, conteo


class TarjetaValor(Tarjeta):
    """KPI de una sola cifra: pastilla + rótulo arriba, valor grande y unidad.

    Es "Valor de Activos Fijos" del mockup. A diferencia del desglose, aquí no
    hay nada accesorio que soltar, así que `_adaptar` solo escala.
    """

    def __init__(self, rotulo: str, valor: str, icono, *, unidad: str = "",
                 vacio: str = "", accion: str = "", on_accion=None,
                 color_icono: str = ft.Colors.ON_SECONDARY_CONTAINER,
                 fondo_icono: str = ft.Colors.SECONDARY_FIXED,
                 color_acento: str = ft.Colors.SECONDARY) -> None:
        """`accion` es el TOOLTIP del chevron, no un rótulo visible.

        Sin `on_accion` no hay chevron ni zona pulsable: la cifra se queda como
        estaba (ver `_zona_pulsable`).
        """
        super().__init__(acento=True, color_acento=color_acento)

        # Pastilla más chica y rótulo pegado a la cifra: el título es el
        # acompañante, no el protagonista, y cada píxel que suelta se lo lleva el
        # número, que es lo que se viene a leer.
        self._pastilla = _pastilla(icono, color_icono, fondo_icono, lado=28,
                                   tam=16)
        self._encabezado = ft.Row([self._pastilla, _rotulo(rotulo)],
                                  spacing=_GAP_SM,
                                  vertical_alignment=ft.CrossAxisAlignment.CENTER)
        self._valor = ft.Text(valor, size=32, weight=ft.FontWeight.W_700,
                              color=ft.Colors.ON_SURFACE, no_wrap=True,
                              overflow=ft.TextOverflow.ELLIPSIS)
        # Escalón de tamaño que manda el ALTO, y ancho útil de la tarjeta. Los
        # fija `_adaptar` al medirse; hasta entonces `_disponible = 0` hace que
        # `_encajar` respete el escalón sin recortar por un ancho que no conoce.
        self._tam_base = self._valor.size
        self._disponible = 0.0
        self._unidad = ft.Text(unidad, size=12,
                               color=ft.Colors.ON_SURFACE_VARIANT, no_wrap=True,
                               overflow=ft.TextOverflow.ELLIPSIS,
                               visible=bool(unidad))
        # Mismo patrón que en `TarjetaTotalActivos`: la cifra ENTERA es el
        # blanco, no un ícono suelto de 18px al que hay que apuntar.
        self._chevron = ft.Icon(ft.Icons.CHEVRON_RIGHT, size=18,
                                color=color_acento)
        self._disparador = _zona_pulsable([self._valor, self._chevron], accion,
                                          on_accion)
        self._cifra = ft.Column([self._disparador, self._unidad], spacing=2,
                                tight=True)
        self._vacio = _mensaje_vacio(vacio, ft.Icons.INVENTORY_2)
        self._esqueleto = Esqueleto()
        # Los tres contenidos del centro son excluyentes y su `visible` lo manda
        # SOLO `_pintar_centro`; estas dos banderas son su entrada.
        self._vacio_activo = False
        self._carga_activa = False
        self._pintar_centro()
        # El rótulo se queda FIJO arriba y solo se centra lo de abajo, igual que
        # en `TarjetaRanking`. Centrar el cuerpo entero —como estaba— movía
        # también el encabezado: al cambiar la cifra por el aviso de vacío, que
        # es más alto, el título bajaba y descuadraba con la tarjeta vecina.
        #
        # Aquí el `expand` vertical SÍ se quiere: esta columna toma el alto que
        # sobra bajo el encabezado y centra dentro lo que esté visible.
        self._centro = ft.Column(
            [self._cifra, self._vacio, self._esqueleto.control], expand=True,
            alignment=ft.MainAxisAlignment.CENTER)
        self._cuerpo.controls.extend([self._encabezado, self._centro])

    def set_valor(self, valor: str, unidad: str | None = None) -> None:
        """Actualiza la cifra y, si se pasa, su unidad.

        `unidad=None` la deja como estaba; una cadena vacía la oculta, que es lo
        que corresponde cuando todavía no hay dato que calificar.

        No refresca, por el mismo motivo que `TarjetaTotalActivos.set_totales`.
        """
        self._valor.value = valor
        if unidad is not None:
            self._unidad.value = unidad
            self._unidad.visible = bool(unidad)
        # El importe formateado que manda el servicio es mucho más largo que el
        # marcador con el que nace la tarjeta, así que el tamaño se recalcula
        # con la cifra NUEVA: si no, la primera respuesta llegaría recortada
        # hasta que algo redimensionara la ventana.
        self._ajustar_cifra()

    def set_cifra(self, texto: str) -> None:
        """Reescribe SOLO el texto de la cifra, conservando su tamaño.

        Es lo que usa el conteo animado. Con `set_valor` cada fotograma
        recalcularía el encaje, y como el importe gana dígitos según sube
        («$4.13» → «$41,383,453.26») la cifra iría encogiendo a saltos mientras
        cuenta. El tamaño lo fija una sola vez `set_valor` con el importe FINAL,
        que es el más largo, y aquí solo cambia el contenido.
        """
        self._valor.value = texto

    def mostrar_vacio(self, vacio: bool) -> None:
        """Cambia la cifra por el aviso de «sin datos», o al revés.

        Son excluyentes: mostrar un guion Y un aviso a la vez diría dos cosas
        distintas sobre lo mismo.
        """
        self._vacio_activo = vacio
        self._pintar_centro()

    def cargando(self, activo: bool) -> None:
        """Pone el esqueleto en lugar de la cifra mientras se consulta.

        NO arranca el brillo: el bucle es asíncrono y quien tenga página lo
        lanza (`page.run_task(tarjeta.animar_carga)`). Así la tarjeta se puede
        montar y probar sin bucle de eventos.
        """
        self._carga_activa = activo
        if activo:
            self._esqueleto.arrancar()
        else:
            self._esqueleto.detener()
        self._pintar_centro()

    async def animar_carga(self) -> None:
        """Pasea el brillo del esqueleto hasta que `cargando(False)` lo corte."""
        await self._esqueleto.animar()

    def _pintar_centro(self) -> None:
        """Único punto que decide QUÉ se ve bajo el encabezado.

        Con tres contenidos excluyentes, repartir el `visible` entre los
        métodos que cambian cada estado deja combinaciones imposibles a un
        descuido de distancia (la cifra y el aviso a la vez, o el esqueleto
        encima de un dato ya pintado).

        La carga gana al vacío: mientras se está consultando no se puede afirmar
        que no haya activos: todavía no se sabe.
        """
        self._esqueleto.control.visible = self._carga_activa
        self._vacio.visible = not self._carga_activa and self._vacio_activo
        self._cifra.visible = not self._carga_activa and not self._vacio_activo

    def _adaptar(self, ancho: float, alto: float) -> None:
        # El `gap` es la distancia entre el rótulo y la cifra, y se deja corto a
        # propósito: son una sola unidad de lectura, no dos bloques separados.
        if alto >= 160:
            pad, gap, lado, valor = _PAD, _GAP_SM, 28, 32
        elif alto >= 120:
            pad, gap, lado, valor = 18, _GAP_XS, 26, 28
        else:
            pad, gap, lado, valor = 12, _GAP_XS, 24, 22
        self._chrome(pad, gap)
        self._pastilla.width = self._pastilla.height = lado
        self._pastilla.content.size = round(lado * 0.56)
        self._pastilla.visible = ancho >= _ANCHO_CON_ICONO
        self._tam_base = valor
        self._disponible = ancho - 2 * pad
        self._ajustar_cifra()

    def _ajustar_cifra(self) -> None:
        """Aplica el tamaño de la cifra: el escalón por alto, recortado por ancho.

        Se llama desde `_adaptar` y desde `set_valor`, porque las dos cosas lo
        cambian: la caja puede encoger, pero también puede llegar un importe más
        largo a la misma caja.
        """
        self._valor.size = self._encajar(self._tam_base, self._disponible)
        # El chevron escala CON la cifra, igual que en la tarjeta de totales:
        # fijo, en la tarjeta baja acabaría siendo más alto que el número.
        self._chevron.size = round(self._valor.size * 0.55)
        # El esqueleto ocupa el hueco de la zona pulsable —cifra más chevron—,
        # así que se mide contra ella y no contra la tarjeta: al aparecer no
        # mueve nada de sitio. `_disponible` es 0 hasta la primera medición, y
        # ahí manda el piso de `medir`.
        self._esqueleto.medir(self._valor.size * _ALTO_LINEA,
                              self._disponible * _ESQUELETO_FRACCION)

    def _encajar(self, tam: int, disponible: float) -> int:
        """Encoge la cifra hasta que quepa a lo ANCHO.

        Los escalones de `_adaptar` miran solo el alto, que es lo que decide la
        densidad de las demás tarjetas. Aquí no basta: el importe formateado
        («$41,383,453.26») mide el triple que un conteo, y esta tarjeta ocupa un
        cuarto del ancho del tablero. Con la ventana en su mínimo de 960px la
        cifra se recortaba con puntos suspensivos, que en un número es peor que
        no mostrarlo: «$41,383,4…» se lee como una cantidad distinta.

        El ancho se ESTIMA a partir del tamaño de fuente porque Flet no mide
        texto: `_RATIO_CARACTER` va holgado a propósito —los separadores son más
        angostos que los dígitos—, así que el error cae del lado de encoger de
        más y nunca del de recortar.
        """
        texto = self._valor.value or ""
        if not texto or disponible <= 0:
            return tam
        # El chevron y su holgura comparten la línea con la cifra, y escala con
        # ella: entra en la ecuación como una fracción del tamaño, no como px.
        con_chevron = self._disparador is not self._valor
        fijo = (_GAP_XS + _GAP_SM) if con_chevron else 0
        por_punto = len(texto) * _RATIO_CARACTER + (0.55 if con_chevron else 0)
        cabe = (disponible - fijo) / por_punto
        return max(_TAM_VALOR_MIN, min(tam, int(cabe)))


class TarjetaRanking(Tarjeta):
    """Listado ordenado con posición, nombre y cantidad ("Top Empresas").

    El primer puesto se distingue con el color primario; el resto queda en tono
    neutro para que el orden se lea de un vistazo.
    """

    def __init__(self, rotulo: str, icono, *, filas: list[tuple] | None = None,
                 vacio: str = "",
                 color_icono: str = ft.Colors.ON_TERTIARY_FIXED_VARIANT,
                 fondo_icono: str = ft.Colors.TERTIARY_FIXED,
                 color_acento: str = ft.Colors.TERTIARY) -> None:
        super().__init__(acento=True, color_acento=color_acento)
        self._vacio = vacio

        # Misma idea que en `TarjetaValor`: pastilla más chica y el rótulo pegado
        # a la lista, para que el podio —que es el contenido— gane el sitio.
        self._pastilla = _pastilla(icono, color_icono, fondo_icono, lado=28,
                                   tam=16)
        self._encabezado = ft.Row([self._pastilla, _rotulo(rotulo)],
                                  spacing=_GAP_SM,
                                  vertical_alignment=ft.CrossAxisAlignment.CENTER)
        # `alignment=CENTER` y no `tight`: las tres posiciones se reparten en el
        # alto sobrante en vez de apelmazarse justo debajo del rótulo.
        self._lista = ft.Column(spacing=_GAP_SM, expand=True,
                                alignment=ft.MainAxisAlignment.CENTER)
        # Una barra por puesto del podio: el esqueleto anticipa la FORMA de lo
        # que viene, no solo que algo viene.
        self._esqueleto = Esqueleto(
            _PUESTOS_PODIO, espacio=_GAP_SM, ancho_completo=True, expandir=True,
            alineacion=ft.MainAxisAlignment.CENTER)
        self._cuerpo.controls.extend(
            [self._encabezado, self._lista, self._esqueleto.control])
        self.set_filas(filas or [])

    def cargando(self, activo: bool) -> None:
        """Pone el esqueleto en lugar del podio mientras se consulta.

        NO arranca el brillo: el bucle es asíncrono y lo lanza quien tenga
        página (`page.run_task(tarjeta.animar_carga)`).
        """
        if activo:
            self._esqueleto.arrancar()
        else:
            self._esqueleto.detener()
        self._esqueleto.control.visible = activo
        self._lista.visible = not activo

    async def animar_carga(self) -> None:
        """Pasea el brillo hasta que `cargando(False)` lo corte."""
        await self._esqueleto.animar()

    def set_filas(self, filas: list[tuple]) -> None:
        """`filas` es [(nombre, cantidad), ...] YA ordenado de mayor a menor.

        Sin filas pone el aviso de vacío en su lugar, para que la tarjeta no
        quede en blanco. Las deja PREPARADAS para entrar (invisibles y
        desplazadas); quien las llene debe llamar después a `animar_entrada` o a
        `mostrar_entrada` —también con el aviso, que entra igual—.
        """
        if filas:
            self._lista.controls = [
                self._fila(i + 1, nombre, cantidad)
                for i, (nombre, cantidad) in enumerate(filas)]
        else:
            self._lista.controls = [_mensaje_vacio(self._vacio,
                                                   ft.Icons.INVENTORY_2)]
        for fila in self._lista.controls:
            preparar_entrada(fila)

    async def animar_entrada(self) -> None:
        """Enciende el podio de arriba abajo."""
        await entrar_escalonado(self._lista.controls, self.refrescar)

    def mostrar_entrada(self) -> None:
        """Deja el podio visible sin animarlo."""
        mostrar_entrada(self._lista.controls)

    @staticmethod
    def _fila(puesto: int, nombre: str, cantidad: str) -> ft.Control:
        lider = puesto == 1
        medalla = ft.Container(
            ft.Text(str(puesto), size=10, weight=ft.FontWeight.BOLD,
                    color=ft.Colors.ON_PRIMARY if lider else ft.Colors.ON_SURFACE),
            width=20, height=20, border_radius=10,
            bgcolor=(ft.Colors.PRIMARY if lider
                     else ft.Colors.SURFACE_CONTAINER_HIGHEST),
            alignment=ft.Alignment(0, 0))
        return ft.Row(
            [medalla,
             ft.Text(nombre, size=12, color=ft.Colors.ON_SURFACE, expand=True,
                     no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS,
                     tooltip=nombre),
             ft.Text(cantidad, size=12, weight=ft.FontWeight.BOLD,
                     color=ft.Colors.PRIMARY if lider
                     else ft.Colors.ON_SURFACE_VARIANT)],
            spacing=_GAP_SM, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    def _adaptar(self, ancho: float, alto: float) -> None:
        if alto >= 160:
            pad, gap, sep, lado = _PAD, _GAP_SM, _GAP_SM, 28
        elif alto >= 120:
            pad, gap, sep, lado = 18, _GAP_XS, 6, 26
        else:
            pad, gap, sep, lado = 12, _GAP_XS, _GAP_XS, 24
        self._chrome(pad, gap)
        self._lista.spacing = sep
        # El esqueleto sigue a la lista para que al cambiar uno por otro nada se
        # mueva de sitio.
        self._esqueleto.set_espacio(sep)
        self._esqueleto.medir(_ALTO_BARRA_PODIO)
        self._pastilla.width = self._pastilla.height = lado
        self._pastilla.content.size = round(lado * 0.56)
        self._pastilla.visible = ancho >= _ANCHO_CON_ICONO


class TarjetaActividad(Tarjeta):
    """Bitácora reciente: título, lista de eventos y acción al pie.

    La lista lleva scroll propio en vez de recortarse: es la única tarjeta cuyo
    número de elementos no se conoce de antemano, así que acotar por alto
    escondería eventos sin avisar.
    """

    def __init__(self, titulo: str, *, eventos: list[tuple] | None = None,
                 accion: str = "", on_accion=None, vacio: str = "",
                 color_acento: str = ft.Colors.OUTLINE) -> None:
        """`accion` es el TOOLTIP del botón del encabezado, no un rótulo visible."""
        super().__init__(acento=True, color_acento=color_acento)
        self._vacio = vacio

        self._titulo = ft.Text(titulo, theme_style=ft.TextThemeStyle.HEADLINE_SMALL,
                               color=ft.Colors.ON_SURFACE, expand=True,
                               no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS)
        # El chevron es un `Icon` pelado, no un `IconButton`: quien recibe el clic
        # y el hover es el encabezado ENTERO, así que un botón dentro sería un
        # segundo blanco con su propio realce compitiendo con el de fuera.
        self._chevron = ft.Icon(ft.Icons.CHEVRON_RIGHT, size=20,
                                color=ft.Colors.ON_SURFACE_VARIANT,
                                visible=bool(accion))
        # Título y chevron en UNA zona pulsable que abarca todo el ancho: el
        # título dice a dónde se va y el chevron que se puede ir, y separarlos
        # obligaba a apuntar a un ícono de 20px teniendo al lado una línea entera
        # que no hacía nada.
        self._encabezado = ft.Container(
            ft.Row([self._titulo, self._chevron], spacing=_GAP_SM,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.Padding.symmetric(horizontal=_GAP_SM, vertical=_GAP_XS),
            # El margen negativo compensa ese relleno: la zona sensible crece
            # hacia afuera, pero el título sigue alineado con la lista de abajo.
            margin=ft.Margin.symmetric(horizontal=-_GAP_SM, vertical=-_GAP_XS),
            border_radius=_RADIO_INTERNO,
            # Ver la nota de `_evento`: sin color de fondo la caja puede quedar
            # fuera del hit-test y el realce solo respondería sobre el texto.
            bgcolor=ft.Colors.TRANSPARENT,
            tooltip=accion or None,
            on_click=on_accion if accion else None,
            ink=bool(accion),
            # Sin `animate`, igual que las filas de la lista: dentro de esta
            # tarjeta el realce entra y sale seco. Mezclar un encabezado que se
            # funde con unas filas que no lo hacen se nota al bajar el puntero de
            # uno a las otras.
            on_hover=self._hover_encabezado if accion else None)
        # El cursor de mano solo si de verdad se pulsa: una mano sobre un
        # encabezado inerte promete un clic que no existe.
        if accion:
            puntero_mano(self._encabezado)
        self._lista = ft.Column(spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)
        # Cuatro barras: los movimientos que la tarjeta alcanza a enseñar. Se
        # alinean ARRIBA como la bitácora, que es una lista, no un bloque
        # centrado.
        self._esqueleto = Esqueleto(
            _EVENTOS_BITACORA, espacio=_GAP_SM, ancho_completo=True,
            expandir=True, alineacion=ft.MainAxisAlignment.START)
        self._cuerpo.controls.extend(
            [self._encabezado, self._lista, self._esqueleto.control])
        self.set_eventos(eventos or [])

    def cargando(self, activo: bool) -> None:
        """Pone el esqueleto en lugar de la bitácora mientras se consulta."""
        if activo:
            self._esqueleto.arrancar()
        else:
            self._esqueleto.detener()
        self._esqueleto.control.visible = activo
        self._lista.visible = not activo

    async def animar_carga(self) -> None:
        """Pasea el brillo hasta que `cargando(False)` lo corte."""
        await self._esqueleto.animar()

    def _hover_encabezado(self, e) -> None:
        """Realza la cabecera entera y tiñe el chevron, para que se lea como un
        único destino y no como un texto junto a un ícono."""
        encima = puntero_encima(e)
        self._encabezado.bgcolor = (_FONDO_FILA_HOVER if encima
                                    else ft.Colors.TRANSPARENT)
        self._chevron.color = (ft.Colors.PRIMARY if encima
                               else ft.Colors.ON_SURFACE_VARIANT)
        self._actualizar(self._encabezado)

    def set_eventos(self, eventos: list[tuple]) -> None:
        """`eventos` es [(icono, titulo, cuando), ...], del más reciente al último.

        Sin eventos pone el aviso de vacío, CENTRADO: con la lista alineada al
        inicio —que es lo correcto para una bitácora— el aviso solo quedaría
        pegado arriba y con todo el hueco debajo.

        Igual que en el podio, quedan preparados para entrar: hace falta llamar
        después a `animar_entrada` o a `mostrar_entrada`.
        """
        if eventos:
            self._lista.controls = [self._evento(*e) for e in eventos]
            self._lista.alignment = ft.MainAxisAlignment.START
        else:
            # Más grande que en las otras: esta tarjeta ocupa media pantalla y
            # con el tamaño de las chicas el aviso se perdía en el hueco.
            self._lista.controls = [
                _mensaje_vacio(self._vacio, ft.Icons.HISTORY_TOGGLE_OFF,
                               tam_icono=44, tam_texto=15)]
            self._lista.alignment = ft.MainAxisAlignment.CENTER
        for fila in self._lista.controls:
            preparar_entrada(fila)

    async def animar_entrada(self) -> None:
        """Enciende la bitácora del evento más reciente al más viejo."""
        await entrar_escalonado(self._lista.controls, self.refrescar)

    def mostrar_entrada(self) -> None:
        """Deja la bitácora visible sin animarla."""
        mostrar_entrada(self._lista.controls)

    @staticmethod
    def _evento(icono, titulo: str, cuando: str) -> ft.Control:
        """Una fila de la bitácora.

        Sin `animate` a propósito: el realce entra y sale SECO. En una lista, el
        fundido arrastra el color de la fila que se acaba de dejar mientras el
        puntero ya está sobre la siguiente, y al recorrerla se ven dos encendidas
        a la vez.
        """
        return ft.Container(
            ft.Row(
                [ft.Container(ft.Icon(icono, size=16,
                                      color=ft.Colors.ON_SURFACE_VARIANT),
                              width=32, height=32, border_radius=16,
                              bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
                              alignment=ft.Alignment(0, 0)),
                 ft.Column(
                     [ft.Text(titulo, theme_style=ft.TextThemeStyle.LABEL_LARGE,
                              color=ft.Colors.ON_SURFACE, no_wrap=True,
                              overflow=ft.TextOverflow.ELLIPSIS, tooltip=titulo),
                      ft.Text(cuando, theme_style=ft.TextThemeStyle.LABEL_MEDIUM,
                              color=ft.Colors.ON_SURFACE_VARIANT, no_wrap=True)],
                     spacing=0, tight=True, expand=True)],
                spacing=_GAP_SM, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            # El relleno horizontal existe para el realce: sin él el fondo del
            # hover arrancaría pegado al ícono y se vería recortado. El margen lo
            # devuelve, para que el contenido no se corra respecto al título.
            padding=ft.Padding.symmetric(horizontal=_GAP_SM,
                                         vertical=_PAD_EVENTO),
            margin=ft.Margin.symmetric(horizontal=-_GAP_SM),
            border_radius=_RADIO_INTERNO,
            border=ft.Border(bottom=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)),
            # TRANSPARENT y no None: un Container sin color de fondo puede no
            # participar del hit-test en toda su caja, y el hover se sentiría solo
            # encima del texto en vez de en la fila entera.
            bgcolor=ft.Colors.TRANSPARENT,
            # El manejador va en el CONSTRUCTOR. Asignarlo después
            # (`fila.on_hover = ...`) no lo registra, y ese era el motivo de que
            # las filas no reaccionaran.
            on_hover=TarjetaActividad._hover_evento)

    @staticmethod
    def _hover_evento(e) -> None:
        """Realce de una fila de la bitácora.

        La fila se saca de `e.control`, no de una clausura: así el manejador es
        uno solo compartido por todas y no hay una función por evento montado.

        Solo cambia el fondo: la fila no es pulsable, y marcarla más fuerte
        prometería un clic que no lleva a ningún lado.
        """
        e.control.bgcolor = (_FONDO_FILA_HOVER if puntero_encima(e)
                             else ft.Colors.TRANSPARENT)
        Tarjeta._actualizar(e.control)

    def _adaptar(self, ancho: float, alto: float) -> None:
        # Todo el alto se reparte a favor de la LISTA: es lo único que crece con
        # los datos, y la tarjeta se mide por cuántos movimientos deja ver de un
        # vistazo. Por eso el relleno vertical y la separación bajo el título se
        # recortan antes que ella.
        #
        # El relleno HORIZONTAL se queda en `_PAD`: recortarlo también sacaría
        # esta tarjeta de la línea que comparten las demás del tablero.
        if alto >= 220:
            pad_v, gap, estilo, chevron = (_GAP, _GAP_SM,
                                           ft.TextThemeStyle.HEADLINE_SMALL, 20)
        elif alto >= 150:
            pad_v, gap, estilo, chevron = (12, _GAP_XS,
                                           ft.TextThemeStyle.TITLE_MEDIUM, 18)
        else:
            pad_v, gap, estilo, chevron = (_GAP_SM, _GAP_XS,
                                           ft.TextThemeStyle.TITLE_SMALL, 16)
        self._chrome(ft.Padding.symmetric(horizontal=_PAD, vertical=pad_v), gap)
        self._titulo.theme_style = estilo
        self._chevron.size = chevron
        self._esqueleto.medir(_ALTO_BARRA_EVENTO)


class TarjetaVacia(Tarjeta):
    """Hueco punteado para una tarjeta por definir (el placeholder del diseño)."""

    def __init__(self, on_click=None) -> None:
        super().__init__(padding=_GAP_SM)
        self.control.bgcolor = None
        self.control.shadow = None
        self.control.border = ft.Border.all(1, ft.Colors.OUTLINE_VARIANT)
        self.control.on_click = on_click
        self._cuerpo.controls.append(
            ft.Container(
                content=ft.Icon(ft.Icons.ADD_CIRCLE_OUTLINE, size=32,
                                color=ft.Colors.OUTLINE_VARIANT),
                alignment=ft.Alignment(0, 0), expand=True))
