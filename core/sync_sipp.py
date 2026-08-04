"""Actualización CENTRALIZADA de la información del SIPP para operar.

Un solo login descarga, para la empresa indicada, todo lo que los módulos
operativos necesitan tener al día:

  - INSUMOS de esa empresa (catálogo, para el selector de insumo).
  - ACTIVOS de esa empresa (caché para generar QR y para «Buscar en SIPP»).
  - EMPLEADOS (catálogo GLOBAL, una sola descarga).

Lo usan «Registro de activos» y «Generador de códigos QR» desde un único botón
(«Actualizar información del SIPP»), en vez de descargar cada cosa por separado.
"""

from __future__ import annotations


async def actualizar_sipp(sesion, id_empresa: int, empresa_nombre: str,
                          progreso=None, mensaje=None) -> dict:
    """Descarga insumos + activos (de `id_empresa`) y empleados (global) con la
    `sesion` (SesionSipp ya logueada). Devuelve {insumos, activos, empleados}.

    `progreso(hechos, total)` y `mensaje(texto)`: callbacks opcionales para
    reflejar el avance en la UI.
    """
    from . import activos_sipp, catalogos_sipp, empleados, insumos

    # Insumos: es por empresa, así que primero se fija la empresa en la sesión.
    if mensaje:
        mensaje(f"Preparando «{empresa_nombre}»…")
    await sesion.preparar_sesion_empresa(empresa_nombre)
    if mensaje:
        mensaje("Descargando insumos…")
    # Se cachea el catálogo COMPLETO (no solo activo fijo): la paginación baja todas
    # las páginas igual, así que no cuesta más red, y el selector filtra localmente
    # con su casilla «Solo activo fijo». Antes solo se guardaban los de activo fijo y
    # el resto no aparecía aunque se desmarcara la casilla.
    ins = await insumos.descargar_catalogo(
        sesion, progreso=progreso, solo_activo_fijo=False)

    # Activos de la empresa (para QR y para la búsqueda del levantamiento).
    if mensaje:
        mensaje("Descargando activos…")
    act = await activos_sipp.descargar_activos(sesion, id_empresa, empresa_nombre)

    # Catálogos del alta: departamentos (empresa) + grupos/centros de costo (por
    # sucursal). Usan el id de empresa como argumento, no la sesión.
    cat = await catalogos_sipp.descargar_catalogos(
        sesion, id_empresa, progreso=progreso, mensaje=mensaje)

    # Empleados: catálogo global (una sola descarga).
    if mensaje:
        mensaje("Descargando empleados…")
    emp = await empleados.descargar_catalogo(sesion)

    return {"insumos": ins.get("guardados", 0),
            "activos": act.get("guardados", 0),
            "departamentos": cat.get("departamentos", 0),
            "grupos": cat.get("grupos", 0),
            "centros": cat.get("centros", 0),
            "empleados": emp.get("guardados", 0)}
