"""RPA del SIPP: base reutilizable (Playwright).

Encapsula en una sola clase (`SesionSipp`) lo COMÚN a cualquier automatización
del portal SIPP: arranque del navegador, login, selección de empresa/sucursal
(selects "chosen" de AngularJS) y navegación por menús. La idea es que cada
pantalla que necesite operar el SIPP reuse esta clase y agregue ARRIBA sus flujos
concretos (consultas, descargas, capturas), en vez de duplicar la automatización.

Localizadores: se priorizan los orientados al usuario (get_by_placeholder /
get_by_role / get_by_label / texto) con respaldos por CSS. Los selects de
empresa/sucursal son selects de AngularJS decorados con el plugin 'chosen'; se
identifican por su ng-model (id_Empresa / id_Sucursal) y se opera el <select>
nativo directamente (fuente de verdad de AngularJS).

Uso típico:

    from core import credenciales
    from core.rpa_sipp import SesionSipp

    async with SesionSipp(headless=False) as sipp:
        usuario, contrasena = credenciales.cargar()
        await sipp.login(usuario, contrasena)
        await sipp.seleccionar_empresa_sucursal("Abastecedora", "Corporativo")
        # ... aquí van los flujos concretos del módulo de activos fijos ...
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import glob
import os
import re
import sys
import threading
from datetime import datetime

from playwright.async_api import (
    Browser,
    BrowserContext,
    Locator,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from core import rutas

# Carpeta del proyecto (para guardar diagnósticos del RPA en desarrollo).
_PROYECTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ------------------------------------------------ navegador (Chromium)
def _ruta_navegadores() -> str:
    """Carpeta (escribible) donde vive Chromium en la app empaquetada."""
    return os.path.join(rutas.DATOS, "ms-playwright")


def _hay_chromium(base: str) -> bool:
    """True si ya hay un Chromium instalado en `base`."""
    return bool(glob.glob(os.path.join(base, "chromium-*", "**", "chrome.exe"), recursive=True))


def necesita_navegador() -> bool:
    """True si la app está empaquetada y aún falta descargar Chromium (primera
    vez). Permite a la interfaz avisar antes de la descarga."""
    if not getattr(sys, "frozen", False):
        return False
    return not _hay_chromium(_ruta_navegadores())


async def asegurar_navegador() -> None:
    """En la app empaquetada (sys.frozen): fija PLAYWRIGHT_BROWSERS_PATH a una
    carpeta escribible del usuario y, si Chromium no está, lo descarga (la primera
    vez, requiere internet). En desarrollo no hace nada (usa la instalación normal
    de Playwright). El driver (node) viene empaquetado (--collect-all playwright)."""
    if not getattr(sys, "frozen", False):
        return
    destino = _ruta_navegadores()
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = destino  # antes de usar Playwright
    if _hay_chromium(destino):
        return
    os.makedirs(destino, exist_ok=True)
    from playwright._impl._driver import compute_driver_executable, get_driver_env

    node, cli = compute_driver_executable()
    entorno_driver = {**os.environ, **get_driver_env()}
    entorno_driver["PLAYWRIGHT_BROWSERS_PATH"] = destino
    try:
        proc = await asyncio.create_subprocess_exec(
            node, cli, "install", "chromium", "--no-shell",
            env=entorno_driver,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        await proc.communicate()
    except Exception as exc:  # noqa: BLE001 — se reporta como ErrorSipp
        raise ErrorSipp("No se pudo descargar el navegador (Chromium): %s" % exc) from exc
    if not _hay_chromium(destino):
        raise ErrorSipp(
            "No se pudo preparar el navegador (Chromium). Revisa la conexión a "
            "internet e inténtalo de nuevo."
        )


class ErrorSipp(Exception):
    """Falla esperada del RPA del SIPP (login fallido, elemento ausente, etc.)."""


# JS que elige una opción de un <select> de AngularJS decorado con 'chosen'.
# Recibe {ngModel, texto}: localiza el <select> por su ng-model, busca la opción
# cuyo texto coincida (sin acentos ni mayúsculas; exacta, luego "empieza con",
# luego "contiene"), fija el valor y notifica el cambio a AngularJS y a 'chosen'.
_JS_ELEGIR_OPCION = r"""(args) => {
    const {ngModel, texto} = args;
    const norm = s => (s || '')
        .normalize('NFD').replace(/[̀-ͯ]/g, '')
        .replace(/\s+/g, ' ').trim().toLowerCase();
    // El portal REPITE el ng-model en paneles ocultos (agregar/editar) y muchos
    // combos son "chosen": su <select> real está OCULTO (offsetParent null), por lo
    // que no se puede filtrar por visible. Se aplica el valor a TODOS los <select>
    // que coincidan (el activo se sincroniza; los inactivos son inofensivos), y se
    // sincroniza Angular y el widget chosen en cada uno.
    const sels = Array.from(
        document.querySelectorAll('select[ng-model="' + ngModel + '"]'));
    if (!sels.length) return {ok: false, motivo: 'select-no-encontrado'};
    const objetivo = norm(texto);
    const jq = window.jQuery || window.$;
    let aplicado = false, elegido = '', disponibles = [];
    for (const sel of sels) {
        // OJO: NO excluir value '0' — hay opciones válidas con 0 (p. ej. Situación
        // "Activo Fijo" = 0). Solo se descarta el placeholder ('' o "Seleccionar").
        const opts = Array.from(sel.options).filter(
            o => o.value !== '' && norm(o.textContent) !== 'seleccionar'
                 && norm(o.textContent) !== 'seleccione');
        const opt = opts.find(o => norm(o.textContent) === objetivo)
                 || opts.find(o => norm(o.textContent).startsWith(objetivo))
                 || opts.find(o => norm(o.textContent).includes(objetivo));
        if (!opt) { disponibles = opts.map(o => o.textContent.trim()); continue; }
        sel.value = opt.value;
        // El evento 'change' nativo del <select> sincroniza el ng-model de Angular;
        // el trigger de jQuery + chosen:updated refresca el widget "chosen".
        sel.dispatchEvent(new Event('change', {bubbles: true}));
        if (jq) {
            try { jq(sel).val(opt.value).trigger('change').trigger('chosen:updated'); }
            catch (e) {}
        }
        aplicado = true; elegido = opt.textContent.trim();
    }
    return aplicado ? {ok: true, elegido}
                    : {ok: false, motivo: 'opcion-no-encontrada', disponibles};
}"""


# JS que llena las CARACTERÍSTICAS dinámicas del insumo ("Detalles Insumo").
# En el SIPP se renderizan con ng-repeat="(key, item) in camposDetalle": el rótulo
# es item.NB_CAMPODETALLE y el valor va en camposDetalle[$index]['DE_VALORCAMPODETALLE'].
# Como no hay un ng-model fijo por campo, se emparejan por el RÓTULO: se recorren
# los inputs de DE_VALORCAMPODETALLE, se lee la etiqueta de su fila y se escribe el
# valor que corresponda. Recibe {items:[{etiqueta, valor}]}.
_JS_LLENAR_CAMPOS_DETALLE = r"""(args) => {
    const {items} = args;
    const norm = s => (s || '')
        .normalize('NFD').replace(/[̀-ͯ]/g, '')
        .replace(/\s+/g, ' ').replace(/\s*:\s*$/, '').trim().toLowerCase();
    const inputs = [...document.querySelectorAll(
        "[ng-model*='DE_VALORCAMPODETALLE']")].filter(el => el.offsetParent !== null);
    const pend = items.map(it => ({et: norm(it.etiqueta), val: it.valor, ok: false}));
    for (const inp of inputs) {
        // Etiqueta de la fila: se busca el <label> del contenedor más cercano;
        // si no hay, se usa el texto del contenedor (sin el propio input).
        const cont = inp.closest('.form-group, .row, td, li, div');
        let etiqueta = '';
        if (cont) {
            const lab = cont.querySelector('label');
            etiqueta = lab ? lab.textContent : cont.textContent;
        }
        const e = norm(etiqueta);
        if (!e) continue;
        const p = pend.find(p => !p.ok && p.et && e.includes(p.et));
        if (!p) continue;
        inp.value = p.val;
        inp.dispatchEvent(new Event('input', {bubbles: true}));
        inp.dispatchEvent(new Event('change', {bubbles: true}));
        p.ok = true;
    }
    return {
        llenados: pend.filter(p => p.ok).map(p => p.et),
        faltantes: pend.filter(p => !p.ok).map(p => p.et),
        inputs_detectados: inputs.length,
    };
}"""


class SesionSipp:
    """Maneja una sesión automatizada del SIPP: navegador, login y selección
    de empresa/sucursal. Pensada para reusarse desde distintos módulos."""

    # --- URLs --- (ajusta BASE_URL al entorno que use la herramienta)
    # Ambiente de PRUEBAS (test): se opera aquí mientras se desarrolla el módulo.
    BASE_URL = "https://test.sipp.petroil.dev"
    # BASE_URL = "https://stage.sipp.petroil.dev"  # stage
    # BASE_URL = "https://dev.sipp.petroil.dev"    # desarrollo
    # BASE_URL = "https://sipp.petroil.com.mx"     # productivo
    URL_LOGIN = BASE_URL + "/login.html"
    URL_CONFIG_SESION = BASE_URL + "/index.cfm#/configuracionsession"
    # Rutas SPA del módulo de Activos Fijos (confirmadas en el DOM real).
    URL_CATALOGO_ACTIVOS = BASE_URL + "/index.cfm#/ActivosFijosNuevo"
    URL_BANDEJA_COMPRAS = BASE_URL + "/index.cfm#/BandejaCompraActivos"

    # --- Tiempos de espera (ms) ---
    TIMEOUT_NAV = 30_000        # navegación / carga de página
    TIMEOUT_ELEMENTO = 10_000   # aparición de un elemento
    TIMEOUT_LOGIN_OK = 5_000    # confirmación de inicio de sesión

    def __init__(self, headless: bool = False, slow_mo: int = 0, zoom: float = 0.8):
        self.headless = headless
        self.slow_mo = slow_mo
        # Factor de escala de la ventana (< 1 = zoom out): más contenido cabe en
        # pantalla, útil para grids largas donde los últimos registros quedaban
        # fuera del borde visible.
        self.zoom = zoom
        self._pw: Playwright | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None

    # ------------------------------------------------------ ciclo de vida
    async def iniciar(self) -> "SesionSipp":
        """Arranca Playwright, el navegador y una pestaña limpia."""
        await asegurar_navegador()
        self._pw = await async_playwright().start()
        self.browser = await self._pw.chromium.launch(
            headless=self.headless, slow_mo=self.slow_mo,
            args=["--start-maximized", f"--force-device-scale-factor={self.zoom}"],
        )
        self.context = await self.browser.new_context(no_viewport=True)
        self.page = await self.context.new_page()
        await self.page.bring_to_front()
        return self

    async def cerrar(self) -> None:
        """Cierra todo de forma segura (idempotente)."""
        if self.context is not None:
            await self.context.close()
        if self.browser is not None:
            await self.browser.close()
        if self._pw is not None:
            await self._pw.stop()
        self._pw = self.browser = self.context = self.page = None

    async def __aenter__(self) -> "SesionSipp":
        return await self.iniciar()

    async def __aexit__(self, *_exc) -> None:
        await self.cerrar()

    def _exigir_pagina(self) -> Page:
        if self.page is None:
            raise ErrorSipp("La sesión del SIPP no está iniciada (llama a iniciar()).")
        return self.page

    # ------------------------------------------------------------ login
    async def login(self, usuario: str, contrasena: str) -> None:
        """Inicia sesión en el portal. Lanza ErrorSipp si faltan credenciales o
        no se confirma el acceso al panel interno en 5 s."""
        if not usuario or not contrasena:
            raise ErrorSipp("Faltan credenciales para iniciar sesión en el SIPP.")
        page = self._exigir_pagina()
        await page.goto(self.URL_LOGIN, wait_until="domcontentloaded", timeout=self.TIMEOUT_NAV)

        campo_usuario = await self._primer_visible(
            [
                page.get_by_placeholder("Usuario", exact=True),
                page.locator("#nb_Usuario"),
                page.get_by_role("textbox", name=re.compile("usuario", re.I)),
            ],
            "campo de usuario",
        )
        await campo_usuario.fill(usuario)

        campo_contrasena = await self._primer_visible(
            [
                page.get_by_placeholder("Contraseña", exact=True),
                page.locator("input[type='password']:visible"),
                page.locator("input[type='password']").first,
            ],
            "campo de contraseña",
        )
        await campo_contrasena.fill(contrasena)

        boton = await self._primer_visible(
            [
                page.get_by_role("button", name=re.compile(r"iniciar sesi", re.I)),
                page.locator("#btnLogin"),
            ],
            "botón de iniciar sesión",
        )
        await boton.click()
        await self._verificar_login()

    async def _verificar_login(self) -> None:
        """Confirma que se entró al panel interno (redirige de login.html a
        index.cfm). Lanza ErrorSipp si no se confirma en 5 s."""
        page = self._exigir_pagina()
        try:
            await page.wait_for_url(re.compile(r"index\.cfm", re.I), timeout=self.TIMEOUT_LOGIN_OK)
            return
        except PlaywrightTimeoutError:
            pass
        señales = [
            page.get_by_text(re.compile("bienvenid", re.I)),
            page.get_by_role("link", name=re.compile("salir|cerrar sesi|logout", re.I)),
            page.get_by_role("button", name=re.compile("salir|cerrar sesi|logout", re.I)),
            page.locator("nav, .navbar, #menu, .sidebar, .main-menu").first,
        ]
        try:
            await self._primer_visible(señales, "panel interno", timeout=self.TIMEOUT_LOGIN_OK)
        except ErrorSipp as exc:
            raise ErrorSipp(
                "No se confirmó el inicio de sesión en el SIPP: no se llegó al panel "
                "interno en 5 s. Revisa las credenciales o el localizador de éxito."
            ) from exc

    # ------------------------------------------- empresa / sucursal (chosen)
    _NG_MODEL = {"Empresa": "id_Empresa", "Sucursal": "id_Sucursal"}

    async def seleccionar_empresa_sucursal(
        self, empresa: str, sucursal: str, guardar: bool = True,
    ) -> None:
        """Va a la pantalla de configuración de sesión, elige empresa y sucursal
        y, si `guardar` es True, pulsa "Guardar" para dejar la selección activa."""
        page = self._exigir_pagina()
        await self._ir_a_ruta_spa(
            self.URL_CONFIG_SESION,
            page.locator(".chosen-container").first,
            "No se cargaron los selects de empresa/sucursal (chosen) en la "
            "pantalla de configuración de sesión.",
            "config_sesion",
        )
        try:
            # esperar_opcion=True en ambas: las empresas cargan por AJAX (el select
            # arranca vacío) y las sucursales se recargan al elegir la empresa.
            await self._elegir_chosen("Empresa", empresa, esperar_opcion=True)
            # Al elegir empresa se recargan las sucursales (AJAX); dar un respiro.
            await page.wait_for_timeout(700)
            await self._elegir_chosen("Sucursal", sucursal, esperar_opcion=True)
        except ErrorSipp:
            await self._capturar_diagnostico("seleccion_empresa_sucursal")
            raise
        if guardar:
            boton_guardar = await self._primer_visible(
                [
                    page.get_by_role("button", name=re.compile(r"^\s*guardar\s*$", re.I)),
                    page.locator("button:has-text('Guardar')").first,
                ],
                "botón Guardar de la configuración de sesión",
            )
            await boton_guardar.click()
            # El guardado de la sesión es AJAX: si se navega a un módulo antes de
            # que persista, el SIPP rebota a esta misma pantalla de configuración.
            # Se espera a que la sesión quede activa (se sale de configuracionsession)
            # o, en su defecto, un margen fijo.
            try:
                await page.wait_for_function(
                    "() => !location.hash.toLowerCase().includes('configuracionsession')",
                    timeout=self.TIMEOUT_ELEMENTO)
            except PlaywrightTimeoutError:
                await page.wait_for_timeout(2500)

    async def preparar_sesion_empresa(self, empresa: str) -> tuple[str, str]:
        """Configura la sesión eligiendo `empresa` y su PRIMERA sucursal disponible.

        Útil cuando la sucursal es indistinta (p. ej. para descargar catálogos por
        empresa). El nombre de empresa se empareja por 'contiene' (las opciones del
        SIPP traen texto como 'ABASTECEDORA... - (Abastecedora )'). Devuelve
        (empresa_elegida, sucursal_elegida)."""
        page = self._exigir_pagina()
        await self._ir_a_ruta_spa(
            self.URL_CONFIG_SESION, page.locator(".chosen-container").first,
            "No se cargó la pantalla de configuración de sesión.", "config_sesion")
        # esperar_opcion=True: las empresas cargan por AJAX (al inicio el select
        # está vacío); se reintenta hasta que la opción exista.
        await self._elegir_opcion_chosen("Empresa", empresa, esperar_opcion=True)
        # Espera a que carguen las sucursales (AJAX) y toma la primera real.
        sucursal = ""
        fin = asyncio.get_event_loop().time() + self.TIMEOUT_ELEMENTO / 1000
        while asyncio.get_event_loop().time() < fin:
            ops = await page.eval_on_selector_all(
                "select[ng-model='id_Sucursal'] option",
                "e=>e.filter(o=>o.value&&o.value!=='0'&&o.value!=='')"
                ".map(o=>o.textContent.trim())")
            if ops:
                sucursal = ops[0]
                break
            await asyncio.sleep(0.3)
        if not sucursal:
            raise ErrorSipp(
                "No se cargaron sucursales para la empresa '%s'." % empresa)
        await self.seleccionar_empresa_sucursal(empresa, sucursal)
        return empresa, sucursal

    async def _elegir_chosen(
        self, etiqueta: str, texto: str, esperar_opcion: bool = False,
    ) -> None:
        """Elige `texto` en el select de `etiqueta`. Primero opera el widget
        "chosen" por la UI (abrir, escribir y CLIC en la opción), que es lo que
        realmente dispara la selección en el portal; si no hay chosen visible, cae
        al respaldo por <select> nativo (JS)."""
        try:
            await self._elegir_opcion_chosen_ui(etiqueta, texto, esperar_opcion)
            return
        except ErrorSipp:
            # Respaldo: operar el <select> nativo por su ng-model.
            await self._elegir_opcion_chosen(etiqueta, texto, esperar_opcion)

    async def _elegir_opcion_chosen_ui(
        self, etiqueta: str, texto: str, esperar_opcion: bool = False,
    ) -> None:
        """Elige `texto` operando el widget "chosen" como lo haría una persona:
        abre el desplegable del select (por su ng-model), escribe en el buscador y
        HACE CLIC en la opción que coincide. Así se disparan los eventos de chosen
        y de AngularJS (ng-change), a diferencia de fijar solo el <select> nativo."""
        page = self._exigir_pagina()
        ng_model = self._NG_MODEL[etiqueta]
        # Contenedor chosen asociado al select (hermano inmediato posterior).
        cont = page.locator(
            f'xpath=//select[@ng-model="{ng_model}"]/following-sibling::div'
            f'[contains(@class,"chosen-container")][1]').first
        try:
            await cont.wait_for(state="visible", timeout=self.TIMEOUT_ELEMENTO)
        except PlaywrightTimeoutError as exc:
            raise ErrorSipp(
                "No apareció el selector (chosen) de %s." % etiqueta) from exc
        await cont.scroll_into_view_if_needed()
        await cont.click()  # abre el desplegable
        # Escribe en el buscador del chosen para filtrar (si lo tiene).
        buscador = cont.locator("input").first
        try:
            await buscador.fill(texto, timeout=3_000)
        except Exception:  # noqa: BLE001 — algún chosen no trae buscador
            pass
        # Espera y hace CLIC en la opción que contiene el texto.
        opcion = cont.locator(
            ".chosen-results li.active-result",
            has_text=re.compile(re.escape(texto), re.I)).first
        try:
            await opcion.wait_for(
                state="visible",
                timeout=self.TIMEOUT_ELEMENTO if esperar_opcion else 2_500)
        except PlaywrightTimeoutError as exc:
            raise ErrorSipp(
                "No apareció la opción '%s' en el selector de %s." % (texto, etiqueta)
            ) from exc
        await opcion.click()

    async def _elegir_opcion_chosen(
        self, etiqueta: str, texto: str, esperar_opcion: bool = False,
    ) -> None:
        """Elige `texto` en el select de `etiqueta` ('Empresa'/'Sucursal') operando
        el <select> nativo por su ng-model. Si `esperar_opcion` es True, reintenta
        hasta que la opción aparezca (útil para la sucursal, que se carga tras
        elegir empresa)."""
        page = self._exigir_pagina()
        ng_model = self._NG_MODEL[etiqueta]
        fin = asyncio.get_event_loop().time() + self.TIMEOUT_ELEMENTO / 1000
        ultimo: dict = {}
        while True:
            ultimo = await page.evaluate(_JS_ELEGIR_OPCION, {"ngModel": ng_model, "texto": texto})
            if ultimo.get("ok"):
                return
            recuperable = esperar_opcion and ultimo.get("motivo") in (
                "opcion-no-encontrada", "select-no-encontrado",
            )
            if not recuperable or asyncio.get_event_loop().time() >= fin:
                break
            await asyncio.sleep(0.25)
        disponibles = ultimo.get("disponibles")
        detalle = ""
        if disponibles:
            muestra = ", ".join(disponibles[:8]) + ("…" if len(disponibles) > 8 else "")
            detalle = " Opciones disponibles: " + muestra
        raise ErrorSipp(
            "No se pudo elegir '%s' en el select de %s.%s" % (texto, etiqueta, detalle)
        )

    # -------------------------------------------------- navegación / menús
    async def elegir_en_menu(self, menu: str, opcion: str) -> None:
        """Abre un menú desplegable de la navbar (por su texto) y elige una de sus
        opciones (por su texto). Reutilizable para cualquier menú/opción. Usa
        localizadores por rol y EXACTOS (evita coincidencias parciales)."""
        page = self._exigir_pagina()
        toggle = await self._primer_visible(
            [
                page.get_by_role("link", name=menu, exact=True),
                page.get_by_role("button", name=menu, exact=True),
            ],
            "menú '%s'" % menu,
        )
        await toggle.click()
        opcion_loc = page.get_by_role("link", name=opcion, exact=True)
        try:
            await opcion_loc.first.wait_for(state="visible", timeout=self.TIMEOUT_ELEMENTO)
        except PlaywrightTimeoutError as exc:
            raise ErrorSipp(
                "No apareció la opción '%s' en el menú '%s'." % (opcion, menu)
            ) from exc
        await opcion_loc.first.click()

    async def _ir_a_ruta_spa(
        self, url: str, ancla: Locator, mensaje_error: str, etiqueta_diag: str,
    ) -> None:
        """Navega a una ruta de la SPA (index.cfm#/...) y espera a que aparezca un
        elemento ancla que confirme que la pantalla cargó. Si no aparece, guarda un
        diagnóstico (captura + HTML) y lanza ErrorSipp."""
        page = self._exigir_pagina()
        await page.goto(url, wait_until="domcontentloaded", timeout=self.TIMEOUT_NAV)
        try:
            await ancla.wait_for(state="visible", timeout=self.TIMEOUT_ELEMENTO)
            return
        except PlaywrightTimeoutError:
            pass
        # Si ya se estaba en index.cfm, cambiar solo el hash puede NO disparar la
        # transición de ui-router; un reload fuerza a la SPA a montar la ruta.
        try:
            await page.reload(wait_until="domcontentloaded", timeout=self.TIMEOUT_NAV)
            await ancla.wait_for(state="visible", timeout=self.TIMEOUT_ELEMENTO)
        except PlaywrightTimeoutError as exc:
            await self._capturar_diagnostico(etiqueta_diag)
            raise ErrorSipp(mensaje_error) from exc

    # ------------------------------------------------ llenado de formularios
    async def set_combo(self, ng_model: str, texto: str, esperar: bool = False) -> None:
        """Elige `texto` en un <select> de AngularJS (con o sin 'chosen') por su
        ng-model. Si `esperar`, reintenta mientras la opción aún no exista (útil
        para combos que se cargan por AJAX en cascada)."""
        if not texto:
            return
        page = self._exigir_pagina()
        fin = asyncio.get_event_loop().time() + self.TIMEOUT_ELEMENTO / 1000
        ultimo: dict = {}
        while True:
            ultimo = await page.evaluate(
                _JS_ELEGIR_OPCION, {"ngModel": ng_model, "texto": texto})
            if ultimo.get("ok"):
                return
            if not esperar or asyncio.get_event_loop().time() >= fin:
                break
            await asyncio.sleep(0.25)
        disponibles = ultimo.get("disponibles")
        detalle = ""
        if disponibles:
            detalle = (" Opciones: " + ", ".join(disponibles[:8])
                       + ("…" if len(disponibles) > 8 else ""))
        raise ErrorSipp(
            "No se pudo elegir '%s' en el combo '%s'.%s" % (texto, ng_model, detalle))

    async def set_input(self, ng_model: str, valor: str) -> None:
        """Escribe `valor` en un input por su ng-model. Se filtra por ':visible'
        porque el portal repite ng-models en paneles ocultos (ng-hide).

        Si el campo NO está presente/visible para este tipo de activo, se omite de
        inmediato (chequeo corto) en vez de esperar el timeout completo: así el RPA
        avanza «conforme encuentra los campos» y no se atora en los no aplicables."""
        page = self._exigir_pagina()
        campo = page.locator(f'[ng-model="{ng_model}"]:visible').first
        try:
            await campo.wait_for(state="visible", timeout=1_500)
        except PlaywrightTimeoutError:
            return  # campo ausente/oculto para este tipo: se omite y se sigue
        try:
            await campo.fill(valor, timeout=2_500)
        except Exception:  # noqa: BLE001 — respaldo: fijar por JS y avisar a Angular
            try:
                await campo.evaluate(
                    "(el, v) => { el.value = v;"
                    " el.dispatchEvent(new Event('input', {bubbles:true}));"
                    " el.dispatchEvent(new Event('change', {bubbles:true})); }",
                    valor, timeout=2_500)
            except Exception:  # noqa: BLE001 — no editable (deshabilitado): se omite
                pass

    async def set_fecha(self, ng_model: str, valor: str) -> None:
        """Escribe una fecha (DD/MM/AAAA) en un input con máscara. Se usa `fill`,
        que enfoca SIN clic real: así no se abre el calendario y Angular sí
        registra el valor (dispara 'input')."""
        page = self._exigir_pagina()
        campo = page.locator(f'[ng-model="{ng_model}"]:visible').first
        await campo.fill(valor)

    async def llenar_campos_detalle(self, detalles: dict) -> dict:
        """Llena las CARACTERÍSTICAS del insumo ('Detalles Insumo'), que en el SIPP
        son dinámicas (camposDetalle) y se emparejan por su rótulo. `detalles` es
        {etiqueta -> valor}. Devuelve {llenados, faltantes, inputs_detectados}."""
        items = [{"etiqueta": k, "valor": v} for k, v in (detalles or {}).items() if v]
        if not items:
            return {"llenados": [], "faltantes": [], "inputs_detectados": 0}
        page = self._exigir_pagina()
        return await page.evaluate(_JS_LLENAR_CAMPOS_DETALLE, {"items": items})

    # ------------------------------------------------ módulo de Activos Fijos
    async def ir_a_catalogo_activos(self) -> None:
        """Navega al catálogo de Activos Fijos (#/ActivosFijosNuevo) y espera a que
        cargue el filtro del listado. La SPA + su grid tardan en montar, así que se
        da un margen mayor que el de un elemento normal."""
        page = self._exigir_pagina()
        ancla = page.locator("[ng-model='js_filtroListado.de_SerieActivo']").first
        await page.goto(self.URL_CATALOGO_ACTIVOS, wait_until="domcontentloaded",
                        timeout=self.TIMEOUT_NAV)
        try:
            await ancla.wait_for(state="visible", timeout=self.TIMEOUT_NAV)
            return
        except PlaywrightTimeoutError:
            pass
        # Un reload fuerza a la SPA a montar la ruta si el cambio de hash no la
        # disparó (o si venía rebotada de la config de sesión).
        try:
            await page.reload(wait_until="domcontentloaded", timeout=self.TIMEOUT_NAV)
            await ancla.wait_for(state="visible", timeout=self.TIMEOUT_NAV)
        except PlaywrightTimeoutError as exc:
            await self._capturar_diagnostico("catalogo_activos")
            raise ErrorSipp(
                "No se cargó el catálogo de Activos Fijos (no apareció el filtro "
                "de No. de serie del listado).") from exc

    async def buscar_en_listado(self, valor: str, por_etiqueta: bool = False) -> int:
        """Filtra el listado del catálogo por No. de serie o por ETIQUETA (número
        de inventario) y devuelve cuántas filas resultaron (0 = no está dado de
        alta). En los inventarios reales la mayoría de los activos no tiene serie,
        así que la etiqueta es el identificador habitual."""
        campo = ("js_filtroListado.de_Etiqueta" if por_etiqueta
                 else "js_filtroListado.de_SerieActivo")
        page = self._exigir_pagina()
        await self.ir_a_catalogo_activos()
        await self.set_input(campo, valor)
        boton = await self._primer_visible(
            [
                page.locator("[ng-click*=\"listarDatosGrid('listadoActivosFijos')\"]"),
                page.locator("button.btn-buscar25p"),
            ],
            "botón de buscar del listado de activos")
        await self._click_seguro(boton)
        await page.wait_for_timeout(2_500)  # la grid recarga por AJAX
        return await self._contar_filas_grid()

    async def buscar_en_listado(self, etiqueta: str = "", serie: str = "") -> int:
        """Filtra el listado del catálogo por ETIQUETA (ancla principal) o, si no
        hay, por No. de serie; devuelve cuántas filas resultaron (0 = el activo NO
        está dado de alta). La etiqueta es más confiable: casi siempre existe,
        mientras que muchos activos no traen serie."""
        page = self._exigir_pagina()
        await self.ir_a_catalogo_activos()
        etiqueta = (etiqueta or "").strip()
        serie = (serie or "").strip()
        if etiqueta:
            await self.set_input("js_filtroListado.de_Etiqueta", etiqueta)
        elif serie:
            await self.set_input("js_filtroListado.de_SerieActivo", serie)
        else:
            return 0
        boton = await self._primer_visible(
            [
                page.locator("[ng-click*=\"listarDatosGrid('listadoActivosFijos')\"]"),
                page.locator("button.btn-buscar25p"),
            ],
            "botón de buscar del listado de activos")
        await self._click_seguro(boton)
        await page.wait_for_timeout(2_500)  # la grid recarga por AJAX
        return await self._contar_filas_grid()

    async def _contar_filas_grid(self) -> int:
        """Cuántos activos trajo el listado. El grid NO renderiza `.ngRow` (los
        datos viven en el array `arr_gridActivosFijos` del scope de Angular, que se
        llena por AJAX); contar el DOM daba siempre 0. Se lee el array del scope,
        con `.ngRow` como respaldo."""
        page = self._exigir_pagina()
        try:
            return await page.evaluate(r"""() => {
              const el = document.querySelector("[ng-model='js_filtroListado.de_SerieActivo']");
              const sc = el && window.angular ? angular.element(el).scope() : null;
              if (sc && Array.isArray(sc.arr_gridActivosFijos))
                  return sc.arr_gridActivosFijos.length;
              return document.querySelectorAll('.ngRow').length;
            }""")
        except Exception:  # noqa: BLE001
            return 0

    async def seleccionar_insumo(self, id_insumo) -> None:
        """Elige el insumo por su ID exacto (Cve Insumo) en el modal 'Buscar
        Insumo': abre el modal, teclea el id, busca y hace clic en la fila
        resultante. Es exacto (por id), a diferencia de buscar por nombre."""
        page = self._exigir_pagina()
        abrir = await self._primer_visible(
            [page.locator("[ng-click*=\"abrirModal('insumos')\"]"),
             page.locator("[ng-click*='insumos']")],
            "botón para abrir el buscador de insumos")
        await self._click_seguro(abrir)
        try:
            await page.locator("[ng-model='filtrosInsumos.id_Insumo']").first.wait_for(
                state="visible", timeout=self.TIMEOUT_ELEMENTO)
        except PlaywrightTimeoutError as exc:
            await self._capturar_diagnostico("modal_insumos")
            raise ErrorSipp("No se abrió el modal 'Buscar Insumo'.") from exc
        await self.set_input("filtrosInsumos.id_Insumo", str(id_insumo))
        await self._click_seguro(page.locator("[ng-click='listarInsumos()']").first)
        await page.wait_for_timeout(2_500)  # la grid del modal recarga por AJAX
        # Cada fila del resultado trae un botón 'agregarInsumo(row)' que lo elige y
        # cierra el modal (a veces como 'grid.appScope.agregarInsumo(row)', por eso
        # match por contiene). Buscando por id exacto, la primera es la correcta.
        boton = page.locator("[ng-click*='agregarInsumo(row)']").first
        try:
            await boton.wait_for(state="visible", timeout=self.TIMEOUT_ELEMENTO)
        except PlaywrightTimeoutError as exc:
            await self._capturar_diagnostico("insumo_no_encontrado")
            raise ErrorSipp(
                f"No apareció el insumo con id {id_insumo} en el catálogo del "
                "SIPP. ¿El catálogo local está desactualizado?") from exc
        await self._click_seguro(boton)
        await page.wait_for_timeout(800)

    async def seleccionar_empleado(self, id_empleado, nombre: str = "") -> None:
        """Elige el empleado de resguardo en el modal 'Buscar Empleado' (Asignación
        del Activo): abre el modal y busca por ID si se tiene (exacto) o, si no, por
        NOMBRE; luego pulsa el botón de la fila. Así el modal se abre y busca al
        empleado aunque no se haya resuelto su id."""
        id_empleado = str(id_empleado or "").strip()
        nombre = (nombre or "").strip()
        if not id_empleado and not nombre:
            return  # nada que buscar
        page = self._exigir_pagina()
        abrir = await self._primer_visible(
            [page.locator("[ng-click*=\"abrirModal('empleados', 2\"]"),
             page.locator("[ng-click*=\"abrirModal('empleados'\"]")],
            "botón para abrir el buscador de empleados")
        await self._click_seguro(abrir)
        try:
            await page.locator(
                "[ng-model='js_filtroModalEmpleado.id_Empleado']").first.wait_for(
                state="visible", timeout=self.TIMEOUT_ELEMENTO)
        except PlaywrightTimeoutError as exc:
            await self._capturar_diagnostico("modal_empleados")
            raise ErrorSipp("No se abrió el modal 'Buscar Empleado'.") from exc
        # Buscar por id (exacto) si se tiene; si no, por nombre.
        if id_empleado:
            await self.set_input("js_filtroModalEmpleado.id_Empleado", id_empleado)
            criterio = f"id {id_empleado}"
        else:
            await self.set_input("js_filtroModalEmpleado.nb_NombreEmpleado", nombre)
            criterio = f"nombre «{nombre}»"
        await self._click_seguro(
            page.locator("[ng-click=\"listarDatosGrid('listadoEmpleados')\"]").first)
        await page.wait_for_timeout(2_500)
        # El botón de la fila puede venir como 'grid.appScope.agregarEmpleado(row)'.
        boton = page.locator("[ng-click*='agregarEmpleado(row)']").first
        try:
            await boton.wait_for(state="visible", timeout=self.TIMEOUT_ELEMENTO)
        except PlaywrightTimeoutError as exc:
            await self._capturar_diagnostico("empleado_no_encontrado")
            raise ErrorSipp(
                f"No apareció el empleado ({criterio}) en el catálogo del SIPP.") from exc
        await self._click_seguro(boton)
        await page.wait_for_timeout(800)

    async def alta_activo(self, tipo_nombre: str, campos: list,
                          detalles: "dict | None" = None,
                          insumo_id=None, empleado_id=None,
                          serie: str = "", etiqueta_actual: str = "",
                          empresa: str = "", sucursal: str = "",
                          empleado_nombre: str = "") -> None:
        """Da de alta un activo en el SIPP.

        Args:
            tipo_nombre: nombre del tipo de activo (se elige en el combo).
            campos: lista de (ng_model, valor, control) donde control es
                'text' | 'number' | 'date' | 'select'.
            detalles: características del insumo {etiqueta -> valor} (camposDetalle).
            insumo_id: Cve Insumo del SIPP; se selecciona por el modal 'Buscar
                Insumo' (el nombre del insumo es de solo lectura, se elige así).
            serie/etiqueta_actual: para buscar la factura en la bandeja de compras
                (solo si la serie es válida: existe y != etiqueta). empresa/sucursal:
                las del activo, para los datos de compra.

        Abre el formulario, elige el tipo, selecciona el insumo por id (lo que
        dispara la carga de las características), llena todo y pulsa Guardar.
        """
        page = self._exigir_pagina()
        await self.ir_a_catalogo_activos()
        boton_agregar = await self._primer_visible(
            [
                page.locator("[ng-click*='confAgregarActivo']"),
                page.get_by_role("button", name=re.compile(r"agregar", re.I)),
            ],
            "botón para agregar un activo")
        await self._click_seguro(boton_agregar)

        # El tipo va primero: de él dependen las características del insumo.
        await self.set_combo("filtrosAgregar.id_TipoActivo", tipo_nombre, esperar=True)
        await page.wait_for_timeout(800)

        # El insumo se elige por ID en el modal (dispara la carga de camposDetalle).
        if insumo_id:
            await self.seleccionar_insumo(insumo_id)
            await page.wait_for_timeout(800)

        # El empleado de resguardo se elige en su modal: por ID si se tiene, o por
        # NOMBRE. Así el modal se abre y busca aunque no se haya resuelto el id.
        if empleado_id or empleado_nombre:
            await self.seleccionar_empleado(empleado_id, empleado_nombre)
            await page.wait_for_timeout(500)

        for ng_model, valor, control in campos:
            if not valor or not ng_model:
                continue
            # Un campo que no se pueda aplicar (ausente/oculto/no editable para ese
            # tipo, p. ej. la Situación en algunos tipos) NO debe abortar el alta: se
            # omite y se sigue con los demás.
            try:
                if control == "select":
                    # El CENTRO de costo depende del GRUPO (cascada AJAX): su opción
                    # solo existe tras elegir el grupo, así que se espera a que aparezca.
                    es_centro = "CentroCosto" in ng_model and "Grupo" not in ng_model
                    try:
                        await self.set_combo(ng_model, valor, esperar=es_centro)
                    except ErrorSipp:
                        # Algunos "select" del portal son en realidad campos de texto
                        # con búsqueda; se intenta escribirlos.
                        await self.set_input(ng_model, valor)
                    if "GrupoCentroCosto" in ng_model:
                        # Dar tiempo a que la cascada cargue los centros del grupo
                        # antes de intentar elegir el centro.
                        await page.wait_for_timeout(1200)
                elif control == "date":
                    await self.set_fecha(ng_model, valor)
                else:
                    await self.set_input(ng_model, valor)
            except Exception:  # noqa: BLE001 — campo no aplicable: se omite, no aborta
                continue

        if detalles:
            await self.llenar_campos_detalle(detalles)

        # Factura + precio desde la bandeja de compras (best-effort): nunca aborta
        # el alta; solo actúa si la serie es válida y existe la entrada de compra.
        try:
            await self._adjuntar_compra(serie, etiqueta_actual, empresa, sucursal)
        except Exception:  # noqa: BLE001 — no crítico: se omite sin tumbar el alta
            pass

        # La ETIQUETA/folio es un consecutivo GLOBAL del SIPP (getEtiqueta ignora
        # empresa y tipo): devuelve el "siguiente" disponible y avanza al guardar
        # cada activo. Se genera con el botón del portal ANTES de guardar; el código
        # generado se devuelve para registrarlo en la herramienta.
        etiqueta = await self.generar_etiqueta()

        # El No. de serie es OBLIGATORIO en el SIPP. Si el activo NO trae serie, se
        # usa la ETIQUETA como número de serie (la generada por el SIPP; como
        # respaldo, la del levantamiento). Se hace tras generar la etiqueta.
        serie_final = ((serie or "").strip() or (etiqueta or "").strip()
                       or (etiqueta_actual or "").strip())
        if serie_final:
            try:
                await self.set_input("filtrosAgregar.nu_Serie", serie_final)
                await page.wait_for_timeout(200)
            except Exception:  # noqa: BLE001 — no crítico
                pass

        guardar = await self._primer_visible(
            [
                page.locator("[ng-click*='guardarActivoFijo()']"),
                page.get_by_role("button", name=re.compile(r"^\s*guardar\s*$", re.I)),
            ],
            "botón Guardar del alta de activo")
        await self._click_seguro(guardar)
        await self.confirmar_aviso_si_hay(3_000)
        return etiqueta

    async def _activar_datos_compra(self) -> None:
        """Marca el checkbox 'Datos de compra' (filtrosAgregar.sn_DatosCompra) para
        habilitar Costo/Factura/Archivo (están ng-disabled hasta que se activa)."""
        page = self._exigir_pagina()
        await page.evaluate(
            "() => { const el = document.querySelector("
            "\"[ng-model='filtrosAgregar.sn_DatosCompra']\");"
            " if (el && !el.checked) { const s = angular.element(el).scope();"
            " s.$apply(() => { s.filtrosAgregar.sn_DatosCompra = 1;"
            " if (typeof s.resetCamposCompra === 'function') s.resetCamposCompra(); }); } }")
        await page.wait_for_timeout(600)

    async def _adjuntar_compra(self, serie: str, etiqueta_actual: str,
                               empresa: str, sucursal: str) -> None:
        """Busca la entrada de compra por serie y, si la halla, activa 'Datos de
        compra', pone el precio (CFDI ValorUnitario) en Costo, el folio en Factura y
        adjunta el PDF. Solo para serie válida (existe y != etiqueta)."""
        from core import compras_sipp as compras
        from core.empresas import ID_POR_EMPRESA

        if not compras.serie_valida(serie, etiqueta_actual):
            return
        id_empresa = ID_POR_EMPRESA.get((empresa or "").strip())
        entrada = await compras.buscar_entrada_por_serie(self, serie, id_empresa)
        if entrada is None:
            return

        info = await compras.datos_factura(self, entrada)  # {precio, folio}
        page = self._exigir_pagina()
        # Habilita la sección; de paso, ya visibles, empresa/sucursal de compra no
        # cuelgan (antes colgaban por estar ocultas). Se ponen las del propio activo.
        await self._activar_datos_compra()
        for ng_model, valor, esperar in (
                ("filtrosAgregar.id_EmpresaAgregar", empresa, True),
                ("filtrosAgregar.id_SucursalAgregar", sucursal, True)):
            if valor:
                try:
                    await self.set_combo(ng_model, valor, esperar=esperar)
                    await page.wait_for_timeout(400)
                except Exception:  # noqa: BLE001 — no aplica: se omite
                    continue
        if info.get("precio") is not None:
            try:
                await self.set_input("filtrosAgregar.im_Costo", f"{info['precio']:.2f}")
            except Exception:  # noqa: BLE001
                pass
        if info.get("folio"):
            try:
                await self.set_input("filtrosAgregar.nb_Factura", info["folio"])
            except Exception:  # noqa: BLE001
                pass
        if entrada.tiene_factura:
            try:
                carpeta = os.path.join(rutas.DATOS, "facturas_alta")
                ruta = await compras.descargar_factura(self, entrada, carpeta)
                if ruta:
                    await page.set_input_files("#ar_ArchivoSoporteFactura", str(ruta))
                    await page.wait_for_timeout(600)  # ng-change subirFactura(this)
            except Exception:  # noqa: BLE001
                pass

    async def _leer_etiqueta(self) -> str:
        page = self._exigir_pagina()
        val = await page.evaluate(
            "() => { const el = document.querySelector("
            "\"[ng-model='filtrosAgregar.nu_Etiqueta']\");"
            " return el ? (el.value || '') : ''; }")
        return (val or "").strip()

    async def generar_etiqueta(self) -> str:
        """Pulsa 'Generar Etiqueta' (generarEtiqueta()) y devuelve el código NUEVO
        que el SIPP asigna en filtrosAgregar.nu_Etiqueta (read-only).

        Se captura el valor previo y se espera uno NO vacío y DISTINTO, para no
        devolver una etiqueta rancia (evita que se repita entre activos)."""
        page = self._exigir_pagina()
        antes = await self._leer_etiqueta()
        try:
            boton = await self._primer_visible(
                [page.locator("[ng-click*='generarEtiqueta']"),
                 page.get_by_role("button", name=re.compile(r"generar\s+etiqueta", re.I))],
                "botón Generar Etiqueta")
            await self._click_seguro(boton)
        except ErrorSipp:
            return ""   # sin botón (algún tipo no la usa): no aborta el alta
        # La etiqueta se asigna por AJAX; se espera a que aparezca una NUEVA.
        fin = asyncio.get_event_loop().time() + self.TIMEOUT_ELEMENTO / 1000
        while asyncio.get_event_loop().time() < fin:
            etiqueta = await self._leer_etiqueta()
            if etiqueta and etiqueta != antes:
                return etiqueta
            await page.wait_for_timeout(300)
        # Si no cambió (el botón no regeneró), se devuelve lo que haya (mejor que nada).
        return await self._leer_etiqueta()

    async def modificar_activo(self, etiqueta: str, serie: str, campos: list,
                               detalles: "dict | None" = None) -> list:
        """Busca un activo por ETIQUETA (o serie si no hay), abre su edición, aplica
        los campos y guarda. Devuelve la lista de campos que NO se pudieron aplicar
        (el formulario de edición no expone exactamente los mismos que el alta, así
        que un campo ausente no aborta el resto).

        `campos`: [(ng_model, valor, control)] ya en su forma de EDICIÓN
        (filtrosEditar.* / FH_*_EDITAR)."""
        page = self._exigir_pagina()
        filas = await self.buscar_en_listado(etiqueta=etiqueta, serie=serie)
        if filas == 0:
            ident = (etiqueta or "").strip() or (serie or "").strip()
            raise ErrorSipp(
                f"No se encontró en el listado un activo con etiqueta/serie '{ident}'.")

        # Abrir la edición de la fila encontrada. El portal usa un botón/ícono de
        # acción por fila; se prueban varios localizadores y, si ninguno aparece,
        # se guarda diagnóstico para afinar el selector con el DOM real.
        abrir = await self._primer_visible(
            [
                page.locator("[ng-click*='confEditarActivo']"),
                page.locator("[ng-click*='editarActivo']"),
                page.locator("[ng-click*='Editar']"),
                page.locator(".ngRow [title*='ditar']"),
            ],
            "acción de editar del listado de activos")
        await self._click_seguro(abrir)

        try:
            await page.locator("[ng-model='filtrosEditar.nu_Serie']").first.wait_for(
                state="visible", timeout=self.TIMEOUT_ELEMENTO)
        except PlaywrightTimeoutError as exc:
            await self._capturar_diagnostico("abrir_edicion_activo")
            raise ErrorSipp(
                "No se abrió el formulario de edición del activo.") from exc

        no_aplicados = []
        for ng_model, valor, control in campos:
            if not valor or not ng_model:
                continue
            try:
                if control == "select":
                    # El centro de costo depende del grupo (cascada AJAX): se espera
                    # a que su opción cargue tras elegir el grupo.
                    es_centro = "CentroCosto" in ng_model and "Grupo" not in ng_model
                    try:
                        await self.set_combo(ng_model, valor, esperar=es_centro)
                    except ErrorSipp:
                        await self.set_input(ng_model, valor)
                    if "GrupoCentroCosto" in ng_model:
                        await page.wait_for_timeout(1200)
                elif control == "date":
                    await self.set_fecha(ng_model, valor)
                else:
                    await self.set_input(ng_model, valor)
            except Exception:  # noqa: BLE001 — campo ausente en edición: se reporta
                no_aplicados.append(ng_model)

        if detalles:
            await self.llenar_campos_detalle(detalles)

        guardar = await self._primer_visible(
            [
                page.locator("[ng-click*='guardarActivoFijoEditar()']"),
                page.get_by_role("button", name=re.compile(r"^\s*guardar\s*$", re.I)),
            ],
            "botón Guardar de la edición del activo")
        await self._click_seguro(guardar)
        await self.confirmar_aviso_si_hay(3_000)
        return no_aplicados

    # --------------------------------------------------------- utilidades
    async def _click_seguro(self, locator: Locator) -> None:
        """Clic robusto: normal y, si algo lo intercepta (overlay/flotante), por DOM."""
        try:
            await locator.click(timeout=self.TIMEOUT_ELEMENTO)
        except Exception:  # noqa: BLE001 — respaldo por JS
            await locator.evaluate("el => el.click()")

    async def confirmar_aviso_si_hay(self, timeout: int = 2_000) -> bool:
        """Si aparece un aviso con botón 'Aceptar', lo pulsa. Best-effort."""
        page = self._exigir_pagina()
        aceptar = page.get_by_role("button", name=re.compile(r"^\s*aceptar\s*$", re.I))
        try:
            await aceptar.first.wait_for(state="visible", timeout=timeout)
        except PlaywrightTimeoutError:
            return False
        try:
            await self._click_seguro(aceptar.first)
            return True
        except Exception:  # noqa: BLE001
            return False

    async def _primer_visible(
        self, candidatos: list[Locator], descripcion: str, timeout: int | None = None,
    ) -> Locator:
        """Devuelve el primer locator de `candidatos` que esté visible dentro del
        timeout. Lanza ErrorSipp si ninguno aparece."""
        page = self._exigir_pagina()
        limite = (timeout or self.TIMEOUT_ELEMENTO) / 1000
        fin = asyncio.get_event_loop().time() + limite
        while asyncio.get_event_loop().time() < fin:
            for loc in candidatos:
                try:
                    if await loc.first.is_visible():
                        return loc.first
                except Exception:  # noqa: BLE001 — candidato inexistente; se prueba el siguiente
                    continue
            await page.wait_for_timeout(150)
        raise ErrorSipp("No se encontró el %s en la pantalla." % descripcion)

    async def _capturar_diagnostico(self, etiqueta: str) -> None:
        """Guarda una captura + el HTML de la página en '_diagnostico_rpa' para
        depurar cuando un localizador falla contra el DOM real. Best-effort."""
        try:
            page = self._exigir_pagina()
            carpeta = os.path.join(_PROYECTO, "_diagnostico_rpa")
            os.makedirs(carpeta, exist_ok=True)
            sello = datetime.now().strftime("%Y%m%d_%H%M%S")
            base = os.path.join(carpeta, f"{etiqueta}_{sello}")
            await page.screenshot(path=base + ".png", full_page=True)
            html = await page.content()
            with open(base + ".html", "w", encoding="utf-8") as fh:
                fh.write(html)
        except Exception:  # noqa: BLE001 — el diagnóstico nunca debe tumbar el flujo
            pass


# =========================================================================
# Infraestructura para correr el RPA desde la interfaz (Flet) sin congelarla
# =========================================================================

class BucleRpa:
    """Bucle de asyncio en un hilo dedicado para correr el RPA.

    Sirve para dos cosas al integrarlo con una GUI (Flet):
      - No congelar la interfaz: el navegador se opera en otro hilo.
      - En Windows, Playwright necesita un ProactorEventLoop para lanzar el
        navegador (subprocesos); `new_event_loop()` lo provee por defecto.

    Todas las corrutinas enviadas corren en el MISMO bucle/hilo, requisito de
    Playwright (sus objetos quedan atados al loop donde se crearon).
    """

    def __init__(self):
        self._loop = asyncio.new_event_loop()
        self._hilo = threading.Thread(target=self._run, name="rpa-loop", daemon=True)
        self._hilo.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    @property
    def loop(self) -> "asyncio.AbstractEventLoop":
        """El bucle del hilo del RPA (lo necesita ControlRpa)."""
        return self._loop

    def enviar(self, coro) -> "concurrent.futures.Future":
        """Programa una corrutina en el bucle y devuelve un Future. Desde un
        manejador async de Flet: `await asyncio.wrap_future(bucle.enviar(coro))`."""
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def cerrar(self) -> None:
        """Detiene el bucle (el hilo es daemon, así que muere con la app)."""
        self._loop.call_soon_threadsafe(self._loop.stop)


class RpaDetenido(Exception):
    """El flujo del RPA se abortó a petición del usuario (Detener).

    No es un error: el llamador lo trata como una parada limpia (sin diálogo de
    error)."""


class ControlRpa:
    """Control cooperativo de pausa / reanudación / detención del flujo del RPA.

    Se construye desde el hilo de la UI pasando el `loop` del BucleRpa. Como una
    asyncio.Event no es segura de modificar entre hilos, los cambios de estado se
    agendan en ESE bucle con call_soon_threadsafe. El flujo del RPA llama a
    `await punto_control()` en puntos seguros (entre iteraciones): ahí se queda
    en pausa o aborta lanzando RpaDetenido.
    """

    def __init__(self, loop: "asyncio.AbstractEventLoop"):
        self._loop = loop
        self._reanudar = asyncio.Event()
        self._reanudar.set()  # arranca corriendo (no pausado)
        self._detenido = False

    @property
    def detenido(self) -> bool:
        return self._detenido

    def pausar(self) -> None:
        self._loop.call_soon_threadsafe(self._reanudar.clear)

    def reanudar(self) -> None:
        self._loop.call_soon_threadsafe(self._reanudar.set)

    def detener(self) -> None:
        self._detenido = True
        # Despierta si estaba en pausa, para que llegue al punto de control y aborte.
        self._loop.call_soon_threadsafe(self._reanudar.set)

    async def punto_control(self) -> None:
        """Punto seguro para pausar/abortar; se llama ENTRE operaciones del flujo."""
        if self._detenido:
            raise RpaDetenido()
        await self._reanudar.wait()
        if self._detenido:
            raise RpaDetenido()
