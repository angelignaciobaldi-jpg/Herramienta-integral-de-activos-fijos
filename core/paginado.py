"""Paginado del lado del servidor, común a los listados de la herramienta.

Todos los endpoints paginados de activos fijos responden igual: `data` con las
filas de la página, y en CADA fila un `num` —su posición dentro del listado
completo— y un `total` con el tamaño de toda la consulta. De ahí sale el número
de páginas.

Vive aparte porque lo comparten varios listados y porque es la forma del
servicio, no de ninguno de ellos: con una copia por endpoint, la primera
diferencia entre copias sería un fallo silencioso en la cuenta de páginas.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Tamaño por defecto. Los endpoints EXIGEN `pageSize`, así que no hay un «todos»
# que mandar: si no se pide uno, se pide este.
TAM_PAGINA = 10


@dataclass
class Pagina:
    """Una página de un listado, con lo que hace falta para navegarlo."""

    elementos: list = field(default_factory=list)
    total: int = 0             # elementos de TODA la consulta
    pagina: int = 1
    tam_pagina: int = TAM_PAGINA
    # `num` del primero y del último de la página, tal cual los manda el
    # servidor. Se guardan en vez de calcularlos a partir de la página: si su
    # paginado y esa cuenta discreparan alguna vez, manda él.
    desde: int = 0
    hasta: int = 0

    @property
    def paginas(self) -> int:
        """Cuántas páginas hay. Nunca menos de una: sin resultados sigue
        habiendo una página, la que enseña que no hay nada."""
        if self.total <= 0 or self.tam_pagina <= 0:
            return 1
        return -(-self.total // self.tam_pagina)   # techo de la división

    @property
    def vacia(self) -> bool:
        return not self.elementos


def entero(valor) -> int:
    """`num` llega como cadena («"1"») y `total` como número."""
    try:
        return int(valor)
    except (TypeError, ValueError):
        return 0


def parsear(respuesta: dict, pagina: int, tam_pagina: int, fabricar) -> Pagina:
    """Arma una `Pagina` con lo que devuelva `fabricar(fila)` por cada fila.

    `pagina` y `tam_pagina` los pone quien consultó: el servicio no los devuelve,
    y sin ellos la página no sabría dónde está.

    Tolerante por diseño: filas que no sean objetos se saltan en vez de reventar.
    """
    filas = [f for f in (respuesta.get("data") or []) if isinstance(f, dict)]
    return Pagina(
        elementos=[fabricar(f) for f in filas],
        # `total` viene repetido en cada fila; basta la primera. Sin filas no
        # hay de dónde sacarlo, y cero es la respuesta correcta.
        total=entero(filas[0].get("total")) if filas else 0,
        pagina=max(1, pagina),
        tam_pagina=max(1, tam_pagina),
        desde=entero(filas[0].get("num")) if filas else 0,
        hasta=entero(filas[-1].get("num")) if filas else 0)


def params(pagina: int, tam_pagina: int, empresa: int | None,
           sucursal: int | None, tipo: int | None,
           activo: bool | None = None) -> dict:
    """Parámetros de consulta comunes a los listados.

    `page` y `pageSize` viajan SIEMPRE porque los endpoints los exigen; los
    filtros son opcionales y `None` ni siquiera se manda (ver `api._url`). Ojo
    con `tipo`: el 0 —«sin identificar»— es un valor legítimo.

    `activo` se manda en minúsculas y no como booleano de Python: `urlencode`
    escribiría «True», y el servicio espera «true».
    """
    return {"page": max(1, pagina), "pageSize": max(1, tam_pagina),
            "empresa": empresa, "sucursal": sucursal, "tipo": tipo,
            "activo": None if activo is None else str(bool(activo)).lower()}
