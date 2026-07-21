"""Color de la barra de título nativa de Windows 11 (DWM). Best-effort.

Pinta el fondo/texto/borde de la barra de título de la ventana para que combine
con el tema de la app (claro/oscuro). Usa DwmSetWindowAttribute (Windows 11,
build 22000+). En Windows 10 y anteriores DWM ignora estos atributos: no-op.

La ventana la crea un proceso flet.exe de forma asíncrona, así que el HWND puede
no existir aún cuando se llama; por eso se sondea en un hilo por el título.
"""

from __future__ import annotations

import sys
import threading
import time

# Atributos de DwmSetWindowAttribute usados aquí.
_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_BORDER_COLOR = 34
_DWMWA_CAPTION_COLOR = 35
_DWMWA_TEXT_COLOR = 36


def _colorref(hex_color: str) -> int:
    """Convierte '#RRGGBB' al entero COLORREF (0x00BBGGRR) que espera DWM."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (b << 16) | (g << 8) | r


def pintar_barra(titulo: str, fondo: str, texto: str, borde: str, oscuro: bool) -> None:
    """Aplica los colores a la ventana cuyo título es `titulo`. Sondea el HWND en
    un hilo (la ventana la crea flet.exe async). No-op fuera de Windows."""
    if sys.platform != "win32":
        return

    def trabajo() -> None:
        import ctypes
        from ctypes import wintypes

        dwmapi = ctypes.windll.dwmapi
        user32 = ctypes.windll.user32
        hwnd = 0
        # Sondea hasta ~3 s a que exista la ventana con ese título.
        for _ in range(30):
            hwnd = user32.FindWindowW(None, titulo)
            if hwnd:
                break
            time.sleep(0.1)
        if not hwnd:
            return

        def set_attr(attr: int, valor: int) -> None:
            v = wintypes.DWORD(valor)
            dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(hwnd), wintypes.DWORD(attr),
                ctypes.byref(v), ctypes.sizeof(v))

        try:
            set_attr(_DWMWA_USE_IMMERSIVE_DARK_MODE, 1 if oscuro else 0)
            set_attr(_DWMWA_CAPTION_COLOR, _colorref(fondo))
            set_attr(_DWMWA_TEXT_COLOR, _colorref(texto))
            set_attr(_DWMWA_BORDER_COLOR, _colorref(borde))
        except Exception:  # noqa: BLE001 — DWM ausente/no soportado: no-op
            pass

    threading.Thread(target=trabajo, daemon=True).start()
