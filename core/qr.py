"""Generación de códigos QR y hojas de etiquetas imprimibles (PDF).

Cada QR codifica un ENLACE corto (URL base configurable + la ETIQUETA del activo).
Al escanearlo, el PWA/API móvil resuelve esa etiqueta y muestra la información del
activo. Aquí solo se generan las etiquetas para imprimir y pegar en los activos.

- QR: `segno` (librería pura de Python, sin dependencias pesadas) -> SVG inline.
- PDF: se arma una hoja HTML con las etiquetas y se imprime a PDF con Chromium
  (Playwright), que ya viene con la herramienta para el RPA. Así no se agregan
  dependencias de PDF.
"""

from __future__ import annotations

import html as _html
import io
import os
import re

import segno
from PIL import Image, ImageDraw, ImageFont


def url_qr(base_url: str, etiqueta: str) -> str:
    """Contenido que codifica el QR: `base_url` + etiqueta. Si no hay base, solo
    la etiqueta (el lector/PWA arma la URL)."""
    etiqueta = (etiqueta or "").strip()
    base = (base_url or "").strip()
    if not base:
        return etiqueta
    # Une base y etiqueta con una sola barra.
    return base.rstrip("/") + "/" + etiqueta.lstrip("/")


# Módulos de blanco alrededor del código. El estándar exige 4: sin ellos el
# lector no distingue dónde empieza el símbolo y falla aunque el QR sea enorme.
_ZONA_SILENCIO = 4


def _codigo(contenido: str):
    """Símbolo QR ESTÁNDAR de `contenido`.

    Se usa `make_qr` y no `make` porque este último devuelve un **Micro QR**
    cuando el dato es corto (una etiqueta lo es), y buena parte de los lectores de
    teléfono no leen ese formato: el usuario ve un código impecable que su celular
    simplemente ignora. El QR estándar lo lee todo el mundo.

    'm' tolera ~15% de daño, que en una etiqueta pegada a un activo (roces, polvo,
    despegues parciales) es lo que la mantiene legible."""
    return segno.make_qr(contenido, error="m")


def qr_svg(contenido: str, scale: int = 4) -> str:
    """SVG inline (sin declaración XML) del QR de `contenido`, listo para HTML."""
    return _codigo(contenido).svg_inline(scale=scale, border=_ZONA_SILENCIO)


# --- Hoja de etiquetas ----------------------------------------------------
_CSS = """
  @page { size: A4; margin: 10mm; }
  * { box-sizing: border-box; }
  body { font-family: Arial, Helvetica, sans-serif; margin: 0; color: #111; }
  .hoja { display: flex; flex-wrap: wrap; gap: 4mm; }
  .etq {
    width: 60mm; height: 40mm; border: 1px solid #bbb; border-radius: 2mm;
    padding: 2mm; display: flex; align-items: center;
    justify-content: center; gap: 2mm;
    page-break-inside: avoid;
  }
  /* El QR es CUADRADO, así que lo limita el ALTO de la etiqueta: por eso crece
     hacia abajo (30 -> 40 mm) y no a lo ancho. El recuadro incluye la zona de
     silencio, que es parte del símbolo y no un margen decorativo.
     `align-items` + `justify-content` centran el QR entre los bordes de la
     etiqueta en los DOS ejes; el `flex: 0 0` impide que se deforme al repartir
     el ancho con el número. */
  .etq .qr { width: 36mm; height: 36mm; flex: 0 0 36mm;
             display: flex; align-items: center; justify-content: center; }
  .etq .qr svg { width: 100%; height: 100%; display: block; }
  /* El número, centrado en la columna que le queda (el mismo trato que el QR). */
  .etq .info { width: 18mm; flex: 0 0 18mm; overflow: hidden;
               text-align: center; }
  /* `nowrap`: el número es UN dato, partirlo en dos renglones lo vuelve dos
     números a la vista. El tamaño lo calcula `_pt_una_linea` por etiqueta, porque
     las hay de 7 y de 10 dígitos y un valor fijo no le sirve a ambas. */
  .etq .num { font-weight: bold; letter-spacing: .5px; white-space: nowrap; }
"""


