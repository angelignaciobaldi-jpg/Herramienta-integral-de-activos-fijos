"""Rejilla de bloques tipo "bento" para el tablero (ver DISENO.md).

Cada bloque declara en qué columna/fila empieza y cuántas columnas/filas abarca;
la rejilla calcula la geometría en píxeles y coloca los bloques con
posicionamiento absoluto dentro de un `ft.Stack`. Es lo que permite que las
tarjetas midan lo que midan sin fijar tamaño: la rejilla les entrega la caja y
ellas se adaptan.

Por qué no `ft.GridView` ni `ft.ResponsiveRow`: el primero solo hace celdas
UNIFORMES (`runs_count`/`child_aspect_ratio`), y el segundo da span horizontal
pero no vertical (sus filas son implícitas por wrap). Ninguno permite decir
"de la columna 2 a la 3 y de la fila 4 a la 5".

Notas de implementación:

- El `Stack` NO puede llevar `expand=True` dentro del `Column` con scroll (una
  altura sin acotar dentro de un scroll view revienta): se le fija `height`.
- El scroll horizontal no puede aparecer: el ancho de celda se deriva del ancho
  medido, así que la retícula encaja exacta a lo ancho por construcción.
- La NO superposición no la garantiza Flet (un `Stack` deja encimar sin
  quejarse); se valida aquí al insertar.
"""

from __future__ import annotations

from dataclasses import dataclass

import flet as ft

# Ancho que se lleva la barra de scroll al aparecer (igual que en
# ui/tabla_responsiva.py, para que las dos pantallas midan con el mismo criterio).
_GUTTER_SCROLL = 14
_UMBRAL_REMEDIR = 2.0


class ColisionRejilla(ValueError):
    """Dos bloques se pelean por la misma celda."""


@dataclass
class Bloque:
    """Un control y el rectángulo de celdas que ocupa (todo 0-based)."""

    control: ft.Control
    col: int
    fila: int
    ancho: int = 1
    alto: int = 1

    def celdas(self):
        for f in range(self.fila, self.fila + self.alto):
            for c in range(self.col, self.col + self.ancho):
                yield (f, c)


class Rejilla:
    """Retícula de `columnas` x N filas; crece hacia abajo con scroll vertical.

    `alto_fila` es fijo a propósito: si las filas repartieran el alto disponible,
    las tarjetas cambiarían de proporción según el monitor y los gráficos se
    deformarían entre una laptop y una pantalla grande.
    """

    def __init__(self, columnas: int = 12, alto_fila: int = 72,
                 espacio: int = 16) -> None:
        self.columnas = columnas
        self.alto_fila = alto_fila
        self.espacio = espacio
        self._bloques: list[Bloque] = []
        self._cajas: list[ft.Container] = []

        self._stack = ft.Stack(clip_behavior=ft.ClipBehavior.NONE)
        self._scroller = ft.Column(
            [self._stack], scroll=ft.ScrollMode.AUTO, expand=True,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH, spacing=0)
        self.contenido = ft.Container(
            content=self._scroller, expand=True,
            on_size_change=self._al_redimensionar, size_change_interval=50)
        self._ancho = 0.0
        self._alto = 0.0

    # ---------------------------------------------------------------- API
    @property
    def filas(self) -> int:
        """Filas realmente ocupadas (la retícula crece con el contenido)."""
        return max((b.fila + b.alto for b in self._bloques), default=0)

    def agregar(self, bloque: Bloque) -> None:
        self._validar(bloque)
        self._bloques.append(bloque)
        caja = ft.Container(content=bloque.control, left=0, top=0, width=0, height=0)
        self._cajas.append(caja)
        self._stack.controls.append(caja)
        self._reposicionar()

    def agregar_todos(self, bloques: list[Bloque]) -> None:
        for b in bloques:
            self.agregar(b)

    # --------------------------------------------------------- validación
    def _validar(self, nuevo: Bloque) -> None:
        if nuevo.col < 0 or nuevo.fila < 0 or nuevo.ancho < 1 or nuevo.alto < 1:
            raise ColisionRejilla(f"Coordenadas inválidas: {nuevo}")
        if nuevo.col + nuevo.ancho > self.columnas:
            raise ColisionRejilla(
                f"El bloque se sale por la derecha (col {nuevo.col}+{nuevo.ancho} "
                f"> {self.columnas} columnas)")
        ocupadas = {celda for b in self._bloques for celda in b.celdas()}
        choque = ocupadas.intersection(nuevo.celdas())
        if choque:
            raise ColisionRejilla(f"Celdas ya ocupadas: {sorted(choque)}")

    # ---------------------------------------------------------- geometría
    def alto_contenido(self) -> float:
        n = self.filas
        return n * self.alto_fila + self.espacio * (n - 1) if n else 0.0

    def _al_redimensionar(self, e: ft.LayoutSizeChangeEvent) -> None:
        if (abs(e.width - self._ancho) < _UMBRAL_REMEDIR
                and abs(e.height - self._alto) < _UMBRAL_REMEDIR):
            return
        self._ancho, self._alto = e.width, e.height
        self._reposicionar()

    def _reposicionar(self) -> None:
        if not self._ancho:
            return  # todavía no nos han medido

        gap = self.espacio
        alto_total = self.alto_contenido()
        # Si va a haber scroll vertical, la barra se come ancho útil; sin
        # descontarla la última columna queda por debajo de la barra.
        hay_scroll = alto_total > self._alto
        ancho_util = self._ancho - (_GUTTER_SCROLL if hay_scroll else 0)
        ancho_celda = (ancho_util - gap * (self.columnas - 1)) / self.columnas

        for bloque, caja in zip(self._bloques, self._cajas):
            caja.left = bloque.col * (ancho_celda + gap)
            caja.top = bloque.fila * (self.alto_fila + gap)
            caja.width = bloque.ancho * ancho_celda + (bloque.ancho - 1) * gap
            caja.height = bloque.alto * self.alto_fila + (bloque.alto - 1) * gap

        self._stack.height = alto_total
        self._stack.width = ancho_util
        for c in (self._stack, self._scroller):
            try:
                c.update()
            except (RuntimeError, AssertionError, AttributeError):
                pass
