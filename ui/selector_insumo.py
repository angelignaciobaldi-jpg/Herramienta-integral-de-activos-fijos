"""Selector de insumo del catálogo del SIPP (búsqueda en la caché local).

Como los nombres del levantamiento no coinciden con el catálogo del SIPP, el
usuario busca aquí el insumo REAL (por descripción o Cve) y lo elige; se devuelve
su id (Cve Insumo) y su nombre, que es lo que el RPA usará para seleccionarlo por
ID exacto en el modal "Buscar Insumo" del portal.

Se apoya en core/db.buscar_insumos (caché descargada con core/insumos).
"""

from __future__ import annotations

import flet as ft

from core import db
from ui.comun import GRIS, NARANJA, VERDE

_ANCHO = 620
_ALTO_FILA = 52    # alto aproximado de cada fila de resultado (px)
_ALTO_MAX = 420    # tope de la lista; a partir de aquí hace scroll


class DialogoSelectorInsumo:
    """Diálogo de búsqueda/selección de un insumo del catálogo cacheado."""

    def __init__(self, app, al_elegir):
        """`al_elegir(id_insumo, nombre)` se llama cuando el usuario elige uno."""
        self.app = app
        self.page = app.page
        self.al_elegir = al_elegir
        self._sugerido = ""
        self._construir()

    def _construir(self) -> None:
        self.tf = ft.TextField(
            hint_text="Buscar por descripción o Cve Insumo… (Enter)",
            dense=True, prefix_icon=ft.Icons.SEARCH, autofocus=True,
            on_submit=self._buscar, expand=True)
        self.chk_af = ft.Checkbox(label="Solo activo fijo", value=True,
                                  on_change=self._buscar)
        self.lista = ft.Column(spacing=4, scroll=ft.ScrollMode.AUTO, tight=True)
        self.estado = ft.Text("", size=12, color=GRIS)
        # La lista mide según los resultados (hasta _ALTO_MAX) y luego hace scroll.
        self._cont_lista = ft.Container(self.lista, height=_ALTO_FILA)
        self.dialogo = ft.AlertDialog(
            modal=True,
            title=ft.Text("Buscar insumo en el catálogo del SIPP", size=18,
                          weight=ft.FontWeight.BOLD),
            content=ft.Container(
                ft.Column(
                    [ft.Row([self.tf, self.chk_af], spacing=10,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER),
                     self.estado,
                     self._cont_lista],
                    spacing=10, tight=True),
                width=_ANCHO),
            actions=[ft.TextButton("Cerrar", on_click=lambda _e: self.page.pop_dialog())],
        )

    def _ajustar_altura(self, n: int) -> None:
        """Altura de la lista según el nº de resultados, con tope (luego scroll)."""
        self._cont_lista.height = min(_ALTO_MAX, max(_ALTO_FILA, n * _ALTO_FILA + 6))

    def abrir(self, sugerido: str = "") -> None:
        """Abre el selector, precargando la búsqueda con `sugerido` (p. ej. el
        nombre del insumo del levantamiento, como punto de partida)."""
        self.tf.value = sugerido or ""
        if not db.buscar_insumos("", limite=1):
            self.estado.value = ("El catálogo de insumos está vacío. Usa «Actualizar "
                                 "catálogo de insumos» para descargarlo del SIPP.")
            self.estado.color = NARANJA
            self.lista.controls = []
            self._ajustar_altura(0)
        else:
            self._buscar()
        self.page.show_dialog(self.dialogo)

    def _buscar(self, _e=None) -> None:
        texto = (self.tf.value or "").strip()
        resultados = db.buscar_insumos(
            texto, solo_activo_fijo=self.chk_af.value, limite=100)
        self.estado.value = (f"{len(resultados)} resultado(s)"
                             + (" (mostrando 100)" if len(resultados) == 100 else ""))
        self.estado.color = GRIS
        self.lista.controls = [self._fila(i) for i in resultados]
        self._ajustar_altura(len(resultados))
        self._safe_update()

    def _fila(self, ins: "db.Insumo") -> ft.Control:
        detalle = " · ".join(p for p in (ins.familia, ins.subfamilia, ins.unidad) if p)
        return ft.Container(
            content=ft.Row(
                [
                    ft.Container(ft.Text(str(ins.id_insumo), size=12,
                                         weight=ft.FontWeight.BOLD), width=64),
                    ft.Column(
                        [ft.Text(ins.nombre, size=13, no_wrap=True),
                         ft.Text(detalle or "—", size=11, color=GRIS, no_wrap=True)],
                        spacing=0, expand=True, tight=True),
                    ft.FilledTonalButton("Elegir",
                                         on_click=lambda _e, x=ins: self._elegir(x)),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=8),
            padding=ft.Padding.symmetric(horizontal=10, vertical=6),
            border=ft.Border(bottom=ft.BorderSide(
                1, ft.Colors.with_opacity(0.4, ft.Colors.OUTLINE_VARIANT))))

    def _elegir(self, ins: "db.Insumo") -> None:
        self.page.pop_dialog()
        if callable(self.al_elegir):
            self.al_elegir(ins.id_insumo, ins.nombre)
        self.app.avisar(f"Insumo elegido: {ins.nombre}", VERDE)

    def _safe_update(self) -> None:
        try:
            self.dialogo.update()
        except (RuntimeError, AssertionError, AttributeError):
            pass