# Geometría de la etiqueta (mm). Vive aquí y no solo en el CSS porque el tamaño
# del número se calcula a partir del hueco que deja el QR.
_ETQ_ANCHO_MM = 60
_ETQ_PADDING_MM = 2
_QR_MM = 36
_GAP_MM = 2
_NUM_ANCHO_MM = _ETQ_ANCHO_MM - 2 * _ETQ_PADDING_MM - _QR_MM - _GAP_MM
_PT_MAX = 16          # tope: más grande no se ve mejor, solo empuja el salto
_PT_MIN = 6
_AVANCE_DIGITO = 0.556   # ancho de un dígito en Arial bold, en 'em'
_PT_A_MM = 0.3528


def _pt_una_linea(texto: str) -> float:
    """Tamaño de fuente (pt) con el que `texto` entra en UNA línea.

    Se calcula en vez de fijarlo porque las etiquetas no miden todas lo mismo
    (7 dígitos en unas empresas, 10 en otras) y un tamaño único obligaría a elegir
    entre partir las largas o achicar las cortas sin necesidad. El 0.95 es holgura
    para el interletraje y el redondeo del render.

    El piso de `_PT_MIN` cubre hasta ~15 dígitos; más allá el número se recortaría
    (las etiquetas reales del SIPP traen entre 7 y 10)."""
    n = max(1, len(texto))
    cabe = _NUM_ANCHO_MM / (_AVANCE_DIGITO * n * _PT_A_MM)
    return round(max(_PT_MIN, min(_PT_MAX, cabe * 0.95)), 1)


def _label(activo: dict, base_url: str) -> str:
    etiqueta = str(activo.get("etiqueta") or "").strip()
    svg = qr_svg(url_qr(base_url, etiqueta))
    return (
        f'<div class="etq"><div class="qr">{svg}</div>'
        f'<div class="info"><div class="num" '
        f'style="font-size:{_pt_una_linea(etiqueta)}pt">'
        f'{_html.escape(etiqueta)}</div></div></div>')


def construir_html_etiquetas(activos: list[dict], base_url: str = "",
                             titulo: str = "Etiquetas de activos") -> str:
    """Arma la hoja HTML con una etiqueta (QR + número) por activo. Cada dict
    necesita solo `etiqueta`: el insumo y la empresa se quitaron del formato a
    pedido de operación (se identifican al escanear)."""
    etiquetas = "".join(_label(a, base_url) for a in activos if a.get("etiqueta"))
    return (f'<!doctype html><html><head><meta charset="utf-8">'
            f'<title>{_html.escape(titulo)}</title><style>{_CSS}</style></head>'
            f'<body><div class="hoja">{etiquetas}</div></body></html>')


async def html_a_pdf(html_str: str, ruta_pdf: str) -> None:
    """Renderiza `html_str` y lo imprime a PDF (A4) con Chromium (Playwright)."""
    from playwright.async_api import async_playwright

    from core.rpa_sipp import asegurar_navegador

    await asegurar_navegador()  # descarga Chromium en la app empaquetada si falta
    async with async_playwright() as p:
        # Mismo motivo que en core/rpa_sipp.SesionSipp.iniciar: sin `channel` el
        # headless exige el binario chrome-headless-shell, que la app no descarga.
        navegador = await p.chromium.launch(headless=True, channel="chromium")
        try:
            pagina = await navegador.new_page()
            await pagina.set_content(html_str, wait_until="load")
            await pagina.pdf(
                path=ruta_pdf, format="A4", print_background=True,
                margin={"top": "10mm", "bottom": "10mm", "left": "8mm", "right": "8mm"})
        finally:
            await navegador.close()


async def generar_pdf_etiquetas(activos: list[dict], ruta_pdf: str,
                                base_url: str = "") -> int:
    """Genera el PDF de etiquetas para `activos` (los que tengan etiqueta) en
    `ruta_pdf`. Devuelve cuántas etiquetas se generaron."""
    con_etiqueta = [a for a in activos if (a.get("etiqueta") or "").strip()]
    if not con_etiqueta:
        return 0
    await html_a_pdf(construir_html_etiquetas(con_etiqueta, base_url), ruta_pdf)
    return len(con_etiqueta)


