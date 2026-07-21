"""Configuración: credenciales del SIPP (para el RPA) y ajustes de la API.

Se abre como diálogo desde el botón de la barra superior. Captura usuario y
contraseña del SIPP, que se guardan localmente con la contraseña cifrada (DPAPI,
ver core/credenciales.py), y la URL/token de los microservicios (el token cifrado
con DPAPI, ver core/ajustes_api.py). Otras pantallas (p. ej. el RPA) leen estas
credenciales con el método credenciales().
"""

from __future__ import annotations

import flet as ft

from core import ajustes_api, credenciales
from ui.comun import GRIS, VERDE, tarjeta

CENTRO = ft.Alignment(0, 0)
_ANCHO = 480


class SeccionConfiguracion:
    """Diálogo de configuración (credenciales SIPP + API)."""

    def __init__(self, app):
        self.app = app
        self.page = app.page
        self._construir()
        self._cargar_credenciales()
        self._cargar_ajustes_api()

    # ------------------------------------------------------------ UI
    @staticmethod
    def _apartado(titulo: str, ayuda: "str | None", *controles) -> ft.Control:
        encabezado = [ft.Text(titulo, size=14, weight=ft.FontWeight.BOLD)]
        if ayuda:
            encabezado.append(ft.Icon(
                ft.Icons.HELP_OUTLINE, size=18, color=GRIS,
                tooltip=ft.Tooltip(
                    message=ayuda, wait_duration=ft.Duration(milliseconds=0))))
        return ft.Column(
            [ft.Row(encabezado, spacing=6, tight=True,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER),
             *controles],
            spacing=8, tight=True)

    def _construir(self) -> None:
        self.tf_usuario = ft.TextField(
            label="Usuario", dense=True, content_padding=10, expand=True)
        self.tf_contrasena = ft.TextField(
            label="Contraseña", password=True, can_reveal_password=True,
            dense=True, content_padding=10, expand=True)
        self.tf_api_url = ft.TextField(
            label="URL base de la API", dense=True, content_padding=10,
            hint_text="https://api.quetzaltic.dev", expand=True)
        self.tf_api_token = ft.TextField(
            label="Token de la API", password=True, can_reveal_password=True,
            dense=True, content_padding=10,
            hint_text="Déjalo vacío para conservar el actual", expand=True)
        self.txt_api_token_estado = ft.Text(size=12)
        self._actualizar_estado_token()

        cred = self._apartado(
            "Credenciales SIPP",
            "Usuario y contraseña del portal SIPP que usa el RPA. La contraseña se "
            "guarda cifrada en este equipo (DPAPI); nunca en claro ni en el repo.",
            self.tf_usuario, self.tf_contrasena)
        api = self._apartado(
            "Configuración de API",
            "URL y token de los microservicios. El token se guarda cifrado en este "
            "equipo (DPAPI); nunca en claro ni en la instalación.",
            self.tf_api_url, self.tf_api_token,
            ft.Row(
                [self.txt_api_token_estado,
                 ft.TextButton("Quitar token", on_click=self._quitar_token)],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER))

        grupo = tarjeta("Sistema", ft.Column(
            [cred, ft.Divider(), api], spacing=14, tight=True))

        contenido = ft.Column(
            [ft.Container(grupo, width=_ANCHO)],
            scroll=ft.ScrollMode.AUTO, tight=True)

        self.dialogo = ft.AlertDialog(
            modal=True,
            title=ft.Row(
                [ft.Text("Configuración", size=22, weight=ft.FontWeight.BOLD),
                 ft.IconButton(icon=ft.Icons.CLOSE, tooltip="Cerrar",
                               on_click=self._cerrar)],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER, width=_ANCHO),
            content=ft.Container(contenido, width=_ANCHO),
            actions=[
                ft.FilledButton("Aceptar", icon=ft.Icons.CHECK, on_click=self._guardar),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )

    # -------------------------------------------------------- acciones
    def abrir(self, _e=None) -> None:
        self.page.show_dialog(self.dialogo)

    def _cerrar(self, _e=None) -> None:
        self.page.pop_dialog()

    def _on_resize(self, _e=None) -> None:
        """El modal es de ancho fijo; no requiere reacomodo. Presente por
        consistencia con el registro de listeners del shell."""

    def _guardar(self, _e=None) -> None:
        """Guarda credenciales (contraseña cifrada) y ajustes de la API (URL como
        preferencia; token cifrado con DPAPI solo si se capturó uno nuevo)."""
        usuario, contrasena = self.credenciales()
        credenciales.guardar(usuario, contrasena)
        ajustes_api.guardar_base_url(self.tf_api_url.value or "")
        token = (self.tf_api_token.value or "").strip()
        if token:  # vacío -> se conserva el token guardado (no se borra al guardar)
            ajustes_api.guardar_token(token)
        self._actualizar_estado_token()
        self._cerrar()
        self.app.avisar("Configuración guardada.", VERDE)

    # -------------------------------------------------- integración (API)
    def _cargar_ajustes_api(self) -> None:
        """Precarga la URL base guardada (el token NO se muestra por seguridad)."""
        self.tf_api_url.value = ajustes_api.base_url() or ""

    def _actualizar_estado_token(self) -> None:
        if ajustes_api.hay_token_local():
            self.txt_api_token_estado.value = "Token guardado ✓"
            self.txt_api_token_estado.color = VERDE
        else:
            self.txt_api_token_estado.value = "Sin token guardado."
            self.txt_api_token_estado.color = GRIS

    def _quitar_token(self, _e=None) -> None:
        ajustes_api.borrar_token()
        self.tf_api_token.value = ""
        self._actualizar_estado_token()
        self.page.update()
        self.app.avisar("Token de la API eliminado.", VERDE)

    # --------------------------------------------------- credenciales
    def _cargar_credenciales(self) -> None:
        datos = credenciales.cargar()
        if datos is None:
            return
        usuario, contrasena = datos
        self.tf_usuario.value = usuario
        self.tf_contrasena.value = contrasena

    def credenciales(self) -> tuple[str, str]:
        """Devuelve (usuario, contraseña) tal como están capturados ahora."""
        return (self.tf_usuario.value or "").strip(), self.tf_contrasena.value or ""
