"""Capa abstracta para consultar activos en el SIPP por número de serie.

Aísla a la UI de CÓMO se obtiene la información (API, RPA o datos de prueba), de
modo que se pueda cambiar la fuente sin tocar la pantalla. Hoy (Fase 1) se usa
`ProveedorMock` con datos deterministas; cuando exista el endpoint se activa
`ProveedorAPI`, o `ProveedorRPA` si se decide raspar el portal.

Contrato: `buscar_por_serie(series)` recibe una lista de números de serie y
devuelve, por cada uno, un `ResultadoBusqueda` indicando si el activo YA está dado
de alta en el catálogo del SIPP y, si aplica, sus datos.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


@dataclass
class ResultadoBusqueda:
    """Resultado de consultar un número de serie en el catálogo del SIPP.

    dado_de_alta:  True si el activo ya existe en el catálogo (listado).
    id_activo_sipp: identificador del activo en el SIPP (si dado_de_alta).
    datos:         campos conocidos del activo (para prellenar/verificar).
    """

    dado_de_alta: bool
    id_activo_sipp: "str | None" = None
    datos: dict = field(default_factory=dict)


class ProveedorActivos:
    """Interfaz de un proveedor de datos de activos. Implementa `buscar_por_serie`."""

    def buscar_por_serie(self, series: list[str]) -> dict[str, ResultadoBusqueda]:
        """Devuelve {no_serie -> ResultadoBusqueda} para cada serie consultada."""
        raise NotImplementedError


class ProveedorMock(ProveedorActivos):
    """Proveedor de PRUEBA (Fase 1). No consulta nada real: decide de forma
    DETERMINISTA (por el hash de la serie) si un activo está "dado de alta", para
    poder probar toda la UI (búsqueda, categorización, pestañas) sin el SIPP.

    Regla: se considera "dado de alta" ~50% de las series (según el primer byte
    del hash). Estable entre corridas: la misma serie siempre cae en la misma
    categoría."""

    def buscar_por_serie(self, series: list[str]) -> dict[str, ResultadoBusqueda]:
        resultado: dict[str, ResultadoBusqueda] = {}
        for serie in series:
            clave = (serie or "").strip()
            h = hashlib.sha1(clave.encode("utf-8")).digest()
            dado = bool(h[0] & 1)  # ~50% deterministamente
            if dado:
                resultado[serie] = ResultadoBusqueda(
                    dado_de_alta=True,
                    id_activo_sipp=f"MOCK-{h[:3].hex().upper()}",
                    datos={"no_serie": clave, "origen": "mock"},
                )
            else:
                resultado[serie] = ResultadoBusqueda(dado_de_alta=False)
        return resultado


class ProveedorAPI(ProveedorActivos):
    """(Fase 2) Consulta el listado de activos vía los microservicios (core/api.py).

    Pendiente: definir el endpoint real (p. ej. GET /activos?serie=...). Mientras
    tanto NO se usa; se deja el esqueleto para enchufarlo sin tocar la UI."""

    def buscar_por_serie(self, series: list[str]) -> dict[str, ResultadoBusqueda]:
        raise NotImplementedError(
            "ProveedorAPI aún no está disponible: falta el endpoint de la API. "
            "Usa ProveedorMock (Fase 1) o ProveedorRPA."
        )


class ProveedorRPA(ProveedorActivos):
    """(Fase 2) Consulta el listado del SIPP con el RPA (core/rpa_sipp.SesionSipp):
    filtra `js_filtroListado.de_SerieActivo`, aplica el filtro y lee el grid.

    Pendiente: implementar el flujo Playwright (login + navegación + filtro +
    recorrido del ngGrid). Se deja el esqueleto para Fase 2."""

    def __init__(self, credenciales: "tuple[str, str] | None" = None):
        self.credenciales = credenciales

    def buscar_por_serie(self, series: list[str]) -> dict[str, ResultadoBusqueda]:
        raise NotImplementedError(
            "ProveedorRPA se implementa en Fase 2 (flujo Playwright sobre el "
            "listado del SIPP)."
        )


# Proveedor por defecto de la app (Fase 1: MOCK). Cambiar aquí para enchufar la
# API/RPA cuando estén listos, sin tocar la pantalla.
def proveedor_por_defecto() -> ProveedorActivos:
    """Devuelve el proveedor activo de la app. Fase 1 -> ProveedorMock."""
    return ProveedorMock()
