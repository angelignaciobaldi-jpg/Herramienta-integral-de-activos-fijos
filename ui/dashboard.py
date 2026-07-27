"""Pantalla "Dashboard activos fijos".

Módulo (por ahora VACÍO / andamiaje) donde vivirá un tablero con gráficos e
indicadores del catálogo de activos fijos. Se deja listo el esqueleto modular
(expone `.contenido` y `_on_resize`) para irle agregando los gráficos.
"""

from __future__ import annotations

import flet as ft

from ui.comun import placeholder


class SeccionDashboard:
    """Tablero de activos fijos (pendiente de contenido)."""

    def __init__(self, app):
        self.app = app
        self.page = app.page
        self._construir()

    def _construir(self) -> None:
        self.contenido = ft.Column(
            [
                ft.Text("Dashboard activos fijos", size=20, weight=ft.FontWeight.BOLD),
                ft.Divider(),
                placeholder(
                    "Tablero en construcción",
                    "Aquí irán los gráficos e indicadores del catálogo de activos fijos.",
                    ft.Icons.DASHBOARD),
            ],
            expand=True, spacing=12,
        )

    def _on_resize(self, _e=None) -> None:
        """Contenido fluido; no requiere recomputar. Presente por consistencia."""

    def _safe_update(self) -> None:
        try:
            self.contenido.update()
        except (RuntimeError, AssertionError, AttributeError):
            pass
