"""Selector de empleado del catálogo del SIPP (búsqueda en la caché local).

El empleado de resguardo se elige aquí (por nombre o id) y el RPA lo selecciona
por su ID exacto en el modal "Buscar Empleado" del alta. Se apoya en
core/db.buscar_empleados (caché descargada con core/empleados).
"""

from __future__ import annotations

import flet as ft

from core import db
from ui.comun import GRIS, NARANJA, VERDE
from ui.componentes import Modal, buscador, fila_resultado, lista_resultados

_ANCHO = 620
_LIMITE = 100


class DialogoSelectorEmpleado:
    """Diálogo de búsqueda/selección de un empleado del catálogo cacheado.

    Mismo estilo que el selector de insumo y que el menú de opciones de la
    tabla: `Modal`, filtrado en vivo, fila pulsable y Enter para el primero.
    """

    def __init__(self, app, al_elegir):
        """`al_elegir(id_empleado, nombre)` se llama cuando el usuario elige uno."""
        self.app = app
        self.page = app.page
        self.al_elegir = al_elegir
        self._resultados: list = []
        self._construir()

    def _construir(self) -> None:
        self.tf = buscador(
            "Buscar por nombre o id de empleado… (Enter elige el primero)",
            on_submit=self._elegir_primero, expand=True, autofocus=True)
        self.tf.on_change = self._buscar
        self.lista = lista_resultados()
        self.estado = ft.Text("", size=12, color=GRIS)

        self.modal = Modal(self.page, "Buscar empleado (resguardo)", ancho=_ANCHO)
        self.modal.cuerpo.spacing = 12
        self.modal.cuerpo.controls = [self.tf, self.estado, self.lista]

    def abrir(self, sugerido: str = "") -> None:
        self.tf.value = sugerido or ""
        if not db.buscar_empleados("", limite=1):
            self._resultados = []
            self.estado.value = ("El catálogo de empleados está vacío. Usa «Actualizar "
                                 "catálogos» para descargarlo del SIPP.")
            self.estado.color = NARANJA
            self.lista.controls = []
        else:
            self._buscar()
        self.modal.abrir()

    def _buscar(self, _e=None) -> None:
        texto = (self.tf.value or "").strip()
        self._resultados = db.buscar_empleados(texto, limite=_LIMITE)
        n = len(self._resultados)
        self.estado.value = (f"{n} resultado(s)"
                             + (f" (mostrando {_LIMITE})" if n == _LIMITE else ""))
        self.estado.color = GRIS
        self.lista.controls = [self._fila(e) for e in self._resultados]
        self.modal.refrescar()

    def _fila(self, emp: "db.Empleado") -> ft.Control:
        return fila_resultado(
            str(emp.id_empleado), emp.nombre, emp.puesto or "",
            on_click=lambda _e, x=emp: self._elegir(x))

    def _elegir_primero(self, _e=None) -> None:
        """Enter: elige el primer resultado. Sin resultados no hace nada."""
        if self._resultados:
            self._elegir(self._resultados[0])

    def _elegir(self, emp: "db.Empleado") -> None:
        self.modal.cerrar()
        if callable(self.al_elegir):
            self.al_elegir(emp.id_empleado, emp.nombre)
        self.app.avisar(f"Empleado elegido: {emp.nombre}", VERDE)
