"""Tema visual de la aplicación — sistema de diseño "Systematic Integrity".

Traduce los tokens de [DISENO.md](../DISENO.md) a los objetos de Flet. Es la
FUENTE ÚNICA del color y la tipografía: las pantallas no deben escribir hex
sueltos, sino usar los ROLES de Material 3 (`ft.Colors.PRIMARY`,
`ON_SURFACE_VARIANT`, `ERROR`, `SURFACE_CONTAINER_HIGHEST`, …). Así el cambio de
paleta se aplica aquí y se propaga solo.

Sobre la paleta oscura: NO es un invento aparte. Material 3 deriva ambos temas de
la misma rampa tonal, y los tokens `*-fixed` del tema claro ya contienen los tonos
que el oscuro necesita, así que el oscuro se arma reusándolos (p. ej. el
`primary` oscuro es el `inverse-primary`/`primary-fixed-dim` del claro). Por eso
los dos temas combinan en lugar de parecer dos diseños distintos.

Dos tokens de DISENO.md no tienen destino en Flet porque Material 3 los deprecó y
los fusionó con `surface`: `background`/`on-background` y `surface-variant`.
"""

from __future__ import annotations

import flet as ft

# Inter NO está en el repo (no hay .ttf/.otf). Al agregarla y registrarla con
# `page.fonts = {"Inter": "..."}`, poner "Inter" aquí y todo el tema la adopta.
# Con None, Flet usa la tipografía del sistema (Segoe UI en Windows).
FUENTE: str | None = None

# Colores de la barra de título nativa (DWM). Se mantienen a juego con
# `surface`/`on-surface` de cada esquema para que la ventana se vea de una pieza.
BARRA_FONDO_CLARO = "#f9f9f9"
BARRA_TEXTO_CLARO = "#1a1c1c"
BARRA_FONDO_OSCURO = "#121314"
BARRA_TEXTO_OSCURO = "#e2e2e2"

# Los tokens `*-fixed` son, por definición de Material 3, IDÉNTICOS en claro y
# oscuro (de ahí el nombre). Se declaran una vez y se reparten a ambos esquemas.
_FIJOS = dict(
    primary_fixed="#e0e0ff",
    primary_fixed_dim="#bdc2ff",
    on_primary_fixed="#000767",
    on_primary_fixed_variant="#343d96",
    secondary_fixed="#d1e4ff",
    secondary_fixed_dim="#9ecaff",
    on_secondary_fixed="#001d36",
    on_secondary_fixed_variant="#00497d",
    tertiary_fixed="#dee0ff",
    tertiary_fixed_dim="#bac3ff",
    on_tertiary_fixed="#00105c",
    on_tertiary_fixed_variant="#293ca0",
)

ESQUEMA_CLARO = ft.ColorScheme(
    primary="#000666",
    on_primary="#ffffff",
    primary_container="#1a237e",
    on_primary_container="#8690ee",
    secondary="#0061a4",
    on_secondary="#ffffff",
    secondary_container="#33a0fd",
    on_secondary_container="#00355c",
    tertiary="#000f5b",
    on_tertiary="#ffffff",
    tertiary_container="#072189",
    on_tertiary_container="#7e90f8",
    error="#ba1a1a",
    on_error="#ffffff",
    error_container="#ffdad6",
    on_error_container="#93000a",
    surface="#f9f9f9",
    on_surface="#1a1c1c",
    on_surface_variant="#454652",
    surface_dim="#dadada",
    surface_bright="#f9f9f9",
    surface_container_lowest="#ffffff",
    surface_container_low="#f3f3f3",
    surface_container="#eeeeee",
    surface_container_high="#e8e8e8",
    surface_container_highest="#e2e2e2",
    outline="#767683",
    outline_variant="#c6c5d4",
    inverse_surface="#2f3131",
    on_inverse_surface="#f1f1f1",
    inverse_primary="#bdc2ff",
    surface_tint="#4c56af",
    **_FIJOS,
)

