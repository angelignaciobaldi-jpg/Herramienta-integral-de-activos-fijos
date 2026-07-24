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
    const sel = document.querySelector('select[ng-model="' + ngModel + '"]');
    if (!sel) return {ok: false, motivo: 'select-no-encontrado'};
    const objetivo = norm(texto);
    const opts = Array.from(sel.options).filter(o => o.value !== '' && o.value !== '0');
    let opt = opts.find(o => norm(o.textContent) === objetivo)
           || opts.find(o => norm(o.textContent).startsWith(objetivo))
           || opts.find(o => norm(o.textContent).includes(objetivo));
    if (!opt) return {ok: false, motivo: 'opcion-no-encontrada',
                      disponibles: opts.map(o => o.textContent.trim())};
    sel.value = opt.value;
    const jq = window.jQuery || window.$;
    if (jq) { try { jq(sel).val(opt.value).trigger('change').trigger('chosen:updated'); } catch (e) {} }
    sel.dispatchEvent(new Event('change', {bubbles: true}));
    return {ok: true, elegido: opt.textContent.trim()};
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
    # Ambiente de PRUEBAS (stage): se opera aquí mientras se desarrolla el módulo.
    BASE_URL = "https://stage.sipp.petroil.dev"
    # BASE_URL = "https://dev.sipp.petroil.dev"   # desarrollo
    # BASE_URL = "https://sipp.petroil.com.mx"    # productivo
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
            await self._elegir_opcion_chosen("Empresa", empresa)
            # Al cambiar la empresa, el portal recarga las sucursales por AJAX; con
            # esperar_opcion=True se reintenta hasta que la sucursal exista.
            await self._elegir_opcion_chosen("Sucursal", sucursal, esperar_opcion=True)
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
        porque el portal repite ng-models en paneles ocultos (ng-hide)."""
        page = self._exigir_pagina()
        campo = page.locator(f'[ng-model="{ng_model}"]:visible').first
        try:
            await campo.fill(valor, timeout=3_000)
        except Exception:  # noqa: BLE001 — respaldo: fijar por JS y avisar a Angular
            await campo.evaluate(
                "(el, v) => { el.value = v;"
                " el.dispatchEvent(new Event('input', {bubbles:true}));"
                " el.dispatchEvent(new Event('change', {bubbles:true})); }",
                valor)

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
        cargue el filtro del listado."""
        page = self._exigir_pagina()
        await self._ir_a_ruta_spa(
            self.URL_CATALOGO_ACTIVOS,
            page.locator("[ng-model='js_filtroListado.de_SerieActivo']").first,
            "No se cargó el catálogo de Activos Fijos (no apareció el filtro de "
            "No. de serie del listado).",
            "catalogo_activos")

    async def buscar_serie_en_listado(self, no_serie: str) -> int:
        """Filtra el listado del catálogo por No. de serie y devuelve cuántas filas
        resultaron (0 = el activo NO está dado de alta)."""
        page = self._exigir_pagina()
        await self.ir_a_catalogo_activos()
        await self.set_input("js_filtroListado.de_SerieActivo", no_serie)
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
        """Cuenta las filas renderizadas del ngGrid visible."""
        page = self._exigir_pagina()
        try:
            return await page.evaluate(
                "() => document.querySelectorAll('.ngRow').length")
        except Exception:  # noqa: BLE001
            return 0

    async def alta_activo(self, tipo_nombre: str, campos: list,
                          detalles: "dict | None" = None) -> None:
        """Da de alta un activo en el SIPP.

        Args:
            tipo_nombre: nombre del tipo de activo (se elige en el combo).
            campos: lista de (ng_model, valor, control) donde control es
                'text' | 'number' | 'date' | 'select'.
            detalles: características del insumo {etiqueta -> valor} (camposDetalle).

        Abre el formulario, elige el tipo (lo que dispara la carga de las
        características), llena todo y pulsa Guardar, aceptando el aviso final.
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

        for ng_model, valor, control in campos:
            if not valor or not ng_model:
                continue
            if control == "select":
                try:
                    await self.set_combo(ng_model, valor)
                except ErrorSipp:
                    # Algunos "select" del portal son en realidad campos de texto
                    # con búsqueda; se intenta escribirlos.
                    await self.set_input(ng_model, valor)
            elif control == "date":
                await self.set_fecha(ng_model, valor)
            else:
                await self.set_input(ng_model, valor)

        if detalles:
            await self.llenar_campos_detalle(detalles)

        guardar = await self._primer_visible(
            [
                page.locator("[ng-click*='guardarActivoFijo()']"),
                page.get_by_role("button", name=re.compile(r"^\s*guardar\s*$", re.I)),
            ],
            "botón Guardar del alta de activo")
        await self._click_seguro(guardar)
        await self.confirmar_aviso_si_hay(3_000)

    async def modificar_activo(self, no_serie: str, campos: list,
                               detalles: "dict | None" = None) -> list:
        """Busca un activo por No. de serie, abre su edición, aplica los campos y
        guarda. Devuelve la lista de campos que NO se pudieron aplicar (el
        formulario de edición no expone exactamente los mismos que el alta, así que
        un campo ausente no aborta el resto).

        `campos`: [(ng_model, valor, control)] ya en su forma de EDICIÓN
        (filtrosEditar.* / FH_*_EDITAR)."""
        page = self._exigir_pagina()
        filas = await self.buscar_serie_en_listado(no_serie)
        if filas == 0:
            raise ErrorSipp(
                f"No se encontró en el listado un activo con la serie '{no_serie}'.")

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
                    try:
                        await self.set_combo(ng_model, valor)
                    except ErrorSipp:
                        await self.set_input(ng_model, valor)
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
