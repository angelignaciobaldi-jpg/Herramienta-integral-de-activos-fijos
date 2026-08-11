"""Catálogos de empresas y sucursales servidos por la API.

Son los que alimentan los filtros del tablero. A diferencia de
`core/empresas.py` —una réplica ESTÁTICA versionada en el repo— estos se
consultan en vivo, así que reflejan altas y bajas sin recompilar la app.

De empresas solo se conservan `id_Empresa` y `nb_Empresa`: la respuesta trae
medio centenar de campos (RFC, domicilio, régimen fiscal, plantillas de
notificación…) que esta herramienta no usa y que, guardados, serían datos del
negocio viajando y quedándose en memoria sin motivo.

De sucursales se conserva todo, que es poco y puede hacer falta.
"""

from __future__ import annotations

from dataclasses import dataclass

from core import api

RUTA_EMPRESAS = "api/empresas"
RUTA_SUCURSALES = "api/sucursales"


@dataclass(frozen=True)
class Empresa:
    """Empresa del grupo, reducida a lo que consume la interfaz."""

    id: int
    nombre: str


@dataclass(frozen=True)
class Sucursal:
    """Sucursal de una empresa, con todos los campos que devuelve la API."""

    id: int
    id_empresa: int
    nombre: str
    id_plaza: int | None = None
    comprobacion: bool = False
    area_comprobacion: bool = False
    corporativo: bool = False


def _bandera(valor) -> bool:
    """Normaliza las banderas del servicio.

    La API mezcla estilos en la MISMA respuesta: `sn_Comprobacion` viene como
    booleano y `sn_AreaComprobacion` como 0/1. Sin normalizar, un `if` sobre el
    segundo funcionaría por accidente y se rompería el día que cambie de tipo.
    """
    return bool(valor)


def _filas(respuesta: dict) -> list[dict]:
    """Los registros de `data`, tolerando que falte o venga vacío."""
    datos = respuesta.get("data")
    return datos if isinstance(datos, list) else []


def parsear_empresas(respuesta: dict) -> list[Empresa]:
    """Convierte la respuesta cruda en `Empresa`. Función pura."""
    empresas = [
        Empresa(id=int(f["id_Empresa"]), nombre=str(f.get("nb_Empresa") or ""))
        for f in _filas(respuesta) if f.get("id_Empresa") is not None
    ]
    # Ordenadas por nombre: es como se leen en un combo, y el servicio no
    # garantiza ningún orden.
    return sorted(empresas, key=lambda e: e.nombre.lower())


def parsear_sucursales(respuesta: dict) -> list[Sucursal]:
    """Convierte la respuesta cruda en `Sucursal`. Función pura."""
    sucursales = [
        Sucursal(
            id=int(f["id_Sucursal"]),
            id_empresa=int(f.get("id_Empresa") or 0),
            nombre=str(f.get("nb_Sucursal") or ""),
            id_plaza=f.get("id_Plaza"),
            comprobacion=_bandera(f.get("sn_Comprobacion")),
            area_comprobacion=_bandera(f.get("sn_AreaComprobacion")),
            corporativo=_bandera(f.get("sn_SucursalCorporativo")),
        )
        for f in _filas(respuesta) if f.get("id_Sucursal") is not None
    ]
    return sorted(sucursales, key=lambda s: s.nombre.lower())


def empresas(empresa: int | None = None) -> list[Empresa]:
    """Catálogo de empresas. Sin `empresa` devuelve el listado completo.

    Propaga `api.ErrorAPI` y `entorno.FaltaVariableEntorno` sin envolverlos: sus
    mensajes ya están escritos para el usuario final.
    """
    return parsear_empresas(
        api.solicitar(RUTA_EMPRESAS, params={"empresa": empresa}))


def sucursales(empresa: int) -> list[Sucursal]:
    """Sucursales de una empresa. El parámetro es OBLIGATORIO en el servicio."""
    return parsear_sucursales(
        api.solicitar(RUTA_SUCURSALES, params={"empresa": empresa}))
