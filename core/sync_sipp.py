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
    from . import activos_sipp, empleados, insumos

    # Insumos: es por empresa, así que primero se fija la empresa en la sesión.
    if mensaje:
        mensaje(f"Preparando «{empresa_nombre}»…")
    await sesion.preparar_sesion_empresa(empresa_nombre)
    if mensaje:
        mensaje("Descargando insumos…")
    ins = await insumos.descargar_catalogo(
        sesion, progreso=progreso, solo_activo_fijo=True)

    # Activos de la empresa (para QR y para la búsqueda del levantamiento).
    if mensaje:
        mensaje("Descargando activos…")
    act = await activos_sipp.descargar_activos(sesion, id_empresa, empresa_nombre)

    # Empleados: catálogo global (una sola descarga).
    if mensaje:
        mensaje("Descargando empleados…")
    emp = await empleados.descargar_catalogo(sesion)

    return {"insumos": ins.get("guardados", 0),
            "activos": act.get("guardados", 0),
            "empleados": emp.get("guardados", 0)}