# --- QR individuales en PNG organizados por departamento ------------------
_SIN_DEPTO = "SIN DEPARTAMENTO"
_SIN_SUC = "SIN SUCURSAL"


def _sanear_nombre(texto: str) -> str:
    """Nombre válido para archivo/carpeta en Windows (sin < > : " / \\ | ? *)."""
    limpio = re.sub(r'[<>:"/\\|?*\r\n\t]', "", str(texto or "")).strip().rstrip(".")
    return limpio or "SIN NOMBRE"


def png_etiqueta(activo: dict, base_url: str = "", escala: int = 10) -> bytes:
    """PNG de un QR con la ETIQUETA impresa debajo — para que al pegarla/imprimirla
    se vea el número junto al QR."""
    etiqueta = str(activo.get("etiqueta") or "").strip()

    # QR como PNG en memoria.
    buf = io.BytesIO()
    _codigo(url_qr(base_url, etiqueta)).save(
        buf, kind="png", scale=escala, border=_ZONA_SILENCIO)
    buf.seek(0)
    qr_img = Image.open(buf).convert("RGB")
    w = qr_img.width

    # Lienzo: QR arriba + franja de texto abajo (solo la etiqueta).
    alto_txt = int(w * 0.22)
    lienzo = Image.new("RGB", (w, w + alto_txt), "white")
    lienzo.paste(qr_img, (0, 0))
    dib = ImageDraw.Draw(lienzo)

    f_etq = ImageFont.load_default(size=max(18, int(w * 0.12)))
    ancho = dib.textlength(etiqueta, font=f_etq)
    dib.text(((w - ancho) / 2, w + int(w * 0.03)), etiqueta, fill="black", font=f_etq)

    salida = io.BytesIO()
    lienzo.save(salida, format="PNG")
    return salida.getvalue()


def generar_carpeta_por_departamento(activos: list[dict], carpeta_raiz: str,
                                     base_url: str = "", progreso=None,
                                     por_sucursal: bool = False) -> dict:
    """Genera un PNG por activo (QR + etiqueta) dentro de `carpeta_raiz`, en
    subcarpetas por DEPARTAMENTO. Cada archivo se nombra con la etiqueta.

    Si `por_sucursal` es True, se antepone un nivel por SUCURSAL:
    `raíz / Sucursal / Departamento / etiqueta.png` (para cuando se generan todas
    las sucursales juntas). Si es False: `raíz / Departamento / etiqueta.png`.

    `progreso(hechos, total)`: callback opcional. Devuelve
    {generados, departamentos, sucursales}."""
    con_etiqueta = [a for a in activos if (a.get("etiqueta") or "").strip()]
    total = len(con_etiqueta)
    os.makedirs(carpeta_raiz, exist_ok=True)
    departamentos, sucursales, generados = set(), set(), 0
    usados: set[str] = set()  # rutas ya escritas (evita pisar etiquetas repetidas)
    for a in con_etiqueta:
        depto = _sanear_nombre(a.get("departamento") or _SIN_DEPTO)
        if por_sucursal:
            suc = _sanear_nombre(a.get("sucursal") or _SIN_SUC)
            sucursales.add(suc)
            carpeta = os.path.join(carpeta_raiz, suc, depto)
        else:
            carpeta = os.path.join(carpeta_raiz, depto)
        os.makedirs(carpeta, exist_ok=True)
        departamentos.add(depto)
        base_arch = _sanear_nombre(a.get("etiqueta"))
        ruta = os.path.join(carpeta, base_arch + ".png")
        n = 2
        while ruta.lower() in usados:
            ruta = os.path.join(carpeta, f"{base_arch} ({n}).png")
            n += 1
        usados.add(ruta.lower())
        with open(ruta, "wb") as fh:
            fh.write(png_etiqueta(a, base_url))
        generados += 1
        if progreso and (generados % 25 == 0 or generados == total):
            progreso(generados, total)
    return {"generados": generados, "departamentos": len(departamentos),
            "sucursales": len(sucursales)}
