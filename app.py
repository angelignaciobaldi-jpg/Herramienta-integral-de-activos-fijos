"""Herramienta Integral de Activos Fijos — punto de entrada (shell).

Arma la ventana: encabezado con logo y botón de modo claro/oscuro, navegación y
el área de contenido. Cada pantalla es un módulo independiente en ui/, para que
se pueda trabajar en colaboración sin pisarse:

    ui/registro_activos.py       -> "Registro de activos"
    ui/extraccion_documentos.py  -> "Extracción de documentos" (OCR)
    ui/automatizacion_sipp.py    -> "Automatización SIPP" (RPA)
    ui/exportacion.py            -> "Exportación / Reportes"
    ui/configuracion.py          -> modal de Configuración (credenciales SIPP)
    ui/comun.py                  -> constantes y utilidades compartidas

El shell expone a cada pantalla: page, picker (diálogos de archivo) y avisar().
"""

from __future__ import annotations

import asyncio
import os
import sys
import traceback

import flet as ft

# Solo se importa al arranque lo IMPRESCINDIBLE y estable (flet + rutas). El resto
# de módulos (core.db/ocr/preferencias y las pantallas de ui) se importan de forma
# PEREZOSA dentro de las funciones, para que un módulo roto no impida cargar app.py
# ni, sobre todo, que corra el auto-updater (que podría traer la corrección).
from core import rutas

# Colores de la barra de título nativa (DWM) según el tema.
_BARRA_FONDO_CLARO = "#FEF7FF"
_BARRA_TEXTO_CLARO = "#1D1B20"
_BARRA_FONDO_OSCURO = "#141218"
_BARRA_TEXTO_OSCURO = "#E6E0E9"

TITULO_APP = "Herramienta Integral de Activos Fijos"
NOMBRE_CORTO = "Herramientas Activos Fijos"
NOMBRE_EXE = "ActivosFijos.exe"


