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
import json
import os
import re
import shutil
import sys
import threading
from datetime import datetime
from urllib.parse import urlsplit

from playwright.async_api import (
    Browser,
    BrowserContext,
    Locator,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from core import certificados, imagenes_sipp, rutas

# Carpeta del proyecto (para guardar diagnósticos del RPA en desarrollo).
_PROYECTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ------------------------------------------------ navegador (Chromium)
def _ruta_navegadores() -> str:
    """Carpeta (escribible) donde vive Chromium en la app empaquetada."""
    return os.path.join(rutas.DATOS, "ms-playwright")


def _revision_chromium() -> str:
    """Revisión de Chromium que EXIGE la versión de Playwright empaquetada.

    Playwright no acepta cualquier Chromium: busca exactamente 'chromium-<rev>',
    y la revisión sube con cada versión de Playwright. El dato vive en el
    browsers.json de su driver, así que se lee de ahí en vez de adivinarlo."""
    try:
        import playwright

        ruta = os.path.join(os.path.dirname(playwright.__file__), "driver",
                            "package", "browsers.json")
        with open(ruta, encoding="utf-8") as fh:
            for navegador in json.load(fh).get("browsers", []):
                if navegador.get("name") == "chromium":
                    return str(navegador.get("revision") or "")
    except Exception:  # noqa: BLE001 — sin el dato se cae al chequeo laxo
        pass
    return ""


def _hay_chromium(base: str) -> bool:
    """True si está el Chromium que ESTA versión de Playwright sabe usar.

    Antes bastaba con que hubiera un 'chromium-*' cualquiera, y eso rompía la app
    en cuanto el build subía de versión de Playwright: la carpeta vieja seguía
    ahí, la descarga se saltaba y el RPA moría al arrancar con «Executable
    doesn't exist at ...chromium-1243...». Se comprueba la revisión exacta."""
    revision = _revision_chromium()
    patron = f"chromium-{revision}" if revision else "chromium-*"
    return bool(glob.glob(os.path.join(base, patron, "**", "chrome.exe"),
                          recursive=True))


def _borrar_chromium_viejos(base: str) -> None:
    """Borra las revisiones de Chromium que ya no se usan (~180 MB cada una).

    Sin esto, cada actualización de la herramienta deja otra copia completa en la
    carpeta del usuario para siempre."""
    revision = _revision_chromium()
    if not revision:
        return
    viejas = (glob.glob(os.path.join(base, "chromium-*"))
              + glob.glob(os.path.join(base, "chromium_headless_shell-*")))
    for carpeta in viejas:
        if carpeta.endswith(f"-{revision}"):
            continue
        try:
            shutil.rmtree(carpeta, ignore_errors=True)
        except OSError:
            pass    # que no se pueda liberar espacio no es motivo para fallar


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
    # La descarga la hace NODE, con su propia lista de autoridades: donde un
    # antivirus inspecciona HTTPS no reconoce al emisor y falla. Se le pasan los
    # certificados de Windows, que son los que el equipo ya usa para navegar. No
    # se pisa la variable si el equipo ya trae una puesta a mano.
    if not entorno_driver.get("NODE_EXTRA_CA_CERTS"):
        pem = certificados.ruta_bundle_pem()
        if pem:
            entorno_driver["NODE_EXTRA_CA_CERTS"] = pem
    try:
        proc = await asyncio.create_subprocess_exec(
            node, cli, "install", "chromium", "--no-shell",
            env=entorno_driver,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        salida, _ = await proc.communicate()
    except Exception as exc:  # noqa: BLE001 — se reporta como ErrorSipp
        raise ErrorSipp("No se pudo descargar el navegador (Chromium): %s" % exc) from exc
    if not _hay_chromium(destino):
        raise ErrorSipp(_error_descarga(salida))
    _borrar_chromium_viejos(destino)


def _error_descarga(salida: bytes | None) -> str:
    """Traduce el fallo de la descarga del navegador a su causa probable.

    La salida del driver se leía y se tiraba, y el usuario recibía «revisa la
    conexión a internet» aunque el equipo tuviera internet de sobra. Los dos
    fallos que sí pasan en la práctica —inspección HTTPS y proxy que corta— se
    nombran; del resto se muestra el final de la salida real, que es lo único
    que permite avanzar."""
    texto = (salida or b"").decode("utf-8", "replace")
    if any(p in texto for p in ("self-signed certificate", "unable to verify",
                                "UNABLE_TO_GET_ISSUER_CERT",
                                "SELF_SIGNED_CERT_IN_CHAIN",
                                "CERT_", "certificate")):
        return (
            "No se pudo descargar el navegador del RPA: un antivirus o proxy de "
            "este equipo está inspeccionando el tráfico HTTPS y el descargador no "
            "reconoce a quien firma sus certificados.\n"
            "Pide que excluyan de la inspección HTTPS los dominios "
            "playwright.azureedge.net y playwright-akamai.azureedge.net, o que "
            "instalen el certificado raíz del antivirus en el almacén de Windows.")
    if any(p in texto for p in ("ECONNRESET", "ETIMEDOUT", "ENOTFOUND",
                                "EAI_AGAIN", "socket hang up", "407", "403")):
        return (
            "No se pudo descargar el navegador del RPA: la red cortó la descarga. "
            "Suele ser el proxy o el firewall del equipo; los dominios que hay que "
            "permitir son playwright.azureedge.net y "
            "playwright-akamai.azureedge.net.")
    cola = " ".join(texto.split())[-400:]
    return ("No se pudo preparar el navegador (Chromium). Revisa la conexión a "
            "internet e inténtalo de nuevo."
            + (f"\nDetalle: …{cola}" if cola else ""))


def serie_para_alta(serie: str = "", etiqueta: str = "",
                    etiqueta_actual: str = "") -> str:
    """No. de serie con el que un activo queda registrado en el SIPP.

    El campo es OBLIGATORIO en el portal, así que un activo sin serie legible se
    da de alta con su ETIQUETA. La regla vive aquí, y no dentro del alta, porque
    la herramienta necesita guardar EXACTAMENTE lo mismo que quedó en el SIPP: si
    cada lado la calculara por su cuenta, la columna «No. de serie» mostraría «—»
    donde el portal ya tiene un dato, y la comparación marcaría una diferencia
    inexistente.
    """
    return ((serie or "").strip() or (etiqueta or "").strip()
            or (etiqueta_actual or "").strip())


class ErrorSipp(Exception):
    """Falla esperada del RPA del SIPP (login fallido, elemento ausente, etc.)."""


# Errores de red de Chromium (net::ERR_*) traducidos a su causa real. Sin esto el
# usuario ve el código del navegador, que no le dice qué hacer.
_CAUSAS_RED = (
    ("ERR_INTERNET_DISCONNECTED", "el equipo no tiene conexión a internet"),
    ("ERR_NAME_NOT_RESOLVED",
     "no se pudo resolver la dirección del portal (revisa el DNS o la VPN)"),
    ("ERR_PROXY_CONNECTION_FAILED", "falló la conexión con el proxy de la red"),
    ("ERR_CONNECTION_TIMED_OUT", "el servidor no contestó a tiempo"),
    ("ERR_CONNECTION_REFUSED", "el servidor rechazó la conexión"),
    ("ERR_CONNECTION_RESET", "la conexión se interrumpió"),
    ("ERR_CERT", "el certificado de seguridad del sitio no es válido"),
)


def mensaje_amigable(exc) -> str:
    """Texto de un error del RPA apto para MOSTRARLE AL USUARIO.

    Playwright adjunta al mensaje un «Call log:» de varias líneas con selectores,
    URLs y tiempos: invaluable al depurar, ruido incomprensible en un aviso de la
    interfaz. Se conserva la primera parte y se descarta la bitácora."""
    texto = str(exc).split("Call log:")[0]
    return " ".join(texto.split()) or "Error desconocido del RPA."


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
    const pend = items.map(it => ({et: norm(it.etiqueta), orig: it.etiqueta,
                                   val: it.valor, ok: false}));
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
        llenados: pend.filter(p => p.ok).map(p => p.orig),
        faltantes: pend.filter(p => !p.ok).map(p => p.orig),
        inputs_detectados: inputs.length,
    };
}"""


# --- JavaScript que se evalúa en el portal para las fotografías ---------------
# El portal guarda las fotos del activo en `ar_ArchivosFotografias` (scope del
# controlador ActivosFijosNuevo). Un hueco con nombre/ruta en blanco es una foto
# BORRADA que conserva su lugar; solo cuentan las que tienen ambos.
# Texto del diálogo de error del portal («Error del Sistema» de
# `fnc_showErrorDetail`). Se lee con `textContent` y no con `innerText` a
# propósito: el detalle útil —el SQL y la columna que falló— vive dentro de un
# «Ver detalle» colapsado, y `innerText` se salta lo que no está visible.
ERROR_PORTAL_JS = r"""() => {
    const cajas = document.querySelectorAll(
        '.modal, .ui-dialog, [role=dialog], .sweet-alert, .alert, .panel-danger');
    for (const n of cajas) {
        if (!n.offsetParent) continue;                 // no está a la vista
        const visible = (n.innerText || '');
        if (!/error/i.test(visible)) continue;
        const todo = (n.textContent || '').replace(/[\s\u00a0]+/g, ' ').trim();
        if (todo) return todo.slice(0, 400);
    }
    return '';
}"""

ESTADO_FILA_LISTADO_JS = """(etiqueta) => {
    const el = document.querySelector("[ng-model='js_filtroListado.de_Etiqueta']");
    const sc = el && window.angular ? angular.element(el).scope() : null;
    const filas = sc && Array.isArray(sc.arr_gridActivosFijos)
        ? sc.arr_gridActivosFijos : [];
    const norm = (t) => String(t === null || t === undefined ? '' : t)
        .trim().toUpperCase();
    const fila = (etiqueta
        ? filas.find((f) => norm(f.DE_ETIQUETA) === norm(etiqueta)) : null) || filas[0];
    if (!fila) return null;
    return {
        etiqueta: String(fila.DE_ETIQUETA || ''),
        insumo: String(fila.NB_ACTIVOFIJO || ''),
        empleado: String(fila.NB_EMPLEADORESGUARDO || ''),
        id_empleado: String(fila.ID_EMPLEADORESGUARDO || ''),
        carta: !!fila.AR_CARTARESPONSIVA,
        inactivo: norm(fila.SN_ACTIVO) === 'FALSE',
    };
}"""

# El modal de reasignación vive en su propio scope (`scopeModal.content`), así que
# el `reasignarModalFiltros` bueno no está en el scope del botón sino más arriba:
# se sube por la cadena hasta encontrarlo. Leerlo del scope del controlador daba
# el objeto equivocado —el de la plantilla, sin el empleado recién elegido—.
EMPLEADO_REASIGNADO_JS = """() => {
    if (!window.angular) return null;
    const botones = document.querySelectorAll('[ng-click]');
    for (const el of botones) {
        const clic = el.getAttribute('ng-click') || '';
        if (clic.indexOf('empleados') === -1 || clic.indexOf('8') === -1) continue;
        let sc = angular.element(el).scope();
        while (sc) {
            const f = sc.reasignarModalFiltros
                || (sc.content && sc.content.reasignarModalFiltros);
            if (f) {
                return {id: String(f.id_EmpleadoNuevo || ''),
                        nombre: String(f.nb_EmpleadoNuevo || '')};
            }
            sc = sc.$parent;
        }
    }
    return null;
}"""

ESTADO_GENERAR_CC_JS = """() => {
    const el = document.querySelector(
        "input[ng-model='filtrosAgregar.sn_GenerarCentroCosto']");
    if (!el) return null;
    const sc = window.angular ? angular.element(el).scope() : null;
    const f = sc ? sc.filtrosAgregar : null;
    return {
        marcado: !!(f ? f.sn_GenerarCentroCosto : el.checked),
        deshabilitada: !!el.disabled,
        grupo: f ? String(f.id_GrupoCentroCostoResguardo || '') : '',
    };
}"""

ESTADO_FOTOS_JS = """(sel) => {
    const el = document.querySelector(sel);
    if (!el || !window.angular) return null;
    const sc = angular.element(el).scope();
    if (!sc) return null;
    const fotos = sc.ar_ArchivosFotografias || [];
    const emp = (sc.filtrosEditar || {}).id_EmpleadoResguardo;
    return {
        reales: fotos.filter((f) => f && f.nombre && f.ruta).length,
        empleado: emp ? String(emp) : "",
    };
}"""


class SesionSipp:
    """Maneja una sesión automatizada del SIPP: navegador, login y selección
    de empresa/sucursal. Pensada para reusarse desde distintos módulos."""

    # --- URLs ---
    # Por defecto PRODUCTIVO: el RPA da de alta y modifica activos REALES, y
    # las etiquetas que consume el alta son consecutivos que no se recuperan.
    #
    # El entorno se cambia con la variable de entorno SIPP_BASE_URL, NO
    # editando esta línea: el tag de GitHub es la versión, así que una URL de
    # pruebas olvidada aquí se compila y se auto-instala en todos los equipos.
    # Entornos conocidos:
    #   https://test.sipp.petroil.dev    (pruebas)
    #   https://stage.sipp.petroil.dev   (stage)
    #   https://dev.sipp.petroil.dev     (desarrollo)
    # Se lee al importar el módulo, así que la variable tiene que estar puesta
    # ANTES de arrancar la app; no se puede cambiar con la app abierta.
    BASE_URL = (os.environ.get("SIPP_BASE_URL")
                or "https://sipp.petroil.com.mx").rstrip("/")
    ES_PRODUCTIVO = BASE_URL == "https://sipp.petroil.com.mx"
    URL_LOGIN = BASE_URL + "/login.html"
    URL_CONFIG_SESION = BASE_URL + "/index.cfm#/configuracionsession"
    # Rutas SPA del módulo de Activos Fijos (confirmadas en el DOM real).
    URL_CATALOGO_ACTIVOS = BASE_URL + "/index.cfm#/ActivosFijosNuevo"
    URL_BANDEJA_COMPRAS = BASE_URL + "/index.cfm#/BandejaCompraActivos"

    # --- Fotografía del activo ---
    # El alta y la edición tienen inputs DISTINTOS, y el mismo id (`ar_ArchivoSoporte`)
    # se repite en el formulario de consulta, que está deshabilitado. Por eso el
    # localizador es el ng-change + `:not([disabled])`: identifica el formulario y
    # descarta la copia de solo lectura.
    SEL_FOTO_ALTA = "input[ng-change='subirFotografia(this)']:not([disabled])"
    SEL_FOTO_EDICION = ("input[ng-change='subirFotografiaEditar(this)']"
                        ":not([disabled])")

    # --- Generación del centro de costo ---
    # El portal NO pide el nombre: lo arma el servidor como
    # «grupo - insumo - No. Económico». La casilla llega DESHABILITADA y su
    # manejador sale antes si no hay Grupo Centro Costo elegido, así que
    # solo se puede marcar DESPUÉS de fijar el grupo.
    SEL_GENERAR_CC = "input[ng-model='filtrosAgregar.sn_GenerarCentroCosto']"

    # --- Reasignación (cambio del empleado de resguardo) ---
    # El SIPP la trata como un MOVIMIENTO, no como una edición: el formulario de
    # modificación ni siquiera deja tocar el empleado. Se hace desde la acción
    # «Reasignar Activo Fijo» de la fila, que el portal solo muestra en los
    # activos ACTIVOS. El modal pide EMPLEADO y ya: empresa, sucursal,
    # departamento y ubicación los rellena el portal con los del empleado elegido
    # (`cargarEmpresaSucursalEmpleado`), y por eso llegan deshabilitados.
    SEL_ACCION_REASIGNAR = "[ng-click*=reasignacion]"
    SEL_GUARDAR_REASIGNAR = "[ng-click*=guardarReasignacion]"
    SEL_EMPLEADO_REASIGNAR = "[ng-click*=\"abrirModal('empleados', 8\"]"

    # --- Tiempos de espera (ms) ---
    TIMEOUT_NAV = 30_000        # navegación / carga de página
    TIMEOUT_ELEMENTO = 10_000   # aparición de un elemento
    TIMEOUT_LOGIN_OK = 5_000    # confirmación de inicio de sesión
    # Llenado de formularios. Son CORTOS a propósito: se pagan una vez POR CAMPO y
    # el formulario ya está montado cuando se llega a llenarlo (el tipo y el insumo
    # se eligieron antes, con su propia espera). Un campo que está a la vista no
    # consume nada de esto; solo lo paga el que aún no pinta o no es editable.
    TIMEOUT_CAMPO = 500         # gracia para un campo presente que aún no aparece
    TIMEOUT_ESCRITURA = 1_200   # escribir en un campo ya visible
    # Cuántas coincidencias de un mismo localizador se revisan buscando la visible
    # (ver _primer_visible). El portal repite ng-clicks entre formularios, pero
    # nunca decenas del mismo a la vista.
    _MAX_COINCIDENCIAS = 12

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
        # Características del último alta que el insumo no tenía y quedaron en la
        # Descripción (ver `_detalles_a_descripcion`), para que el reporte lo diga.
        self.ultimos_en_descripcion: list = []
        self.ultimo_aviso_fotos: str = ""
        # Por qué NO se generó el centro de costo cuando se pidió ("" = se
        # generó o no se pidió). Lo lee el reporte de la corrida.
        self.ultimo_aviso_centro_costo: str = ""

    # ------------------------------------------------------ ciclo de vida
    async def iniciar(self) -> "SesionSipp":
        """Arranca Playwright, el navegador y una pestaña limpia."""
        await asegurar_navegador()
        self._pw = await async_playwright().start()
        # `channel="chromium"`: en modo headless Playwright usa por defecto un
        # binario aparte (chromium_headless_shell) que `asegurar_navegador` NO
        # descarga (instala con --no-shell para ahorrar ~80 MB). Con el canal
        # explícito se corre headless sobre el Chromium completo, que sí está.
        self.browser = await self._pw.chromium.launch(
            headless=self.headless, slow_mo=self.slow_mo, channel="chromium",
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

    # ------------------------------------------------------- navegación
    async def _abrir_url(self, url: str, descripcion: str) -> None:
        """Navega a `url` traduciendo los fallos de red a un aviso entendible.

        El error crudo ('Page.goto: Timeout 30000ms exceeded' + un Call log) no le
        dice al usuario ni qué falló ni qué hacer, y en la práctica la causa casi
        siempre es la misma: internet lento o intermitente, la VPN caída o el
        portal fuera de servicio. Eso es lo que se nombra aquí."""
        page = self._exigir_pagina()
        sitio = urlsplit(url).netloc or url
        try:
            await page.goto(url, wait_until="domcontentloaded",
                            timeout=self.TIMEOUT_NAV)
        except PlaywrightTimeoutError as exc:
            raise ErrorSipp(
                f"{descripcion} ({sitio}) no respondió en "
                f"{self.TIMEOUT_NAV // 1000} segundos. Suele ser por una conexión "
                "lenta o intermitente: revisa tu internet (y la VPN, si la usas) "
                "y vuelve a intentar."
            ) from exc
        except Exception as exc:  # noqa: BLE001 — red caída, DNS, proxy, certificado…
            texto = str(exc)
            causa = next((c for marca, c in _CAUSAS_RED if marca in texto), "")
            raise ErrorSipp(
                f"{descripcion} ({sitio}) no se pudo abrir: "
                f"{causa or mensaje_amigable(exc)}."
            ) from exc

    # ------------------------------------------------------------ login
    async def login(self, usuario: str, contrasena: str) -> None:
        """Inicia sesión en el portal. Lanza ErrorSipp si faltan credenciales o
        no se confirma el acceso al panel interno en 5 s."""
        if not usuario or not contrasena:
            raise ErrorSipp("Faltan credenciales para iniciar sesión en el SIPP.")
        page = self._exigir_pagina()
        await self._abrir_url(self.URL_LOGIN, "El portal del SIPP")

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
        await self._abrir_url(url, "La pantalla del SIPP")
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

    async def _campo_a_la_vista(self, ng_model: str) -> "Locator | None":
        """Devuelve el input visible de `ng_model`, o None si no aplica a esta
        pantalla. Se filtra por ':visible' porque el portal repite ng-models en
        paneles ocultos (ng-hide).

        El orden importa para la VELOCIDAD, porque esto se paga por cada campo de
        cada activo:
          1. ¿Ya está a la vista? `count()` NO espera: es una consulta al DOM.
             Es el caso normal y cuesta milisegundos.
          2. ¿Existe siquiera en el DOM? Si el portal no lo pinta para este tipo de
             activo (o el formulario de edición no lo expone), no va a aparecer por
             esperarlo: se omite YA, sin consumir ningún timeout.
          3. Existe pero oculto: puede ser una cascada de Angular a medio pintar,
             así que se le da una gracia corta y se sigue.
        """
        page = self._exigir_pagina()
        visibles = page.locator(f'[ng-model="{ng_model}"]:visible')
        try:
            cuantos = await visibles.count()
            if cuantos == 0:
                if await page.locator(f'[ng-model="{ng_model}"]').count() == 0:
                    return None
                await visibles.first.wait_for(state="visible",
                                              timeout=self.TIMEOUT_CAMPO)
                cuantos = await visibles.count()
            if cuantos > 1:
                # El portal REPITE el mismo ng-model entre sus modales (edición y
                # detalle) y una de las copias viene bloqueada: hay que quedarse con
                # la que sí admite escritura. La comprobación solo se paga cuando
                # hay duplicados a la vista, no en el caso normal.
                for i in range(cuantos):
                    campo = visibles.nth(i)
                    if await campo.is_editable(timeout=self.TIMEOUT_CAMPO):
                        return campo
        except PlaywrightTimeoutError:
            return None
        except Exception:  # noqa: BLE001 — DOM cambiando bajo los pies: se omite
            return None
        return visibles.first

    async def _campo_existe(self, ng_model: str) -> bool:
        """¿El ng-model está en el DOM? Consulta instantánea, sin esperas.

        NO se exige que sea visible: los <select> decorados con 'chosen' quedan
        ocultos y aun así se llenan por JS, así que pedirles visibilidad los daría
        por ausentes."""
        page = self._exigir_pagina()
        try:
            return await page.locator(f'[ng-model="{ng_model}"]').count() > 0
        except Exception:  # noqa: BLE001 — DOM cambiando: se trata como ausente
            return False

    async def set_input(self, ng_model: str, valor: str) -> None:
        """Escribe `valor` en un input por su ng-model.

        Si el campo NO aplica a esta pantalla se omite y se sigue: así el RPA
        avanza «conforme encuentra los campos» y no se atora en los no aplicables
        (ver `_campo_a_la_vista`)."""
        campo = await self._campo_a_la_vista(ng_model)
        if campo is None:
            return
        try:
            await campo.fill(valor, timeout=self.TIMEOUT_ESCRITURA)
        except Exception:  # noqa: BLE001 — respaldo: fijar por JS y avisar a Angular
            try:
                await campo.evaluate(
                    "(el, v) => { el.value = v;"
                    " el.dispatchEvent(new Event('input', {bubbles:true}));"
                    " el.dispatchEvent(new Event('change', {bubbles:true})); }",
                    valor, timeout=self.TIMEOUT_ESCRITURA)
            except Exception:  # noqa: BLE001 — no editable (deshabilitado): se omite
                pass

    async def set_fecha(self, ng_model: str, valor: str) -> None:
        """Escribe una fecha (DD/MM/AAAA) en un input con máscara. Se usa `fill`,
        que enfoca SIN clic real: así no se abre el calendario y Angular sí
        registra el valor (dispara 'input').

        Pasa por el mismo chequeo que `set_input`: sin él, una fecha que no exista
        en la pantalla se llevaba el timeout POR DEFECTO de Playwright (30 s).
        A diferencia de `set_input`, aquí el fallo SÍ se reporta (lanza): es lo que
        alimenta la lista de campos no aplicados de `modificar_activo`, y el motivo
        va en el mensaje porque no es el mismo problema que el campo no exista a
        que el portal lo bloquee (p. ej. la Fecha de Asignación de la edición, que
        el SIPP marca `ng-disabled` porque se cambia al reasignar)."""
        campo = await self._campo_a_la_vista(ng_model)
        if campo is None:
            raise ErrorSipp("No está en esta pantalla del SIPP.")
        if not await campo.is_editable(timeout=self.TIMEOUT_CAMPO):
            raise ErrorSipp("El SIPP lo bloquea aquí (es de solo lectura).")
        await campo.fill(valor, timeout=self.TIMEOUT_ESCRITURA)

    async def llenar_campos_detalle(self, detalles: dict) -> dict:
        """Llena las CARACTERÍSTICAS del insumo ('Detalles Insumo'), que en el SIPP
        son dinámicas (camposDetalle) y se emparejan por su rótulo. `detalles` es
        {etiqueta -> valor}. Devuelve {llenados, faltantes, inputs_detectados}."""
        items = [{"etiqueta": k, "valor": v} for k, v in (detalles or {}).items() if v]
        if not items:
            return {"llenados": [], "faltantes": [], "inputs_detectados": 0}
        page = self._exigir_pagina()
        return await page.evaluate(_JS_LLENAR_CAMPOS_DETALLE, {"items": items})

    async def _detalles_a_descripcion(self, ng_descripcion: str, detalles: dict,
                                      faltantes: list) -> list:
        """Deja en la DESCRIPCIÓN las características que el insumo no tiene.

        Marca, Modelo, Cliente… son «Detalles Insumo»: el SIPP los muestra o no
        según el insumo elegido. La herramienta los pide para cualquier activo
        porque el inventario en Excel los trae, pero si el insumo no tiene ese
        rótulo el dato no tenía dónde caer y se perdía EN SILENCIO —el resultado
        del llenado ni se miraba—. La Descripción existe en todos los tipos: ahí va
        «Marca: HERMAN MILLER · Modelo: SILLA VERSUS OPERATIVA».

        Se lee lo que el campo ya tiene y solo se agrega lo que no esté: así una
        modificación repetida no apila el mismo texto una y otra vez. Devuelve los
        rótulos que se movieron a la Descripción. Best-effort: si el campo no está,
        no aborta el alta.
        """
        piezas = [(e, str(detalles.get(e) or "").strip()) for e in faltantes]
        piezas = [(e, v) for e, v in piezas if v]
        if not piezas:
            return []
        try:
            actual = (await self._valor_actual(ng_descripcion)).strip()
        except Exception:  # noqa: BLE001 — sin campo no hay dónde escribir
            return []
        nuevas = [(e, v) for e, v in piezas
                  if f"{e}: {v}".upper() not in actual.upper()]
        if not nuevas:
            return [e for e, _v in piezas]      # ya estaban de una vez anterior
        texto = " · ".join(f"{e}: {v}" for e, v in nuevas)
        try:
            await self.set_input(ng_descripcion,
                                 f"{actual} · {texto}" if actual else texto)
        except Exception:  # noqa: BLE001 — no aplicable aquí: no aborta el alta
            return []
        return [e for e, _v in piezas]

    # ------------------------------------------------ módulo de Activos Fijos
    async def ir_a_catalogo_activos(self) -> None:
        """Navega al catálogo de Activos Fijos (#/ActivosFijosNuevo) y espera a que
        cargue el filtro del listado. La SPA + su grid tardan en montar, así que se
        da un margen mayor que el de un elemento normal."""
        page = self._exigir_pagina()
        ancla = page.locator("[ng-model='js_filtroListado.de_SerieActivo']").first
        await self._abrir_url(self.URL_CATALOGO_ACTIVOS,
                              "El catálogo de Activos Fijos del SIPP")
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

    async def _limpiar_ambito_listado(self) -> None:
        """Vacía empresa y sucursal del filtro del listado para que la búsqueda
        sea global (solo por etiqueta/serie). Best-effort: si Angular no está o el
        scope cambió, no rompe el flujo."""
        page = self._exigir_pagina()
        try:
            await page.evaluate(r"""() => {
              const el = document.querySelector("[ng-model^='js_filtroListado']");
              const sc = el && window.angular ? angular.element(el).scope() : null;
              if (!sc || !sc.js_filtroListado) return;
              sc.$apply(() => {
                sc.js_filtroListado.id_Empresa = '';
                sc.js_filtroListado.id_SucursalAsignado = '';
              });
            }""")
        except Exception:  # noqa: BLE001
            pass

    async def buscar_en_listado(self, etiqueta: str = "", serie: str = "") -> int:
        """Filtra el listado del catálogo por ETIQUETA (ancla principal) o, si no
        hay, por No. de serie; devuelve cuántas filas resultaron (0 = el activo NO
        está dado de alta). La etiqueta es más confiable: casi siempre existe,
        mientras que muchos activos no traen serie.

        Busca **solo por etiqueta/serie**: antes de filtrar limpia empresa y
        sucursal del `js_filtroListado`. Si no se limpian, el listado queda
        acotado a la empresa/sucursal de la sesión (p. ej. Aske/Corporativo) y no
        encuentra activos de otras empresas —era la causa de que un activo real de
        otra empresa saliera como 'no dado de alta'."""
        page = self._exigir_pagina()
        await self.ir_a_catalogo_activos()
        etiqueta = (etiqueta or "").strip()
        serie = (serie or "").strip()
        if not etiqueta and not serie:
            return 0
        await self._limpiar_ambito_listado()
        if etiqueta:
            await self.set_input("js_filtroListado.de_Etiqueta", etiqueta)
        elif serie:
            await self.set_input("js_filtroListado.de_SerieActivo", serie)
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
        # Hay un botón igual por formulario (alta, edición, reasignación) y todos
        # viven en el DOM a la vez: el bueno es el del formulario ABIERTO, que es
        # el único visible. De eso se encarga `_primer_visible`.
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

    # A qué formulario entrega el modal «Buscar Empleado». El portal no tiene uno
    # por pantalla: tiene UNO solo y reparte el elegido con un número de opción
    # (`agregarEmpleado` hace un switch sobre `opcionEmpleado`). Elegir el botón
    # equivocado deja al empleado en otro formulario y el que se ve sigue vacío.
    OPCION_EMPLEADO_ALTA = 2
    OPCION_EMPLEADO_REASIGNAR = 8

    async def seleccionar_empleado(self, id_empleado, nombre: str = "",
                                   opcion: int = OPCION_EMPLEADO_ALTA) -> None:
        """Elige un empleado en el modal 'Buscar Empleado': abre el modal y busca
        por ID si se tiene (exacto) o, si no, por NOMBRE; luego pulsa el botón de
        la fila. Así el modal se abre y busca al empleado aunque no se haya
        resuelto su id.

        `opcion` dice a QUÉ formulario va el empleado elegido (ver las constantes
        de arriba): 2 es la asignación del alta y 8 el modal de reasignación.
        """
        id_empleado = str(id_empleado or "").strip()
        nombre = (nombre or "").strip()
        if not id_empleado and not nombre:
            return  # nada que buscar
        page = self._exigir_pagina()
        abrir = await self._primer_visible(
            [page.locator(f"[ng-click*=\"abrirModal('empleados', {opcion}\"]"),
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

    # ------------------------------------------------------- fotografías
    async def _estado_fotografias(self, selector: str) -> "dict | None":
        """Fotos que YA tiene el activo abierto y si hay empleado de resguardo.

        Se lee del scope de AngularJS y no del DOM porque la lista de fotos
        (`ar_ArchivosFotografias`) solo se pinta dentro de un `ng-if`, y sus huecos
        vacíos —los que deja borrar una foto— no se pintan en absoluto: contar
        miniaturas daría un número distinto al que usa el portal para decidir si
        acepta la subida.

        El empleado viene junto porque al guardar la edición el portal EXIGE
        empleado de resguardo si el activo lleva alguna foto: sin él, Guardar aborta
        con un mensaje en línea y no se guarda NADA del activo, ni los campos. Mejor
        saberlo antes de subir la foto que perder la modificación entera.

        Devuelve None si no se pudo leer (otra versión del portal): quien llama
        sigue adelante a ciegas, que es como se hacía antes.
        """
        try:
            return await self._exigir_pagina().evaluate(
                ESTADO_FOTOS_JS, selector)
        except Exception:  # noqa: BLE001 — sin lectura se sigue a ciegas
            return None

    async def _esperar_fotografias(self, selector: str, objetivo: int) -> int:
        """Espera a que el portal termine de subir las fotos. Devuelve cuántas hay
        (o -1 si no se pudo leer el estado).

        Cada archivo viaja a un almacenamiento externo y solo cuando responde el
        servidor aparece en `ar_ArchivosFotografias`; pulsar Guardar antes las
        pierde sin avisar. Por eso se espera al ARREGLO y no un tiempo fijo.
        """
        page = self._exigir_pagina()
        for _ in range(60):  # ~30 s: son subidas reales, no un repintado
            estado = await self._estado_fotografias(selector)
            if estado is None:
                return -1
            if estado["reales"] >= objetivo:
                return estado["reales"]
            await page.wait_for_timeout(500)
        estado = await self._estado_fotografias(selector)
        return estado["reales"] if estado else -1

    async def subir_fotografias(self, selector: str, imagenes: "list | None",
                                detallar_exito: bool = True) -> dict:
        """Sube fotos al formulario abierto (alta o edición) SIN borrar ninguna.

        El SIPP admite 3 por activo. Aquí solo se llenan los HUECOS LIBRES: lo que
        ya está en el portal no se toca, porque pudo subirlo otra persona y la
        herramienta no tiene con qué decidir cuál sobra. Lo que no cabe se devuelve
        por su nombre para que el usuario baje a mano las que considere y vuelva a
        intentar.

        Las fotos entran por ORDEN DE NOMBRE: cuando solo caben algunas, que el
        criterio sea estable y evidente vale más que cualquier heurística.

        Devuelve `{"texto", "pendientes", "subidas"}`: el texto es para el reporte
        (vacío si no hay nada que decir) y `pendientes` son los nombres de archivo
        que se quedaron fuera por falta de espacio. Best-effort: una foto que no
        sube no tumba el alta ni la modificación, pero tiene que constar.

        `detallar_exito`: con False, callar cuando todo salió bien. En el ALTA la
        foto es una parte más del alta y anunciarla en cada fila solo estorba; en la
        MODIFICACIÓN es justo lo que el usuario fue a comprobar, así que se dice.
        """
        def resultado(texto: str, pendientes=(), subidas: int = 0) -> dict:
            return {"texto": texto, "pendientes": list(pendientes),
                    "subidas": subidas}

        rutas, avisos = imagenes_sipp.preparar(
            sorted(imagenes or [], key=lambda p: os.path.basename(p).lower()),
            maximo=imagenes_sipp.MAXIMO)
        if not rutas:
            return resultado("; ".join(avisos))

        page = self._exigir_pagina()
        estado = await self._estado_fotografias(selector)
        previas = estado["reales"] if estado else 0
        pendientes: list[str] = []
        if estado is not None:
            if selector == self.SEL_FOTO_EDICION and not estado["empleado"]:
                return resultado(
                    "No se subió la fotografía: el SIPP la condiciona a que el "
                    "activo tenga empleado de resguardo, y este no lo tiene.")
            libres = imagenes_sipp.MAXIMO - previas
            if libres <= 0:
                return resultado(
                    f"Sin espacio para fotografías: el activo ya tiene "
                    f"{previas} en el SIPP. Baja a mano las que no sirvan y vuelve "
                    f"a intentar.",
                    pendientes=[os.path.basename(p) for p in rutas])
            if len(rutas) > libres:
                pendientes = [os.path.basename(p) for p in rutas[libres:]]
                rutas = rutas[:libres]

        try:
            await page.set_input_files(selector, rutas)
        except Exception as exc:  # noqa: BLE001 — no crítico: se reporta y sigue
            avisos.append("No se pudo adjuntar la fotografía: "
                          + mensaje_amigable(exc))
            return resultado("; ".join(avisos), pendientes)

        subidas = await self._esperar_fotografias(selector, previas + len(rutas))
        if subidas < 0:  # sin lectura del scope: se le da su tiempo y a ciegas
            await page.wait_for_timeout(1500 * len(rutas))
            hechas = len(rutas)
        else:
            hechas = max(0, subidas - previas)
        if hechas and not detallar_exito and not avisos and not pendientes:
            return resultado("", pendientes, hechas)
        partes = []
        if hechas:
            partes.append(f"{hechas} fotografía(s) subida(s)"
                          + (f" (el activo ya tenía {previas})" if previas else ""))
        else:
            partes.append("La fotografía no terminó de subir")
        if pendientes:
            partes.append(f"sin espacio para {', '.join(pendientes)}")
        partes.extend(avisos)
        return resultado("; ".join(partes), pendientes, hechas)

    async def marcar_generar_centro_costo(self) -> str:
        """Marca «Generar Centro Costo» en el formulario de alta.

        Devuelve "" si quedó marcada o el MOTIVO por el que no: que no se pueda
        no es excusa para abortar el alta —el activo se registra igual, solo que
        sin centro de costo nuevo— pero el usuario tiene que enterarse, porque
        creyó haberlo pedido.

        Se pulsa el control de verdad en vez de tocar el scope: la casilla lleva
        `ng-change`, y asignar la variable a mano no dispara el manejador que
        vacía el Centro Costo elegido. Guardar con los dos puestos es justo lo que
        el portal no espera.
        """
        page = self._exigir_pagina()
        try:
            estado = await page.evaluate(ESTADO_GENERAR_CC_JS)
        except Exception:  # noqa: BLE001 — sin lectura no se arriesga el alta
            return "no se pudo leer la casilla «Generar Centro Costo»"
        if estado is None:
            return "el formulario del portal no tiene la casilla «Generar Centro Costo»"
        if estado["marcado"]:
            return ""
        if estado["deshabilitada"] or not estado["grupo"]:
            # El manejador del portal (`habilitarComboCentroCostoResguardo`) sale
            # antes si no hay grupo: sin él, marcarla no haría nada.
            return ("el SIPP no habilita «Generar Centro Costo» sin un Grupo Centro "
                    "Costo elegido")
        try:
            await self._click_seguro(page.locator(self.SEL_GENERAR_CC).first)
            await page.wait_for_timeout(400)
            estado = await page.evaluate(ESTADO_GENERAR_CC_JS)
        except Exception as exc:  # noqa: BLE001 — se reporta y el alta sigue
            return f"no se pudo marcar «Generar Centro Costo»: {mensaje_amigable(exc)}"
        if not (estado and estado["marcado"]):
            return "la casilla «Generar Centro Costo» no quedó marcada"
        return ""

    async def alta_activo(self, tipo_nombre: str, campos: list,
                          detalles: "dict | None" = None,
                          insumo_id=None, empleado_id=None,
                          serie: str = "", etiqueta_actual: str = "",
                          empresa: str = "", sucursal: str = "",
                          empleado_nombre: str = "", imagenes: "list | None" = None,
                          generar_centro_costo: bool = False) -> None:
        """Da de alta un activo en el SIPP.

        Args:
            tipo_nombre: nombre del tipo de activo (se elige en el combo).
            campos: lista de (ng_model, valor, control) donde control es
                'text' | 'number' | 'date' | 'select'.
            detalles: características del insumo {etiqueta -> valor} (camposDetalle).
            insumo_id: Cve Insumo del SIPP; se selecciona por el modal 'Buscar
                Insumo' (el nombre del insumo es de solo lectura, se elige así).
            generar_centro_costo: marca «Generar Centro Costo» para que el SIPP
                cree uno nuevo («grupo - insumo - No. Económico») en vez de
                elegir uno existente. Requiere Grupo Centro Costo en `campos`.
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
                    # Combos que CARGAN POR AJAX al elegir otro: sus opciones no
                    # existen todavía cuando se llega a ellos, así que hay que
                    # reintentar hasta que aparezcan. La sucursal cuelga de la
                    # empresa igual que el centro de costo cuelga del grupo; sin
                    # esperarla se quedaba vacía y el SIPP —que la exige— rechazaba
                    # el guardado sin que la herramienta se enterara.
                    # La cadena completa llega por AJAX: empresa -> sucursal ->
                    # grupo CC -> centro CC. El GRUPO también hay que esperarlo (sus
                    # opciones no existen hasta que la sucursal las trae); sin eso se
                    # quedaba vacío y el campo se perdía en silencio. El departamento
                    # NO cuelga de nadie (no tiene ng-disabled), así que no se espera:
                    # ahí un valor inexistente costaría el timeout completo por activo.
                    dependiente = ("Sucursal" in ng_model
                                   or "CentroCosto" in ng_model)
                    try:
                        await self.set_combo(ng_model, valor, esperar=dependiente)
                    except ErrorSipp:
                        # Algunos "select" del portal son en realidad campos de texto
                        # con búsqueda; se intenta escribirlos.
                        await self.set_input(ng_model, valor)
                    if "GrupoCentroCosto" in ng_model or "Empresa" in ng_model:
                        # Deja que la cascada traiga las opciones dependientes
                        # (centros del grupo / sucursales de la empresa).
                        await page.wait_for_timeout(1200)
                elif control == "date":
                    await self.set_fecha(ng_model, valor)
                else:
                    await self.set_input(ng_model, valor)
            except Exception:  # noqa: BLE001 — campo no aplicable: se omite, no aborta
                continue

        self.ultimos_en_descripcion = []
        if detalles:
            res = await self.llenar_campos_detalle(detalles)
            self.ultimos_en_descripcion = await self._detalles_a_descripcion(
                "filtrosAgregar.de_DescripcionActivo", detalles,
                res.get("faltantes") or [])

        # Factura + precio desde la bandeja de compras (best-effort): nunca aborta
        # el alta; solo actúa si la serie es válida y existe la entrada de compra.
        try:
            await self._adjuntar_compra(serie, etiqueta_actual, empresa, sucursal)
        except Exception:  # noqa: BLE001 — no crítico: se omite sin tumbar el alta
            pass

        # RE-APLICAR los campos de la Asignación que dependen de cascada/auto-relleno
        # (grupo -> centro -> departamento): elegir el empleado o la empresa/sucursal
        # de resguardo puede recargarlos o limpiarlos, así que se fijan AL FINAL, en
        # orden de dependencia, para que persistan al guardar.
        self.ultimo_aviso_centro_costo = ""
        for clave in ("filtrosAgregar.id_GrupoCentroCostoResguardo",
                      "filtrosAgregar.id_CentroCostoResguardo",
                      "filtrosAgregar.id_DepartamentoResguardo"):
            # Si el SIPP va a CREAR el centro de costo, no se elige ninguno:
            # al marcar la casilla el portal vacía ese combo, y volver a
            # llenarlo deja el formulario pidiendo las dos cosas a la vez.
            if generar_centro_costo and clave.endswith("id_CentroCostoResguardo"):
                continue
            valor = next((v for ng, v, _c in campos if ng == clave and v), "")
            if not valor:
                continue
            try:
                await self.set_combo(clave, valor,
                                     esperar="CentroCosto" in clave)
                if clave.endswith("id_GrupoCentroCostoResguardo"):
                    await page.wait_for_timeout(1000)  # deja cargar los centros
            except Exception:  # noqa: BLE001 — no aplica: se omite
                pass

        # La casilla va DESPUÉS de la cascada: el portal la mantiene
        # deshabilitada mientras no haya Grupo Centro Costo elegido.
        if generar_centro_costo:
            self.ultimo_aviso_centro_costo = (
                await self.marcar_generar_centro_costo())

        # Imágenes/soporte del insumo (Fotografía, máx 3). Best-effort: una foto
        # que no entre no debe costar el alta entera.
        self.ultimo_aviso_fotos = (await self.subir_fotografias(
            self.SEL_FOTO_ALTA, imagenes, detallar_exito=False))["texto"]

        # La ETIQUETA/folio es un consecutivo GLOBAL del SIPP (getEtiqueta ignora
        # empresa y tipo): devuelve el "siguiente" disponible y avanza al guardar
        # cada activo. Se genera con el botón del portal ANTES de guardar; el código
        # generado se devuelve para registrarlo en la herramienta.
        etiqueta = await self.generar_etiqueta()

        # El No. de serie es OBLIGATORIO en el SIPP. Si el activo NO trae serie, se
        # usa la ETIQUETA como número de serie (la generada por el SIPP; como
        # respaldo, la del levantamiento). Se hace tras generar la etiqueta.
        serie_final = serie_para_alta(serie, etiqueta, etiqueta_actual)
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
                               detalles: "dict | None" = None,
                               punto_control=None, insumo_id=None,
                               imagenes: "list | None" = None) -> dict:
        """Busca un activo por ETIQUETA (o serie si no hay), abre su edición, aplica
        los campos y guarda. Devuelve:

            {"cambios": [(ng_model, antes, después)],
             "no_aplicados": [(ng_model, motivo)],
             "fotos": "texto de lo que pasó con la fotografía",
             "fotos_pendientes": [nombres que no cupieron en el SIPP],
             "detalles": [rótulos de «Detalles Insumo» que se escribieron]}

        `cambios` es lo que de verdad cambió en el portal (leído del formulario
        antes y después de escribir), para que el reporte pueda mostrarlo.
        `no_aplicados` son los campos que el formulario de edición no admitió: no
        expone exactamente los mismos que el alta, así que uno ausente no aborta el
        resto. El MOTIVO va incluido porque «no existe» y «el portal no deja
        editarlo» piden acciones distintas de quien lee el reporte.

        `campos`: [(ng_model, valor, control)] ya en su forma de EDICIÓN
        (filtrosEditar.* / FH_*_EDITAR).

        `punto_control`: corrutina que se llama entre campos para permitir detener
        el proceso a media captura (ver `_aplicar_campos_edicion`). Si aborta antes
        de Guardar, el activo queda SIN tocar en el SIPP.

        `insumo_id`: si se pasa, cambia el INSUMO con el modal «Buscar Insumo» de
        la edición (no es un campo de texto: el portal lo bloquea y solo se elige
        ahí). Se omite si el activo ya tiene ese insumo.

        `imagenes`: fotos del levantamiento que se adjuntan al activo, solo en los
        huecos libres de los 3 que admite el SIPP. Nunca se borra una foto del
        portal; las que no caben vuelven en `fotos_pendientes` para reportarlas."""
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

        # El INSUMO va PRIMERO, como en el alta: elegirlo repinta los campos por
        # tipo (camposDetalle), así que hacerlo después borraría lo ya escrito.
        cambios, no_aplicados = [], []
        if insumo_id:
            cambio = await self._cambiar_insumo_edicion(insumo_id)
            if cambio:
                cambios.append(cambio)

        mas_cambios, no_aplicados = await self._aplicar_campos_edicion(
            campos, punto_control=punto_control)
        cambios.extend(mas_cambios)

        en_descripcion, detalles_escritos = [], []
        if detalles:
            res = await self.llenar_campos_detalle(detalles)
            detalles_escritos = res.get("llenados") or []
            en_descripcion = await self._detalles_a_descripcion(
                "filtrosEditar.de_DescripcionActivo", detalles,
                res.get("faltantes") or [])

        # La fotografía va al final: la subida sale del navegador hacia el
        # almacenamiento del portal y tarda, así que se hace cuando el resto del
        # formulario ya está puesto y todavía antes de Guardar (detenerse aquí
        # sigue dejando el activo intacto).
        fotos = {"texto": "", "pendientes": []}
        if imagenes:
            fotos = await self.subir_fotografias(self.SEL_FOTO_EDICION, imagenes)

        # Última salida limpia: detenerse AQUÍ deja el activo intacto en el portal,
        # porque nada se guarda hasta pulsar Guardar. Pasado este punto el cambio ya
        # está enviado y detener solo evita seguir con el SIGUIENTE activo.
        if punto_control is not None:
            await punto_control()

        guardar = await self._primer_visible(
            [
                page.locator("[ng-click*='guardarActivoFijoEditar()']"),
                page.get_by_role("button", name=re.compile(r"^\s*guardar\s*$", re.I)),
            ],
            "botón Guardar de la edición del activo")
        await self._click_seguro(guardar)
        await self.confirmar_aviso_si_hay(3_000)
        return {"cambios": cambios, "no_aplicados": no_aplicados,
                "en_descripcion": en_descripcion, "fotos": fotos["texto"],
                "fotos_pendientes": fotos["pendientes"],
                "detalles": detalles_escritos}

    async def _cambiar_insumo_edicion(self, insumo_id) -> "tuple | None":
        """Cambia el insumo del activo abierto en la EDICIÓN. Devuelve el
        (ng_model, antes, después) del reporte, o None si no hubo cambio.

        A diferencia del empleado —que la edición del portal no deja tocar—, el
        insumo SÍ es modificable ahí: su campo es `readonly`, pero tiene al lado el
        botón «Buscar Insumo» (`abrirModal('insumos')`), el mismo modal del alta.

        Antes de abrirlo se compara el ID que ya tiene el activo: si es el mismo, se
        ahorra todo el viaje por el modal, que es lo caro. Se compara por ID y no
        por nombre porque el nombre del portal y el del levantamiento pueden diferir
        en mayúsculas, acentos o serie, y eso provocaría reelecciones inútiles."""
        deseado = str(insumo_id or "").strip()
        if not deseado:
            return None
        actual_id = (await self._valor_actual("filtrosEditar.id_InsumoOrigen")).strip()
        if actual_id == deseado:
            return None
        antes = await self._valor_actual("filtrosEditar.nb_NombreInsumo")
        await self.seleccionar_insumo(deseado)
        despues = await self._valor_actual("filtrosEditar.nb_NombreInsumo")
        if (antes or "").strip() == (despues or "").strip():
            return None
        return ("filtrosEditar.nb_NombreInsumo", antes, despues)

    async def _valor_actual(self, ng_model: str, control: str = "text") -> str:
        """Lo que el formulario YA tiene en ese campo, para el «antes» del reporte.

        Se lee ANTES de escribir; es el valor real del portal, no el que la
        herramienta suponga. Un select se lee por el TEXTO de su opción (que es lo
        que ve el usuario) y sin exigir que esté a la vista: cuando lo decora
        'chosen', el <select> original queda oculto pero conserva el valor."""
        campo = await self._campo_a_la_vista(ng_model)
        if campo is None:
            # Sin copia a la vista: puede ser un select oculto por 'chosen' (que sí
            # conserva su valor) o un campo que no existe. Se distingue con una
            # consulta instantánea, porque operar sobre un locator que no resuelve
            # se lleva el timeout POR DEFECTO de Playwright (30 s) por campo.
            if not await self._campo_existe(ng_model):
                return ""
            campo = self._exigir_pagina().locator(f'[ng-model="{ng_model}"]').first
        try:
            if control == "select":
                return await campo.evaluate(
                    "el => (el.options && el.selectedIndex >= 0)"
                    " ? (el.options[el.selectedIndex].text || '') : (el.value || '')",
                    timeout=self.TIMEOUT_ESCRITURA) or ""
            return await campo.input_value(timeout=self.TIMEOUT_CAMPO)
        except Exception:  # noqa: BLE001 — campo ilegible: se sigue sin «antes»
            return ""

    async def _aplicar_campos_edicion(self, campos: list,
                                      punto_control=None) -> "tuple[list, list]":
        """Aplica `campos` al formulario de edición ya abierto.

        Devuelve `(cambios, no_aplicados)`:
          - `cambios`: [(ng_model, antes, después)] SOLO de lo que de verdad cambió.
            Un campo cuyo valor ya coincidía no es un cambio y no debe ensuciar el
            reporte.
          - `no_aplicados`: [(ng_model, motivo)] de lo que el portal no admitió.

        `punto_control` (opcional) se llama ANTES de cada campo: es lo que permite
        que «Detener» corte en segundos en vez de al terminar el activo. Lanza
        RpaDetenido, que sale sin pulsar Guardar y deja el activo intacto.
        """
        page = self._exigir_pagina()
        cambios, no_aplicados = [], []
        for ng_model, valor, control in campos:
            if punto_control is not None:
                await punto_control()
            if not valor or not ng_model:
                continue
            # Un campo que no está en el formulario se reporta y se salta SIN
            # pelearlo: insistir costaba ~60 s por campo (esperas de Playwright +
            # reintentos del combo) y encima no dejaba rastro en el reporte.
            if not await self._campo_existe(ng_model):
                no_aplicados.append(
                    (ng_model, "No existe en el formulario de edición del SIPP."))
                continue
            antes = await self._valor_actual(ng_model, control)
            try:
                if control == "select":
                    # Cascada AJAX: el grupo cuelga de la sucursal y el centro del
                    # grupo, así que en ambos hay que esperar a que sus opciones
                    # carguen; si no, el combo queda vacío y el cambio se pierde.
                    cascada = "Sucursal" in ng_model or "CentroCosto" in ng_model
                    try:
                        await self.set_combo(ng_model, valor, esperar=cascada)
                    except ErrorSipp:
                        await self.set_input(ng_model, valor)
                    if "GrupoCentroCosto" in ng_model:
                        await page.wait_for_timeout(1200)
                elif control == "date":
                    await self.set_fecha(ng_model, valor)
                else:
                    await self.set_input(ng_model, valor)
            except ErrorSipp as exc:  # motivo conocido (ausente / bloqueado)
                no_aplicados.append((ng_model, str(exc)))
                continue
            except Exception as exc:  # noqa: BLE001 — cualquier otro fallo del campo
                no_aplicados.append((ng_model, mensaje_amigable(exc)))
                continue
            # El «después» se relee del formulario en vez de dar por hecho que se
            # escribió lo que se pidió: las máscaras y los combos normalizan (una
            # fecha se reformatea, un select guarda el texto de su opción).
            despues = await self._valor_actual(ng_model, control)
            if despues.strip() != antes.strip():
                cambios.append((ng_model, antes.strip(), despues.strip()))
        return cambios, no_aplicados

    # --------------------------------------------------------- utilidades
    async def _click_seguro(self, locator: Locator) -> None:
        """Clic robusto: normal y, si algo lo intercepta (overlay/flotante), por DOM."""
        try:
            await locator.click(timeout=self.TIMEOUT_ELEMENTO)
        except Exception:  # noqa: BLE001 — respaldo por JS
            await locator.evaluate("el => el.click()")

    @staticmethod
    def _mismo_nombre(a: str, b: str) -> bool:
        """¿Dos nombres de empleado son el mismo? (espacios y mayúsculas aparte)."""
        return " ".join(str(a or "").split()).upper() ==                " ".join(str(b or "").split()).upper()

    async def reasignar_activo(self, etiqueta: str, serie: str, id_empleado,
                               nombre_empleado: str = "",
                               cancelar_carta: bool = False) -> dict:
        """Cambia el EMPLEADO DE RESGUARDO de un activo, por la vía que el portal
        ofrece para eso: la acción «Reasignar Activo Fijo» de su fila.

        Devuelve {"hecho", "motivo", "carta", "empleado_anterior",
        "empleado_nuevo"}. `hecho=False` NO es un error: son los casos en que el
        portal no permite la reasignación y hay que reportarlos, no reventar el
        lote.

        Si el activo tiene CARTA RESPONSIVA y `cancelar_carta` es False, no se
        toca nada: el SIPP exige cancelar la carta para reasignar, y eso lo decide
        el usuario. Con True se acepta esa cancelación, que es lo que haría una
        persona a mano.

        La fecha de asignación se deja como la propone el portal (hoy): es la
        fecha del movimiento, no la del levantamiento.
        """
        page = self._exigir_pagina()
        filas = await self.buscar_en_listado(etiqueta=etiqueta, serie=serie)
        if filas == 0:
            ident = (etiqueta or "").strip() or (serie or "").strip()
            raise ErrorSipp(
                f"No se encontró en el listado un activo con etiqueta/serie '{ident}'.")

        # Lo que el portal sabe de la fila ANTES de tocar nada: de ahí salen la
        # carta responsiva y el estatus, que deciden si siquiera se intenta.
        try:
            fila = await page.evaluate(ESTADO_FILA_LISTADO_JS, (etiqueta or "").strip())
        except Exception:  # noqa: BLE001 — sin lectura se sigue a ciegas
            fila = None
        anterior = (fila or {}).get("empleado", "")
        base = {"hecho": False, "carta": bool((fila or {}).get("carta")),
                "empleado_anterior": anterior, "empleado_nuevo": "", "motivo": ""}
        if fila and fila.get("inactivo"):
            return {**base, "motivo": ("el activo está INACTIVO en el SIPP y el "
                                       "portal no ofrece reasignarlo")}
        if fila and fila.get("carta") and not cancelar_carta:
            return {**base, "motivo": ("tiene carta responsiva: el SIPP exige "
                                       "cancelarla para poder reasignar")}

        abrir = await self._primer_visible(
            [page.locator(self.SEL_ACCION_REASIGNAR)],
            "acción «Reasignar Activo Fijo» del listado")
        await self._click_seguro(abrir)
        if fila and fila.get("carta"):
            # El portal pregunta antes de cancelar la carta y luego avisa que la
            # canceló; aquí ya se decidió, así que se aceptan los dos.
            await self.confirmar_aviso_si_hay(5_000)
            await page.wait_for_timeout(1_500)
            await self.confirmar_aviso_si_hay(3_000)
        try:
            await page.locator(self.SEL_EMPLEADO_REASIGNAR).first.wait_for(
                state="visible", timeout=self.TIMEOUT_ELEMENTO)
        except PlaywrightTimeoutError as exc:
            await self._capturar_diagnostico("modal_reasignacion")
            raise ErrorSipp("No se abrió el modal «Reasignar Activo Fijo».") from exc

        await self.seleccionar_empleado(id_empleado, nombre_empleado,
                                        opcion=self.OPCION_EMPLEADO_REASIGNAR)
        # Elegir al empleado dispara la carga por AJAX de su empresa, sucursal y
        # centros de costo: guardar antes de que llegue deja el modal a medias.
        await page.wait_for_timeout(2_500)
        elegido = await page.evaluate(EMPLEADO_REASIGNADO_JS)
        if not (elegido and elegido.get("id")):
            await self._capturar_diagnostico("reasignacion_sin_empleado")
            raise ErrorSipp(
                "El modal de reasignación se quedó sin empleado nuevo; no se guardó.")
        nuevo = elegido.get("nombre") or nombre_empleado

        guardar = await self._primer_visible(
            [page.locator(self.SEL_GUARDAR_REASIGNAR)],
            "botón Guardar de la reasignación")
        await self._click_seguro(guardar)
        await self.confirmar_aviso_si_hay(5_000)      # «¿Estás seguro…?»
        await page.wait_for_timeout(2_500)
        # Lo que el portal conteste se lee ANTES de cerrarlo: si su backend falló,
        # ese diálogo es la única explicación que va a existir.
        fallo = await self.error_visible_del_portal()
        await self.confirmar_aviso_si_hay(3_000)      # cierra el aviso (bueno o malo)
        await page.wait_for_timeout(1_500)
        if fallo:
            return {**base, "empleado_nuevo": nuevo,
                    "error_portal": True,
                    "motivo": f"el SIPP rechazó la reasignación — {fallo}"}

        # Se CONFIRMA contra el listado y no por el aviso del portal: el aviso se
        # va solo a los pocos segundos y un fallo del servidor saldría por otro
        # camino. Si el listado sigue mostrando al anterior, no se reasignó.
        confirmado = ""
        try:
            await self.buscar_en_listado(etiqueta=etiqueta, serie=serie)
            fila2 = await page.evaluate(ESTADO_FILA_LISTADO_JS, (etiqueta or "").strip())
            confirmado = (fila2 or {}).get("empleado", "")
        except Exception:  # noqa: BLE001 — sin confirmación se reporta como duda
            confirmado = ""
        if confirmado and not self._mismo_nombre(confirmado, nuevo):
            return {**base, "empleado_nuevo": nuevo,
                    "motivo": (f"el portal sigue mostrando a «{confirmado}» como "
                               f"resguardante")}
        return {**base, "hecho": True, "empleado_nuevo": nuevo}

    async def error_visible_del_portal(self) -> str:
        """Texto del error que el SIPP esté mostrando, o "" si no hay ninguno.

        El portal no devuelve códigos: cuando su backend falla, pinta un diálogo
        «Error del Sistema» con el detalle (archivo, línea y SQL). Leerlo es la
        diferencia entre reportar «no se guardó» y reportar POR QUÉ, que es lo
        único con lo que alguien puede ir a pedir que lo arreglen.

        Best-effort: si no se puede leer, se devuelve "" y el flujo sigue con su
        propia verificación."""
        try:
            return await self._exigir_pagina().evaluate(ERROR_PORTAL_JS) or ""
        except Exception:  # noqa: BLE001 — sin lectura se sigue sin el detalle
            return ""

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
        """Devuelve el primer elemento VISIBLE de `candidatos` dentro del timeout.
        Lanza ErrorSipp si ninguno aparece.

        Mira TODAS las coincidencias de cada candidato, no solo la primera: el
        portal es una SPA que monta a la vez los formularios de alta, edición,
        reasignación… y repite el mismo `ng-click` en todos. Quedarse con `.first`
        devolvía el del formulario OCULTO —el de arriba en el DOM— y hacía fallar
        el buscador de insumos de la edición con un «no se encontró el botón»
        aunque estuviera ahí, a la vista.

        El barrido se topa a `_MAX_COINCIDENCIAS` porque algún candidato es un
        selector amplio (p. ej. `[ng-click*='insumos']`) que puede casar con
        decenas de nodos, y esto se paga por cada activo del lote."""
        page = self._exigir_pagina()
        limite = (timeout or self.TIMEOUT_ELEMENTO) / 1000
        fin = asyncio.get_event_loop().time() + limite
        while asyncio.get_event_loop().time() < fin:
            for loc in candidatos:
                try:
                    cuantos = min(await loc.count(), self._MAX_COINCIDENCIAS)
                    for i in range(cuantos):
                        elemento = loc.nth(i)
                        if await elemento.is_visible():
                            return elemento
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
