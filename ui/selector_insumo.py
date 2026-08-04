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
from ui.componentes import Modal, buscador, fila_resultado, lista_resultados

_ANCHO = 620
_LIMITE = 100


class DialogoSelectorInsumo:
    """Diálogo de búsqueda/selección de un insumo del catálogo cacheado.

    Sigue el estilo del menú de opciones de la tabla: armazón `Modal` (fundido,
    Esc y botón de cierre), filtrado EN VIVO al teclear, fila entera pulsable y
    Enter para elegir el primer resultado.
    """

    def __init__(self, app, al_elegir):
        """`al_elegir(id_insumo, nombre)` se llama cuando el usuario elige uno."""
        self.app = app
        self.page = app.page
        self.al_elegir = al_elegir
        self._resultados: list = []
        self._construir()

    def _construir(self) -> None:
        self.tf = buscador(
            "Buscar por descripción o Cve Insumo… (Enter elige el primero)",
            on_submit=self._elegir_primero, expand=True, autofocus=True)
        # El catálogo es local (SQLite), así que filtrar al teclear es viable y
        # ahorra el paso de pulsar Enter para ver resultados.
        self.tf.on_change = self._buscar
        self.chk_af = ft.Checkbox(label="Solo activo fijo", value=True,
                                  on_change=self._buscar)
        self.lista = lista_resultados()
        self.estado = ft.Text("", size=12, color=GRIS)

        self.modal = Modal(self.page, "Buscar insumo en el catálogo del SIPP",
                           ancho=_ANCHO)
        self.modal.cuerpo.spacing = 12
        self.modal.cuerpo.controls = [
            ft.Row([self.tf, self.chk_af], spacing=10,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            self.estado,
            self.lista,
        ]

    def abrir(self, sugerido: str = "") -> None:
        """Abre el selector, precargando la búsqueda con `sugerido` (p. ej. el
        nombre del insumo del levantamiento, como punto de partida)."""
        self.tf.value = sugerido or ""
        if not db.buscar_insumos("", limite=1):
            self._resultados = []
            self.estado.value = ("El catálogo de insumos está vacío. Usa «Actualizar "
                                 "información del SIPP» para descargarlo.")
            self.estado.color = NARANJA
            self.lista.controls = []
        else:
            self._buscar()
        self.modal.abrir()

    def _buscar(self, _e=None) -> None:
        texto = (self.tf.value or "").strip()
        self._resultados = db.buscar_insumos(
            texto, solo_activo_fijo=self.chk_af.value, limite=_LIMITE)
        n = len(self._resultados)
        total = db.contar_insumos(texto, solo_activo_fijo=self.chk_af.value)
        # El tope evita pintar miles de filas (congelaría); si hay más, se avisa que
        # escriba para acotar (el filtro corre sobre TODO el catálogo en la base).
        if total > n:
            self.estado.value = (f"Mostrando {n} de {total:,}. Escribe una descripción "
                                 f"o Cve para acotar la búsqueda.")
        else:
            self.estado.value = f"{total:,} resultado(s)"
        self.estado.color = GRIS
        self.lista.controls = [self._fila(i) for i in self._resultados]
        self.modal.refrescar()

    def _fila(self, ins: "db.Insumo") -> ft.Control:
        detalle = " · ".join(p for p in (ins.familia, ins.subfamilia, ins.unidad) if p)
        return fila_resultado(
            str(ins.id_insumo), ins.nombre, detalle,
            on_click=lambda _e, x=ins: self._elegir(x))

    def _elegir_primero(self, _e=None) -> None:
        """Enter: elige el primer resultado. Sin resultados no hace nada, en vez
        de cerrar sin elegir."""
        if self._resultados:
            self._elegir(self._resultados[0])

    def _elegir(self, ins: "db.Insumo") -> None:
        self.modal.cerrar()
        if callable(self.al_elegir):
            self.al_elegir(ins.id_insumo, ins.nombre)
        self.app.avisar(f"Insumo elegido: {ins.nombre}", VERDE)
