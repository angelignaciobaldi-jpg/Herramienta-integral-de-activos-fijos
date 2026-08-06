"""Detalle de inversión en activos fijos (endpoint /api/activos-fijos/inversion).

Es el desglose que hay detrás de la tarjeta «Total de inversión» del tablero: el
importe y el conteo de activos SIN COSTO capturado, por tipo y separando los que
siguen dados de alta de los que ya no.

Los importes llegan YA FORMATEADOS por el servicio y se pintan tal cual, igual
que en `core/dashboard.py`: quien los calcula es quien sabe en qué moneda y con
cuántos decimales van.

`parsear()` está separada de `obtener()` a propósito: es una función pura sobre
un diccionario, así que se puede ejercitar con una respuesta guardada sin
levantar red ni API.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core import api

RUTA = "api/activos-fijos/inversion"

# El SP marca con 1 los activos vigentes y con 0 los dados de baja (`sn_Activo`).
VIGENTE = 1


@dataclass
class ResumenInversion:
    """Las dos cifras de cabecera de un grupo: cuánto vale y cuánto no se sabe."""

    total: str = ""      # importe formateado por el servicio
    sin_costo: int = 0   # activos cuyo costo calculado da cero


@dataclass
class LineaInversion:
    """Un renglón del detalle: un tipo de activo dentro de un grupo."""

    id_tipo: int
    nombre: str
    total: str
    sin_costo: int


@dataclass
class DatosInversion:
    """Detalle completo, ya repartido en los dos grupos.

    La separación por `activo` se hace AQUÍ y no en la pantalla: el servicio
    manda una sola lista con una bandera, y dejar que la interfaz la filtre le
    obligaría a conocer que 1 es «vigente».
    """

    vigentes: ResumenInversion = field(default_factory=ResumenInversion)
    bajas: ResumenInversion = field(default_factory=ResumenInversion)
    detalle_vigentes: list[LineaInversion] = field(default_factory=list)
    detalle_bajas: list[LineaInversion] = field(default_factory=list)

    @property
    def vacio(self) -> bool:
        """Si no hay absolutamente nada que enseñar.

        Se mira el DETALLE y no los totales: el resumen siempre trae sus dos
        renglones —el SP los fuerza—, aunque vengan en cero.
        """
        return not self.detalle_vigentes and not self.detalle_bajas


def _resumen(fila: dict) -> ResumenInversion:
    return ResumenInversion(total=str(fila.get("total") or "").strip(),
                            sin_costo=int(fila.get("inversion_cero") or 0))


def _linea(fila: dict) -> LineaInversion:
    return LineaInversion(id_tipo=int(fila.get("id") or 0),
                          nombre=str(fila.get("nombre") or ""),
                          total=str(fila.get("total") or "").strip(),
                          sin_costo=int(fila.get("inversion_cero") or 0))


def _es_vigente(fila: dict) -> bool:
    """`activo` llega como número, pero se tolera el booleano.

    Si el SP dejara de convertir `sn_Activo` a INT, `FOR JSON` lo mandaría como
    `true`/`false` y una comparación contra 1 diría que todo son bajas.
    """
    valor = fila.get("activo")
    return valor is True or valor == VIGENTE


def parsear(respuesta: dict) -> DatosInversion:
    """Convierte la respuesta cruda del endpoint en `DatosInversion`.

    Tolerante por diseño: un bloque que falte deja su parte vacía en vez de
    reventar.
    """
    d = api.desanidar(respuesta, "json_inversion")
    datos = DatosInversion()

    for fila in (d.get("valor") or []):
        if not isinstance(fila, dict):
            continue
        if _es_vigente(fila):
            datos.vigentes = _resumen(fila)
        else:
            datos.bajas = _resumen(fila)

    for fila in (d.get("valor_detallado") or []):
        if not isinstance(fila, dict):
            continue
        destino = (datos.detalle_vigentes if _es_vigente(fila)
                   else datos.detalle_bajas)
        destino.append(_linea(fila))

    return datos


def obtener(empresa: int | None = None, sucursal: int | None = None,
            tipo: int | None = None) -> DatosInversion:
    """Consulta el detalle para el ámbito pedido.

    Los tres son ids del SIPP y `None` significa «todos», que ni siquiera viaja
    en la URL (ver `api._url`). Ojo con `tipo`: el 0 —«sin identificar»— es un
    valor LEGÍTIMO, así que la diferencia entre 0 y None importa.

    Propaga `api.ErrorAPI` y `entorno.FaltaVariableEntorno` sin envolverlos: sus
    mensajes ya están escritos para el usuario.
    """
    respuesta = api.solicitar(
        RUTA, params={"empresa": empresa, "sucursal": sucursal, "tipo": tipo})
    return parsear(respuesta)
