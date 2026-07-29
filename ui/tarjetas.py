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
paleta de ui/tema.py se aplique sola en claro y en oscuro.
"""

from __future__ import annotations

import flet as ft

# Espaciado y forma del sistema de diseño.
_RADIO = 12          # rounded.md — "elementos grandes"
_PAD = 24            # spacing.lg
_GAP = 16            # spacing.md
_GAP_SM = 8          # spacing.sm
_ACENTO = 4          # ancho de la banda vertical de la tarjeta destacada

# Elevación: nivel 1 en reposo, nivel 2 al pasar el cursor.
_SOMBRA_N1 = ft.BoxShadow(
    blur_radius=4, offset=ft.Offset(0, 2),
    color=ft.Colors.with_opacity(0.05, ft.Colors.BLACK))
_SOMBRA_N2 = ft.BoxShadow(
    blur_radius=8, offset=ft.Offset(0, 4),
    color=ft.Colors.with_opacity(0.08, ft.Colors.BLACK))

# Umbrales de densidad por ALTO de la caja. No basta con ocultar elementos: el
# "aire" (padding y separación) también tiene que encoger, porque en cajas bajas
# se lo come todo. Con padding fijo de 24, una tarjeta de 72px de alto solo deja
# 24px útiles y el contenido desborda.
_ALTO_COMPLETO = 240   # cabe todo, incluida la acción del pie
_ALTO_MEDIO = 150      # se pierde la acción
_ALTO_MINIMO = 110     # se pierde la nota de variación
# Por debajo de este ancho, el ícono en pastilla estorba más de lo que aporta.
_ANCHO_CON_ICONO = 200

# Métricas para calcular cuánto alto sobra para el número. Fijar su tamaño por
# nivel siempre desborda en la frontera del nivel, así que se DERIVA del espacio
# libre: queda tan grande como quepa y nunca se sale.
_LINEA_ETIQUETA = 20   # LABEL_LARGE del tema
_LINEA_NOTA = 20       # BODY_MEDIUM del tema
_FACTOR_LINEA = 1.25   # alto de línea de un Text con `size` explícito
_VALOR_MIN, _VALOR_MAX = 20, 56
_MARGEN = 4            # colchón para el redondeo del motor de texto

# No repintar por cambios menores a esto (evita trabajo durante el arrastre),
# mismo criterio que _UMBRAL_REMEDIR en ui/tabla_responsiva.py.
_UMBRAL = 2.0


class Tarjeta:
    """Superficie base: fondo, borde, radio, elevación y hover.

    No fija tamaño. El contenido lo arma la subclase en `self._cuerpo`.
    `acento=True` agrega la banda vertical izquierda de 4px que el diseño usa
    para distinguir la tarjeta principal del tablero.
    """

    def __init__(self, *, acento: bool = False, padding: int = _PAD) -> None:
        self._cuerpo = ft.Column(spacing=_GAP, expand=True)
        self._ancho = 0.0
        self._alto = 0.0

        # Se guarda la referencia: el padding es una de las cosas que encoge al
        # adaptar la densidad (ver `_chrome`).
        interior = self._interior = ft.Container(
            content=self._cuerpo, padding=padding, expand=True)
        if acento:
            # La banda va DENTRO y recortada por el radio, no como un borde
            # izquierdo grueso: Flutter ignora `border_radius` cuando el borde no
            # es uniforme, y la tarjeta saldría con las esquinas rectas.
            banda = ft.Container(width=_ACENTO, bgcolor=ft.Colors.PRIMARY_CONTAINER)
            contenido: ft.Control = ft.Row(
                [banda, interior], spacing=0,
                vertical_alignment=ft.CrossAxisAlignment.STRETCH)
        else:
            contenido = interior

        self.control = ft.Container(
            content=contenido,
            bgcolor=ft.Colors.SURFACE_CONTAINER_LOWEST,
            # Borde UNIFORME: es la condición para que el radio se aplique.
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=_RADIO,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            shadow=_SOMBRA_N1,
            on_hover=self._hover,
            on_size_change=self._al_medir,
            size_change_interval=50,
            animate=ft.Animation(160, ft.AnimationCurve.EASE_OUT),
        )

    def _hover(self, e) -> None:
        self.control.shadow = _SOMBRA_N2 if e.data == "true" else _SOMBRA_N1
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

    def _chrome(self, padding: int, gap: int) -> None:
        """Ajusta el 'aire' de la tarjeta: margen interno y separación vertical."""
        self._interior.padding = padding
        self._cuerpo.spacing = gap

    @staticmethod
    def _actualizar(control: ft.Control) -> None:
        """`update()` tolerante: la tarjeta puede no estar montada todavía."""
        try:
            control.update()
        except (RuntimeError, AssertionError, AttributeError):
            pass


class TarjetaMetrica(Tarjeta):
    """Métrica destacada: etiqueta, valor grande, variación y acción al pie.

    Es la tarjeta "Activos Registrados" del diseño. En cajas bajas suelta primero
    la acción del pie y luego la variación, y encoge el valor, de modo que hasta
    en 1x1 sigue siendo legible.
    """

    def __init__(self, etiqueta: str, valor: str, icono, *,
                 variacion: str | None = None, nota: str | None = None,
                 accion: str | None = None, on_accion=None) -> None:
        super().__init__(acento=True)
        # Qué partes existen se decide UNA vez, al construir. `_adaptar` solo
        # decide si caben: si consultara `visible`, leería su propio efecto.
        self._tiene_variacion = bool(variacion)
        self._tiene_nota = bool(variacion or nota)
        self._tiene_accion = bool(accion)

        self._etiqueta = ft.Text(
            etiqueta.upper(), theme_style=ft.TextThemeStyle.LABEL_LARGE,
            color=ft.Colors.PRIMARY, no_wrap=True,
            overflow=ft.TextOverflow.ELLIPSIS)
        self._ico = ft.Icon(icono, size=20, color=ft.Colors.ON_SECONDARY_CONTAINER)
        self._insignia = ft.Container(
            content=self._ico, bgcolor=ft.Colors.SECONDARY_FIXED,
            padding=8, border_radius=8)
        encabezado = ft.Row(
            [self._etiqueta, self._insignia],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.START)

        self._valor = ft.Text(valor, size=48, weight=ft.FontWeight.W_900,
                              color=ft.Colors.PRIMARY, no_wrap=True,
                              overflow=ft.TextOverflow.ELLIPSIS)
        self._tendencia = ft.Icon(ft.Icons.TRENDING_UP, size=20,
                                  color=ft.Colors.ON_SECONDARY_CONTAINER,
                                  visible=variacion is not None)
        fila_valor = ft.Row([self._valor, self._tendencia], spacing=4, tight=True,
                            vertical_alignment=ft.CrossAxisAlignment.END)

        # La variación va en un span aparte para resaltarla sin partir el texto.
        self._nota = ft.Text(
            spans=[
                ft.TextSpan(variacion or "",
                            ft.TextStyle(color=ft.Colors.SECONDARY,
                                         weight=ft.FontWeight.BOLD)),
                ft.TextSpan(f" {nota}" if nota else ""),
            ],
            theme_style=ft.TextThemeStyle.BODY_MEDIUM,
            color=ft.Colors.ON_SURFACE_VARIANT, expand=True,
            no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS,
            visible=bool(variacion or nota))

        # La acción es solo ícono (el texto viaja en el tooltip) y comparte línea
        # con la nota. Así NO cuesta alto propio: la fila la marca el botón, que
        # es más alto que el texto, y ese ahorro es lo que permite agrandar el
        # valor sin que la tarjeta desborde.
        self._accion = ft.IconButton(
            icon=ft.Icons.ARROW_FORWARD, icon_size=18, width=32, height=32,
            tooltip=accion or None, on_click=on_accion, visible=bool(accion))
        self._pie = ft.Row([self._nota, self._accion], spacing=_GAP_SM,
                           vertical_alignment=ft.CrossAxisAlignment.CENTER)

        # El espaciador reparte el sobrante y empuja el pie abajo.
        self._espaciador = ft.Container(expand=True)
        self._cuerpo.controls.extend(
            [encabezado, fila_valor, self._espaciador, self._pie])

    def _adaptar(self, ancho: float, alto: float) -> None:
        completo = alto >= _ALTO_COMPLETO
        medio = alto >= _ALTO_MEDIO
        minimo = alto >= _ALTO_MINIMO

        # Cromo por nivel, más un TOPE para el valor. El tope existe porque al
        # cruzar a un nivel más bajo se libera espacio (menos padding, se oculta
        # el pie) y el número crecería al encoger la tarjeta, que se ve como un
        # error. Los topes garantizan que nunca aumente al reducir la caja.
        # (padding, gap, ícono, padding pastilla, lado del botón, tope del valor)
        if completo:
            pad, gap, ico, pad_ico, boton, tope = _PAD, _GAP, 20, 8, 36, _VALOR_MAX
        elif medio:
            pad, gap, ico, pad_ico, boton, tope = 18, 8, 16, 5, 32, 44
        elif minimo:
            pad, gap, ico, pad_ico, boton, tope = 12, 4, 14, 4, 26, 26
        else:
            pad, gap, ico, pad_ico, boton, tope = 10, 2, 12, 3, 24, _VALOR_MIN
        self._chrome(pad, gap)

        # El pie sobrevive hasta el nivel mínimo: al ir en una sola línea, ya no
        # compite por alto con el valor.
        self._accion.visible = minimo and self._tiene_accion
        self._nota.visible = minimo and self._tiene_nota
        self._pie.visible = minimo and (self._tiene_accion or self._tiene_nota)
        self._tendencia.visible = medio and self._tiene_variacion
        self._insignia.visible = ancho >= _ANCHO_CON_ICONO
        self._ico.size = ico
        self._insignia.padding = pad_ico
        self._accion.width = self._accion.height = boton
        self._accion.icon_size = round(boton * 0.56)

        # El número se queda con TODO el alto que sobre, acotado por los topes.
        alto_encabezado = (max(_LINEA_ETIQUETA, ico + 2 * pad_ico)
                           if self._insignia.visible else _LINEA_ETIQUETA)
        alto_pie = max(_LINEA_NOTA, boton) if self._pie.visible else 0
        piezas = 3 if self._pie.visible else 2
        libre = (alto - 2 * pad - alto_encabezado - alto_pie
                 - gap * (piezas - 1) - _MARGEN)
        self._valor.size = max(_VALOR_MIN,
                               min(tope, int(libre / _FACTOR_LINEA)))


class TarjetaKpi(Tarjeta):
    """KPI compacto: ícono en pastilla + etiqueta + valor.

    Es la tarjeta "Valor Total Estimado" del diseño. El par de colores del ícono
    se pasa por ROL (fondo y frente) para poder distinguir KPIs entre sí sin
    salirse de la paleta.
    """

    def __init__(self, etiqueta: str, valor: str, icono, *,
                 color_icono: str = ft.Colors.ON_TERTIARY_FIXED_VARIANT,
                 fondo_icono: str = ft.Colors.TERTIARY_FIXED) -> None:
        super().__init__(padding=_PAD)

        self._ico = ft.Icon(icono, size=22, color=color_icono)
        self._insignia = ft.Container(
            content=self._ico, bgcolor=fondo_icono, width=48, height=48,
            border_radius=8, alignment=ft.Alignment(0, 0))
        self._etiqueta = ft.Text(
            etiqueta.upper(), theme_style=ft.TextThemeStyle.LABEL_MEDIUM,
            color=ft.Colors.ON_SURFACE_VARIANT, no_wrap=True,
            overflow=ft.TextOverflow.ELLIPSIS)
        self._valor = ft.Text(
            valor, theme_style=ft.TextThemeStyle.HEADLINE_SMALL,
            color=ft.Colors.ON_SURFACE, no_wrap=True,
            overflow=ft.TextOverflow.ELLIPSIS)

        # `expand` en el texto: si el KPI se angosta, la elipsis actúa sobre la
        # etiqueta en vez de empujar al ícono fuera de la tarjeta.
        self._textos = ft.Column([self._etiqueta, self._valor], spacing=2,
                                 tight=True, expand=True)
        self._fila = ft.Row([self._insignia, self._textos], spacing=_GAP,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            expand=True)
        self._cuerpo.controls.append(self._fila)

    def _adaptar(self, ancho: float, alto: float) -> None:
        # La pastilla del ícono es lo que primero deja de caber: con 48px de lado
        # más 24 de padding arriba y abajo ya necesita 96px de alto.
        if alto >= 120:
            pad, lado, ico = _PAD, 48, 22
            estilo = ft.TextThemeStyle.HEADLINE_SMALL
        elif alto >= 88:
            pad, lado, ico = _GAP, 40, 20
            estilo = ft.TextThemeStyle.TITLE_MEDIUM
        else:
            pad, lado, ico = 12, 32, 18
            estilo = ft.TextThemeStyle.TITLE_SMALL
        self._chrome(pad, gap=0)   # una sola fila: el gap vertical no aplica

        self._insignia.width = self._insignia.height = lado
        self._ico.size = ico
        self._insignia.visible = ancho >= _ANCHO_CON_ICONO
        self._fila.spacing = _GAP if lado >= 48 else 12
        self._valor.theme_style = estilo


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
