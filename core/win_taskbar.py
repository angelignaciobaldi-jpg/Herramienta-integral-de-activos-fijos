"""Identidad de la app en la barra de tareas de Windows (AppUserModelID). Best-effort.

Al fijar un AppUserModelID (AUMID) propio, Windows agrupa la ventana (creada por
el flet.exe cliente) y los accesos directos bajo la MISMA identidad, con el icono
y el comando de re-lanzamiento correctos al anclar a la barra de tareas.

El AUMID DEBE coincidir con el declarado en instalador.iss ([Icons] AppUserModelID).
No-op fuera de Windows o si la API no está disponible.
"""

from __future__ import annotations

import sys

# DEBE coincidir con el AppUserModelID de instalador.iss.
AUMID = "QuetzalticSolutions.HerramientasActivosFijos"


def configurar_identidad(
    titulo: str, relaunch_cmd: str, icon_path: str | None = None,
    display: str | None = None,
) -> None:
    """Fija el AppUserModelID del proceso actual. Best-effort: cualquier fallo se
    ignora (la identidad de la barra no es crítica para operar)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(AUMID)
    except Exception:  # noqa: BLE001 — la identidad de la barra no es crítica
        pass