class AppActivosFijos:
    """Shell de la aplicación: ventana, encabezado, tema y navegación."""

    def __init__(self, page: ft.Page):
        self.page = page
        self.picker = ft.FilePicker()
        page.services.append(self.picker)
        self._picker_al_frente()
        self._on_resize_cbs: list = []
        self._construir()

    # ------------------------------------------------ redimensionado
    def registrar_on_resize(self, callback) -> None:
        """Registra un listener para el evento de redimensionado de la ventana."""
        if callable(callback):
            self._on_resize_cbs.append(callback)

    def _despachar_resize(self, e) -> None:
        for cb in self._on_resize_cbs:
            try:
                cb(e)
            except Exception:  # noqa: BLE001 — un listener no debe tumbar el resize
                pass

    # ------------------------------------------------ diálogos de archivo
    def _picker_al_frente(self) -> None:
        """Envuelve los métodos del FilePicker para que el diálogo del sistema
        aparezca SIEMPRE por encima de la app (en Windows suele abrirse detrás)."""
        for nombre in ("pick_files", "get_directory_path", "save_file"):
            original = getattr(self.picker, nombre, None)
            if callable(original):
                setattr(self.picker, nombre, self._envolver_al_frente(original))

    def _envolver_al_frente(self, original):
        async def envuelto(*args, **kwargs):
            self._fijar_topmost(True)
            try:
                return await original(*args, **kwargs)
            finally:
                self._fijar_topmost(False)
        return envuelto

    def _fijar_topmost(self, valor: bool) -> None:
        try:
            self.page.window.always_on_top = valor
            self.page.update()
        except Exception:  # noqa: BLE001 — el traer-al-frente no es crítico
            pass

    def abrir_en_sistema(self, ruta: str) -> None:
        """Abre un archivo o carpeta en el programa predeterminado y lo trae AL
        FRENTE de la app (best-effort)."""
        self._fijar_topmost(False)
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
            except Exception:  # noqa: BLE001
                pass
        try:
            os.startfile(ruta)  # noqa: S606 — abre en el visor/Explorador predeterminado
        except Exception as exc:  # noqa: BLE001 — se reporta al usuario
            self.avisar(f"No se pudo abrir: {exc}", ft.Colors.RED_700)

    # Servicio compartido: aviso tipo snackbar (opcional: botón de acción + callback).
    def avisar(self, mensaje: str, color: str | None = None,
               accion: str | None = None, on_accion=None, duracion=None) -> None:
        barra = ft.SnackBar(
            content=ft.Text(mensaje, color=ft.Colors.WHITE), bgcolor=color)
        if accion:
            barra.action = accion
            barra.on_action = on_accion
        if duracion is not None:
            barra.duration = duracion
        self.page.show_dialog(barra)

    def _construir(self) -> None:
        # Import perezoso de las pantallas: si una estuviera rota, el error se
        # contiene en _arrancar_app (que lo muestra en pantalla) en vez de tumbar
        # todo el proceso.
        from ui.automatizacion_sipp import SeccionAutomatizacionSipp
        from ui.configuracion import SeccionConfiguracion
        from ui.exportacion import SeccionExportacion
        from ui.extraccion_documentos import SeccionExtraccionDocumentos
        from ui.registro_activos import SeccionRegistroActivos

        self.config = SeccionConfiguracion(self)
        self.registro = SeccionRegistroActivos(self)
        self.extraccion = SeccionExtraccionDocumentos(self)
        self.automatizacion = SeccionAutomatizacionSipp(self)
        self.exportacion = SeccionExportacion(self)

        # Área de contenido: todas las pantallas viven aquí; solo se muestra la
        # activa (se alterna 'visible'), en vez de un TabBarView de Material.
        self._secciones = [
            self.registro.contenido,
            self.extraccion.contenido,
            self.automatizacion.contenido,
            self.exportacion.contenido,
        ]
        for i, seccion in enumerate(self._secciones):
            seccion.visible = i == 0
        self._area = ft.Column(self._secciones, expand=True)

        oscuro = self.page.theme_mode == ft.ThemeMode.DARK
        self.logo = ft.Image(
            src=self._logo_src(oscuro),
            height=58, fit=ft.BoxFit.CONTAIN,
            error_content=ft.Text("Quetzaltic Solutions", weight=ft.FontWeight.BOLD, size=20),
        )
        self._tag_disponible: str | None = None
        self.btn_actualizar = ft.IconButton(
            icon=ft.Icons.SYSTEM_UPDATE, tooltip="Buscar actualizaciones",
            on_click=self._buscar_actualizacion_manual,
        )
        self.btn_config = ft.IconButton(
            icon=ft.Icons.SETTINGS, tooltip="Configuración", on_click=self.config.abrir,
        )
        self.btn_tema = ft.IconButton(
            icon=ft.Icons.LIGHT_MODE if oscuro else ft.Icons.DARK_MODE,
            tooltip="Modo claro" if oscuro else "Modo oscuro",
            on_click=self._alternar_tema,
        )
        encabezado = ft.Row(
            [
                self.logo,
                self._construir_nav(),
                ft.Row([self.btn_actualizar, self.btn_config, self.btn_tema], tight=True),
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=12,
        )

        pie = ft.Container(
            content=ft.Text(
                "Quetzaltic Solutions - 2026",
                size=11, color=ft.Colors.ON_SURFACE_VARIANT,
                text_align=ft.TextAlign.CENTER,
            ),
            alignment=ft.Alignment(0, 0),
            padding=ft.Padding.symmetric(horizontal=6, vertical=1),
        )

        self.page.controls.clear()
        self.page.add(encabezado, self._area, pie)
        # `page.on_resize` es un slot ÚNICO; se despacha a una lista de listeners.
        for pantalla in (self.registro, self.extraccion, self.automatizacion,
                         self.exportacion, self.config):
            self.registrar_on_resize(getattr(pantalla, "_on_resize", None))
        self.page.on_resize = self._despachar_resize
        self._pintar_barra_titulo(oscuro)
        # Carga inicial de registros guardados (si la pantalla lo soporta).
        cargar = getattr(self.registro, "cargar_desde_db", None)
        if callable(cargar):
            cargar()

    # ------------------------------------------------------ navegación
    def _construir_nav(self) -> ft.Control:
        self._nav_activa = 0
        self._nav_items: list[dict] = []
        definiciones = [
            ("Registro de activos", ft.Icons.INVENTORY_2),
            ("Extracción de documentos", ft.Icons.DOCUMENT_SCANNER),
            ("Automatización SIPP", ft.Icons.SMART_TOY),
            ("Exportación / Reportes", ft.Icons.IOS_SHARE),
        ]
        controles = []
        for idx, (texto, icono) in enumerate(definiciones):
            ico = ft.Icon(icono, size=18)
            txt = ft.Text(texto, size=13, no_wrap=True)
            cont = ft.Container(
                content=ft.Row(
                    [ico, txt], spacing=8, tight=True,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                padding=ft.Padding.symmetric(horizontal=16, vertical=12),
                border_radius=8,
                on_click=lambda _e, i=idx: self._seleccionar_nav(i),
                on_hover=lambda e, i=idx: self._hover_nav(i, e.data == "true"),
                animate=ft.Animation(160, ft.AnimationCurve.EASE_OUT),
            )
            self._nav_items.append(
                {"container": cont, "icono": ico, "texto": txt, "hover": False})
            self._estilo_nav(idx)
            controles.append(cont)
        fila = ft.Row(
            controles, scroll=ft.ScrollMode.AUTO, spacing=6,
            alignment=ft.MainAxisAlignment.CENTER,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        return ft.Container(content=fila, expand=True)

    def _estilo_nav(self, idx: int) -> None:
        item = self._nav_items[idx]
        activo = idx == self._nav_activa
        resaltar = activo or item["hover"]
        color = ft.Colors.PRIMARY if resaltar else ft.Colors.ON_SURFACE_VARIANT
        item["icono"].color = color
        item["texto"].color = color
        item["texto"].weight = ft.FontWeight.BOLD if activo else ft.FontWeight.W_500
        item["container"].border = ft.Border(
            bottom=ft.BorderSide(
                3, ft.Colors.PRIMARY if resaltar else ft.Colors.TRANSPARENT))

    def _hover_nav(self, idx: int, dentro: bool) -> None:
        self._nav_items[idx]["hover"] = dentro
        self._estilo_nav(idx)
        self._nav_items[idx]["container"].update()

    def _seleccionar_nav(self, idx: int) -> None:
        if idx == self._nav_activa:
            return
        anterior = self._nav_activa
        self._nav_activa = idx
        self._estilo_nav(anterior)
        self._estilo_nav(idx)
        for i, seccion in enumerate(self._secciones):
            seccion.visible = i == idx
        self._nav_items[anterior]["container"].update()
        self._nav_items[idx]["container"].update()
        self._area.update()

    @staticmethod
    def _logo_src(oscuro: bool) -> str:
        return ("Imagenes/Quetzaltic Texto Blanco .png" if oscuro
                else "Imagenes/Quetzaltic Texto negro.png")

    def _pintar_barra_titulo(self, oscuro: bool) -> None:
        try:
            from core import win_titlebar

            fondo = _BARRA_FONDO_OSCURO if oscuro else _BARRA_FONDO_CLARO
            texto = _BARRA_TEXTO_OSCURO if oscuro else _BARRA_TEXTO_CLARO
            win_titlebar.pintar_barra(
                self.page.title, fondo, texto=texto, borde=fondo, oscuro=oscuro)
        except Exception:  # noqa: BLE001 — el color de la barra no es crítico
            pass

    def _alternar_tema(self, _e) -> None:
        oscuro = self.page.theme_mode != ft.ThemeMode.DARK
        self.page.theme_mode = ft.ThemeMode.DARK if oscuro else ft.ThemeMode.LIGHT
        self.logo.src = self._logo_src(oscuro)
        self.btn_tema.icon = ft.Icons.LIGHT_MODE if oscuro else ft.Icons.DARK_MODE
        self.btn_tema.tooltip = "Modo claro" if oscuro else "Modo oscuro"
        self._pintar_barra_titulo(oscuro)
        _guardar_tema_oscuro(oscuro)
        self.page.update()

    # ----------------------------------------------------- actualizaciones
    def marcar_actualizacion_disponible(self, tag: str) -> None:
        self._tag_disponible = tag
        self.btn_actualizar.icon_color = ft.Colors.AMBER
        self.btn_actualizar.tooltip = (
            f"Actualización disponible ({tag}) — haz clic para aplicarla")
        self.page.update()

    async def _buscar_actualizacion_manual(self, _e=None) -> None:
        self.btn_actualizar.disabled = True
        self.page.update()
        tag = await asyncio.to_thread(_comprobar_update_sync)
        self.btn_actualizar.disabled = False
        self.page.update()
        if not tag:
            self.avisar("Ya tienes la última versión instalada.", ft.Colors.GREEN_700)
            return
        self.marcar_actualizacion_disponible(tag)
        self._dialogo_actualizacion(tag)

    def _dialogo_actualizacion(self, tag: str) -> None:
        def aplicar(_e=None) -> None:
            self.page.pop_dialog()
            self.page.run_task(self._aplicar_update, tag)

        self.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Row(
                    [ft.Icon(ft.Icons.SYSTEM_UPDATE, color=ft.Colors.PRIMARY),
                     ft.Text("Actualización disponible", weight=ft.FontWeight.BOLD)],
                    spacing=10,
                ),
                content=ft.Text(
                    f"Hay una nueva versión ({tag}).\n\n"
                    "Al aplicarla, la aplicación se cerrará y se volverá a abrir "
                    "automáticamente ya actualizada. Guarda tus pendientes antes "
                    "de continuar.",
                ),
                actions=[
                    ft.TextButton("Ahora no", on_click=lambda e: self.page.pop_dialog()),
                    ft.FilledButton("Aplicar actualización", icon=ft.Icons.SYSTEM_UPDATE,
                                    on_click=aplicar),
                ],
                actions_alignment=ft.MainAxisAlignment.END,
            )
        )

    async def _aplicar_update(self, tag: str) -> None:
        from core.auto_updater import AutoUpdater

        progreso = ft.AlertDialog(
            modal=True,
            content=ft.Row(
                [
                    ft.ProgressRing(width=28, height=28, stroke_width=3),
                    ft.Text(f"Descargando e instalando la versión {tag}…\n"
                            "La aplicación se reiniciará al terminar."),
                ],
                spacing=16, tight=True,
            ),
        )
        self.page.show_dialog(progreso)
        self.page.update()
        try:
            ruta = await asyncio.to_thread(AutoUpdater().buscar_y_descargar)
        except Exception as exc:  # noqa: BLE001 — se reporta, la app sigue viva
            self.page.pop_dialog()
            self.avisar(f"No se pudo actualizar: {exc}", ft.Colors.RED_700)
            return
        if ruta is None:
            self.page.pop_dialog()
            self.avisar("Ya tienes la última versión instalada.", ft.Colors.GREEN_700)
            return
        AutoUpdater().aplicar_y_salir(ruta)


def _pantalla_cargando(page: ft.Page, titulo: str, mensaje: str) -> None:
    """Splash centrado para que el usuario NUNCA vea la ventana en blanco."""
    page.controls.clear()
    page.add(
        ft.Container(
            content=ft.Column(
                [
                    ft.ProgressRing(width=46, height=46, stroke_width=4),
                    ft.Text(titulo, size=20, weight=ft.FontWeight.BOLD,
                            text_align=ft.TextAlign.CENTER),
                    ft.Text(mensaje, size=14, text_align=ft.TextAlign.CENTER,
                            color=ft.Colors.ON_SURFACE_VARIANT),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                alignment=ft.MainAxisAlignment.CENTER,
                spacing=18,
            ),
            alignment=ft.Alignment(0, 0),
            expand=True,
        )
    )
    page.update()


async def _splash_descargando(page: ft.Page, tag: str) -> None:
    _pantalla_cargando(
        page, "Actualizando " + NOMBRE_CORTO,
        f"Descargando e instalando la versión {tag}.\n"
        "La aplicación se reiniciará automáticamente al terminar.",
    )


def _comprobar_update_sync() -> str | None:
    """Chequeo SÍNCRONO (para correr en un hilo): devuelve el tag disponible o
    None. Solo en la app empaquetada con PAT; cualquier fallo -> None."""
    if not getattr(sys, "frozen", False):
        return None
    try:
        from core import entorno
        from core.auto_updater import AutoUpdater

        if not entorno.github_pat(requerido=False):
            return None
        return AutoUpdater().hay_actualizacion()
    except Exception:  # noqa: BLE001 — el chequeo nunca debe estorbar
        return None


async def _revisar_actualizacion_2do_plano(page: ft.Page, app: "AppActivosFijos") -> None:
    """Tras cargar la app, revisa en segundo plano si hay una versión nueva; si la
    hay, prende el botón de actualización para que el usuario la aplique."""
    tag = await asyncio.to_thread(_comprobar_update_sync)
    if tag:
        app.marcar_actualizacion_disponible(tag)


_CLAVE_VENTANA = "ventana"
_CLAVE_TEMA = "tema"


def _tema_oscuro_guardado() -> bool:
    try:
        from core import preferencias
        return preferencias.cargar_valor(_CLAVE_TEMA) == "oscuro"
    except Exception:  # noqa: BLE001
        return False


def _guardar_tema_oscuro(oscuro: bool) -> None:
    try:
        from core import preferencias
        preferencias.guardar_valor(_CLAVE_TEMA, "oscuro" if oscuro else "claro")
    except Exception:  # noqa: BLE001
        pass


def _restaurar_ventana(page: ft.Page) -> None:
    from core import preferencias

    est = preferencias.cargar_valor(_CLAVE_VENTANA)
    if isinstance(est, dict) and est:
        if est.get("maximized"):
            page.window.maximized = True
        else:
            if est.get("width"):
                page.window.width = est["width"]
            if est.get("height"):
                page.window.height = est["height"]
            if est.get("left") is not None:
                page.window.left = est["left"]
            if est.get("top") is not None:
                page.window.top = est["top"]
    else:
        page.window.maximized = True  # primera vez: maximizada


def _vigilar_ventana(page: ft.Page) -> None:
    from core import preferencias

    def guardar() -> None:
        est = {
            "width": page.window.width,
            "height": page.window.height,
            "left": page.window.left,
            "top": page.window.top,
            "maximized": bool(page.window.maximized),
        }
        if est["maximized"]:
            prev = preferencias.cargar_valor(_CLAVE_VENTANA)
            if isinstance(prev, dict):
                for k in ("width", "height", "left", "top"):
                    if prev.get(k) is not None:
                        est[k] = prev[k]
        preferencias.guardar_valor(_CLAVE_VENTANA, est)

    def on_event(e) -> None:
        if e.type in (
            ft.WindowEventType.RESIZED, ft.WindowEventType.MOVED,
            ft.WindowEventType.MAXIMIZE, ft.WindowEventType.UNMAXIMIZE,
            ft.WindowEventType.RESTORE,
        ):
            guardar()

    page.window.on_event = on_event


async def main(page: ft.Page) -> None:
    page.title = TITULO_APP
    page.locale_configuration = ft.LocaleConfiguration(
        supported_locales=[ft.Locale("es", "MX"), ft.Locale("es", "ES"),
                           ft.Locale("en", "US")],
        current_locale=ft.Locale("es", "MX"),
    )
    page.window.icon = "Imagenes/icon.ico"
    page.padding = ft.Padding.only(left=18, right=18, top=18, bottom=10)
    page.theme_mode = (
        ft.ThemeMode.DARK if _tema_oscuro_guardado() else ft.ThemeMode.LIGHT)
    _barra = ft.ScrollbarTheme(
        thumb_visibility=True, track_visibility=True, thickness=12, interactive=True)
    page.theme = ft.Theme(scrollbar_theme=_barra)
    page.dark_theme = ft.Theme(scrollbar_theme=_barra)
    _pantalla_cargando(page, NOMBRE_CORTO, "Iniciando…")
    await asyncio.sleep(0.05)
    _configurar_taskbar(page)
    try:
        _restaurar_ventana(page)
        _vigilar_ventana(page)
    except Exception:  # noqa: BLE001 — la persistencia de ventana no es crítica
        pass

    _pantalla_cargando(page, NOMBRE_CORTO, "Iniciando la aplicación…")
    await asyncio.sleep(0.05)
    try:
        app = _arrancar_app(page)
    except Exception as exc:  # noqa: BLE001 — se reporta en pantalla, no se propaga
        _pantalla_error_arranque(page, exc)
        return

    page.run_task(_revisar_actualizacion_2do_plano, page, app)


def _configurar_taskbar(page: ft.Page) -> None:
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    try:
        from core import win_taskbar

        exe = os.path.join(rutas.INSTALL, NOMBRE_EXE)
        icono = os.path.join(rutas.INSTALL, "icon.ico")
        win_taskbar.configurar_identidad(
            titulo=page.title,
            relaunch_cmd=f'"{exe}"',
            icon_path=icono if os.path.exists(icono) else None,
            display=NOMBRE_CORTO,
        )
    except Exception:  # noqa: BLE001 — la identidad de la barra no es crítica
        pass


def _arrancar_app(page: ft.Page) -> "AppActivosFijos":
    """Importa (de forma perezosa) y arranca la app completa."""
    from core import db, ocr

    db.inicializar()

    if not ocr.tesseract_disponible():
        page.show_dialog(
            ft.SnackBar(
                content=ft.Text(
                    "No se encontró el motor Tesseract. Los PDF con texto se leerán igual, "
                    "pero los documentos escaneados no podrán procesarse por OCR."
                ),
                bgcolor=ft.Colors.AMBER_800,
            )
        )

    return AppActivosFijos(page)


def _pantalla_error_arranque(page: ft.Page, exc: Exception) -> None:
    """Pantalla clara cuando la app no puede iniciar (p. ej. un módulo roto)."""
    detalle = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))

    async def reintentar(_e=None) -> None:
        _pantalla_cargando(page, NOMBRE_CORTO, "Reintentando…")
        await asyncio.sleep(0.05)
        try:
            _arrancar_app(page)
        except Exception as exc2:  # noqa: BLE001
            _pantalla_error_arranque(page, exc2)

    page.controls.clear()
    page.add(
        ft.Container(
            content=ft.Column(
                [
                    ft.Icon(ft.Icons.ERROR_OUTLINE, size=48, color=ft.Colors.ERROR),
                    ft.Text("No se pudo iniciar la aplicación",
                            size=20, weight=ft.FontWeight.BOLD,
                            text_align=ft.TextAlign.CENTER),
                    ft.Text(
                        "Ocurrió un error al cargar un componente. Si hay una "
                        "actualización disponible, al reintentar se aplicará sola; "
                        "si el problema persiste, reinstala desde el instalador.",
                        size=14, text_align=ft.TextAlign.CENTER,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                    ft.FilledButton(
                        "Reintentar", icon=ft.Icons.REFRESH, on_click=reintentar),
                    ft.Divider(),
                    ft.Text("Detalle técnico:", size=12, weight=ft.FontWeight.BOLD),
                    ft.Container(
                        content=ft.Column(
                            [ft.Text(detalle, size=11, selectable=True,
                                     font_family="monospace")],
                            scroll=ft.ScrollMode.AUTO,
                        ),
                        height=200, width=640,
                        bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
                        border_radius=8, padding=10,
                    ),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                alignment=ft.MainAxisAlignment.CENTER,
                spacing=14,
                scroll=ft.ScrollMode.AUTO,
            ),
            alignment=ft.Alignment(0, 0),
            padding=24,
            expand=True,
        )
    )
    page.update()


if __name__ == "__main__":
    # Fija el CWD a la carpeta de la app para que todos los lanzadores (pin, menú
    # inicio, acceso del escritorio) se comporten igual y resuelvan bien los assets.
    try:
        os.chdir(rutas.INSTALL)
    except OSError:
        pass
    ft.run(main, assets_dir=rutas.BUNDLE)