# Oscuro: mismos tonos, invertidos como manda Material 3. El primario pasa al
# tono claro (80) y su contenedor al oscuro (30); las superficies bajan a tonos
# 4-22 y los "on-" suben a 80-90.
ESQUEMA_OSCURO = ft.ColorScheme(
    primary="#bdc2ff",             # = inverse_primary del claro (tono 80)
    on_primary="#1a2280",          # tono 20
    primary_container="#343d96",   # = on_primary_fixed_variant (tono 30)
    on_primary_container="#e0e0ff",  # = primary_fixed (tono 90)
    secondary="#9ecaff",
    on_secondary="#003258",
    secondary_container="#00497d",
    on_secondary_container="#d1e4ff",
    tertiary="#bac3ff",
    on_tertiary="#10267e",
    tertiary_container="#293ca0",
    on_tertiary_container="#dee0ff",
    error="#ffb4ab",
    on_error="#690005",
    error_container="#93000a",
    on_error_container="#ffdad6",
    surface="#121314",
    on_surface="#e2e2e2",
    on_surface_variant="#c6c5d4",  # = outline_variant del claro
    surface_dim="#121314",
    surface_bright="#38393a",
    surface_container_lowest="#0d0e0f",
    surface_container_low="#1a1c1c",
    surface_container="#1e2021",
    surface_container_high="#292a2b",
    surface_container_highest="#343536",
    outline="#90909d",
    outline_variant="#454652",     # = on_surface_variant del claro
    inverse_surface="#e2e2e2",
    on_inverse_surface="#2f3131",
    inverse_primary="#4c56af",
    surface_tint="#bdc2ff",
    **_FIJOS,
)

# Tipografía. `height` en Flet es MULTIPLICADOR (lineHeight / fontSize) y
# `letter_spacing` va en PÍXELES, no en em: los valores del diseño (-0.02em a
# 32px) se convierten aquí (-0.64px).
#
# El `color` es OBLIGATORIO en cada estilo: declarar un TextTheme sustituye a la
# tipografía por defecto de Material, y un estilo sin color deja el texto en
# blanco (invisible sobre fondos claros). Se pone el ROL `ON_SURFACE`, no un hex,
# para que lo resuelva el esquema activo y el modo oscuro siga funcionando.
# Quien necesite otro color lo fija en su propio control, que tiene precedencia.
_TINTA = ft.Colors.ON_SURFACE

TIPOGRAFIA = ft.TextTheme(
    headline_large=ft.TextStyle(
        size=32, weight=ft.FontWeight.W_700, height=1.25, letter_spacing=-0.64,
        color=_TINTA),
    headline_medium=ft.TextStyle(
        size=24, weight=ft.FontWeight.W_600, height=1.333, letter_spacing=-0.24,
        color=_TINTA),
    headline_small=ft.TextStyle(
        size=20, weight=ft.FontWeight.W_600, height=1.4, color=_TINTA),
    body_large=ft.TextStyle(
        size=16, weight=ft.FontWeight.W_400, height=1.5, color=_TINTA),
    body_medium=ft.TextStyle(
        size=14, weight=ft.FontWeight.W_400, height=1.429, color=_TINTA),
    label_large=ft.TextStyle(
        size=14, weight=ft.FontWeight.W_600, height=1.429, letter_spacing=0.14,
        color=_TINTA),
    label_medium=ft.TextStyle(
        size=12, weight=ft.FontWeight.W_500, height=1.333, letter_spacing=0.24,
        color=_TINTA),
)

# El token `code` del diseño no tiene rol en Material; además Inter no es
# monoespaciada y donde se usa (detalle técnico de la pantalla de error) la
# alineación por columnas SÍ importa. Se expone como estilo suelto.
CODIGO = ft.TextStyle(size=13, weight=ft.FontWeight.W_400, height=1.385,
                      font_family="monospace")


def construir_tema(oscuro: bool, scrollbar_theme=None) -> ft.Theme:
    """Tema completo (color + tipografía) para el modo pedido.

    `scrollbar_theme` se recibe en vez de fijarse aquí porque lo define el shell
    y debe ser el MISMO objeto en el tema claro y en el oscuro.
    """
    return ft.Theme(
        color_scheme=ESQUEMA_OSCURO if oscuro else ESQUEMA_CLARO,
        text_theme=TIPOGRAFIA,
        font_family=FUENTE,
        scrollbar_theme=scrollbar_theme,
    )
