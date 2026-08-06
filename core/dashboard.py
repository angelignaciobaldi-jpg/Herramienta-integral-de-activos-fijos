"""Datos del tablero de activos fijos (endpoint /api/activos-fijos/dashboard).

El desanidado de las dos capas de JSON con que responde el SP vive en
`core/api.py` (`desanidar`), porque es la forma del sobre del servicio y no algo
propio del tablero.

`parsear()` está separada de `obtener()` a propósito: es una función pura sobre un
diccionario, así que se puede ejercitar con una respuesta guardada sin levantar
red ni API.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core import api

RUTA = "api/activos-fijos/dashboard"

# El SP marca con 0 los activos sin tipo asignado ("Sin definir"); el catálogo
# real (core/tipos_activo.py) empieza en 1.
ID_SIN_TIPO = 0


@dataclass
class DatosDashboard:
    """Cifras del tablero, ya desanidadas y en tipos de Python.

    Las listas van como tuplas planas —y no como los diccionarios del JSON—
    porque es lo que consumen las tarjetas, y así la interfaz no tiene que
    conocer los nombres de campo del servicio.
    """

    activos: int = 0
    inactivos: int = 0
    total: int = 0
    # Movimiento del mes en curso. Son CONTEOS del periodo, no la diferencia
    # contra el total de hace un mes: el SP cuenta 'REGISTRO' e 'INACTIVAR' en
    # vez de reconstruir un total histórico que la bitácora no garantiza.
    altas_mes: int = 0
    bajas_mes: int = 0
    # (id_tipo, nombre, total). El NOMBRE viene del servidor: es él quien manda
    # sobre el catálogo, y la interfaz solo usa el id para elegir el ícono.
    desglose: list[tuple[int, str, int]] = field(default_factory=list)
    # (nombre, total), ya ordenadas de mayor a menor por el SP.
    top_empresas: list[tuple[str, int]] = field(default_factory=list)
    # (tipo, movimiento, tiempo). `tipo` es la clave del movimiento.
    actividad: list[tuple[str, str, str]] = field(default_factory=list)
    # Importe de la inversión, YA FORMATEADO por el servicio ("$41,383,453.26").
    # Llega como texto y se pinta tal cual, sin reconvertirlo: quien lo calcula
    # es quien sabe en qué moneda y con cuántos decimales va, y volver a
    # formatearlo aquí sería una segunda opinión que puede contradecirlo.
    valor: str = ""


def parsear(respuesta: dict) -> DatosDashboard:
    """Convierte la respuesta cruda del endpoint en `DatosDashboard`.

    Tolerante por diseño: un bloque que falte deja su parte vacía en vez de
    reventar. Un tablero al que le falta el podio sigue sirviendo; uno que lanza
    excepción, no.
    """
    d = api.desanidar(respuesta, "json_dashboard")
    totales = api.primero(d.get("totales"))
    mes = api.primero(d.get("movimientos_mes"))
    valor = api.primero(d.get("valor"))

    return DatosDashboard(
        activos=int(totales.get("activos") or 0),
        inactivos=int(totales.get("inactivos") or 0),
        total=int(totales.get("total") or 0),
        altas_mes=int(mes.get("altas") or 0),
        bajas_mes=int(mes.get("bajas") or 0),
        desglose=[
            (int(r.get("activo_fijo") or 0),
             str(r.get("nombre") or ""),
             int(r.get("total") or 0))
            for r in (d.get("desgloce") or [])
        ],
        top_empresas=[
            (str(e.get("nombre") or ""), int(e.get("total") or 0))
            for e in (d.get("top_empresas") or [])
        ],
        actividad=[
            (str(a.get("tipo") or ""),
             str(a.get("movimiento") or ""),
             str(a.get("tiempo") or ""))
            for a in (d.get("actividad") or [])
        ],
        # Sin `int()` ni formateo: llega listo para pintarse.
        valor=str(valor.get("total") or "").strip(),
    )


def obtener(empresa: int | None = None, sucursal: int | None = None,
            tipo: int | None = None) -> DatosDashboard:
    """Consulta el tablero para el ámbito pedido.

    `empresa`, `sucursal` y `tipo` son ids del SIPP; `None` significa «todos» y
    ni siquiera viaja en la URL (ver `api._url`). Ojo con `tipo`: `ID_SIN_TIPO`
    es 0 y es un valor LEGÍTIMO —los activos sin clasificar—, así que la
    diferencia entre 0 y None importa y no se puede colapsar con un `if tipo`.

    Propaga `api.ErrorAPI` y `entorno.FaltaVariableEntorno` sin envolverlos: sus
    mensajes ya están escritos para el usuario, y quien llama decide cómo
    mostrarlos.
    """
    respuesta = api.solicitar(
        RUTA, params={"empresa": empresa, "sucursal": sucursal, "tipo": tipo})
    return parsear(respuesta)
