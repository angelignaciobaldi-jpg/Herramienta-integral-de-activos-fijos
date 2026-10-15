from __future__ import annotations

"""Caché del cliente de escritorio de Flet: detectarla rota y rehacerla.

Flet no dibuja la ventana desde Python: lanza un ejecutable aparte
(`flet.exe`) que vive en `~/.flet/client/flet-desktop-<versión>/flet/`. El
instalador trae ese cliente comprimido (`flet_desktop/app/flet-windows.zip`) y
Flet lo descomprime ahí la primera vez que se abre la app.

El problema es cómo decide Flet que ya lo tiene: comprueba que la CARPETA
exista, no que el ejecutable esté dentro. Así que si algo se lleva el
`flet.exe` —un antivirus, porque es un binario de Flutter sin firmar; o una
descompresión que quedó a medias— Flet da la caché por buena y arranca el
proceso igual. El resultado es el arranque muriendo con

    [WinError 2] El sistema no puede encontrar el archivo especificado

desde `create_subprocess_exec`, sin decir QUÉ archivo falta y sin forma de que
el usuario lo arregle.

La reparación es quitar de en medio esas carpetas antes de arrancar: sin
carpeta, Flet vuelve a descomprimir el ZIP que viaja en el paquete, así que no
hace falta internet ni reinstalar.
"""

import shutil
import sys
from pathlib import Path

# Donde Flet guarda el cliente ya descomprimido, una carpeta por versión.
RAIZ = Path.home() / ".flet" / "client"
_PREFIJO = "flet-desktop-"


def _ejecutable(carpeta: Path) -> Path:
    return carpeta / "flet" / "flet.exe"


def _inservible(carpeta: Path) -> bool:
    """¿Esta caché existe pero NO sirve para arrancar?

    Se mira el tamaño además de la existencia: algunos antivirus no borran el
    archivo, lo dejan en cero al ponerlo en cuarentena, y un `flet.exe` vacío
    falla igual pero más tarde y peor.
    """
    exe = _ejecutable(carpeta)
    try:
        return not exe.is_file() or exe.stat().st_size == 0
    except OSError:
        return True      # ilegible (permisos, ruta rota): tampoco sirve


def reparar() -> list[str]:
    """Borra las cachés del cliente que no tengan su ejecutable utilizable.

    Devuelve los nombres de las carpetas borradas (vacío si no había nada que
    hacer). Best-effort y silenciosa: corre en el arranque, antes de que haya
    interfaz con la que avisar, y un fallo aquí no debe impedir abrir la app
    —como mucho se vuelve al error de siempre—.

    Solo toca carpetas SIN ejecutable, así que no puede quitarle el suelo a una
    instancia que esté corriendo: si está corriendo, su ejecutable está ahí.
    """
    borradas: list[str] = []
    if sys.platform != "win32":
        # El nombre del cliente cambia por plataforma (.app en macOS); esta app
        # solo se distribuye para Windows y no hay forma de probar el resto.
        return borradas
    try:
        if not RAIZ.is_dir():
            return borradas
        for carpeta in RAIZ.iterdir():
            if not carpeta.is_dir() or not carpeta.name.startswith(_PREFIJO):
                continue
            if not _inservible(carpeta):
                continue
            shutil.rmtree(carpeta, ignore_errors=True)
            if not carpeta.exists():
                borradas.append(carpeta.name)
    except OSError:
        pass
    return borradas


def diagnostico() -> str:
    """Texto corto con el estado de las cachés, para el reporte de un fallo."""
    if not RAIZ.is_dir():
        return f"No existe {RAIZ} (el cliente se descomprimirá al arrancar)."
    lineas = []
    for carpeta in sorted(RAIZ.iterdir()):
        if carpeta.is_dir() and carpeta.name.startswith(_PREFIJO):
            estado = "SIN ejecutable" if _inservible(carpeta) else "completa"
            lineas.append(f"  {carpeta.name}: {estado}")
    return f"Cachés del cliente en {RAIZ}:\n" + ("\n".join(lineas) or "  (ninguna)")
