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


def qr_svg(contenido: str, scale: int = 4) -> str:
    """SVG inline (sin declaración XML) del QR de `contenido`, listo para HTML."""
    q = segno.make(contenido, error="m")  # 'm' tolera ~15% de daño en la etiqueta
    return q.svg_inline(scale=scale, border=0)


# --- Hoja de etiquetas ----------------------------------------------------
_CSS = """
  @page { size: A4; margin: 10mm; }
  * { box-sizing: border-box; }
  body { font-family: Arial, Helvetica, sans-serif; margin: 0; color: #111; }
  .hoja { display: flex; flex-wrap: wrap; gap: 4mm; }
  .etq {
    width: 60mm; height: 30mm; border: 1px solid #bbb; border-radius: 2mm;
    padding: 2.5mm; display: flex; align-items: center; gap: 2.5mm;
    page-break-inside: avoid;
  }
  .etq .qr { width: 25mm; height: 25mm; flex: 0 0 25mm; }
  .etq .qr svg { width: 100%; height: 100%; }
  .etq .info { overflow: hidden; }
  .etq .num { font-size: 12pt; font-weight: bold; letter-spacing: .3px; }
  .etq .ins { font-size: 8pt; color: #333; margin-top: 1mm;
              display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical;
              overflow: hidden; }
  .etq .emp { font-size: 7pt; color: #777; margin-top: 1mm; }
"""


def _label(activo: dict, base_url: str) -> str:
    etiqueta = str(activo.get("etiqueta") or "").strip()
    insumo = _html.escape(str(activo.get("insumo") or ""))
    empresa = _html.escape(str(activo.get("empresa") or ""))
    svg = qr_svg(url_qr(base_url, etiqueta))
    return (
        f'<div class="etq"><div class="qr">{svg}</div>'
        f'<div class="info"><div class="num">{_html.escape(etiqueta)}</div>'
        f'<div class="ins">{insumo}</div>'
        f'<div class="emp">{empresa}</div></div></div>')


def construir_html_etiquetas(activos: list[dict], base_url: str = "",
                             titulo: str = "Etiquetas de activos") -> str:
    """Arma la hoja HTML con una etiqueta (QR + datos) por activo. Cada dict:
    etiqueta (obligatorio), insumo, empresa."""
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
        navegador = await p.chromium.launch(headless=True)
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


def _sanear_nombre(texto: str) -> str:
    """Nombre válido para archivo/carpeta en Windows (sin < > : " / \\ | ? *)."""
    limpio = re.sub(r'[<>:"/\\|?*\r\n\t]', "", str(texto or "")).strip().rstrip(".")
    return limpio or "SIN NOMBRE"


def png_etiqueta(activo: dict, base_url: str = "", escala: int = 10) -> bytes:
    """PNG de un QR con la ETIQUETA (y la serie, si hay) impresa debajo — para que
    al pegarla/imprimirla se vea el número junto al QR."""
    etiqueta = str(activo.get("etiqueta") or "").strip()
    serie = str(activo.get("serie") or "").strip()

    # QR como PNG en memoria.
    buf = io.BytesIO()
    segno.make(url_qr(base_url, etiqueta), error="m").save(
        buf, kind="png", scale=escala, border=2)
    buf.seek(0)
    qr_img = Image.open(buf).convert("RGB")
    w = qr_img.width

    # Lienzo: QR arriba + franja de texto abajo.
    alto_txt = int(w * 0.22) + (int(w * 0.11) if serie else 0)
    lienzo = Image.new("RGB", (w, w + alto_txt), "white")
    lienzo.paste(qr_img, (0, 0))
    dib = ImageDraw.Draw(lienzo)

    f_etq = ImageFont.load_default(size=max(18, int(w * 0.12)))
    f_ser = ImageFont.load_default(size=max(14, int(w * 0.075)))

    def centrar(texto, fuente, y):
        ancho = dib.textlength(texto, font=fuente)
        dib.text(((w - ancho) / 2, y), texto, fill="black", font=fuente)

    y = w + int(w * 0.03)
    centrar(etiqueta, f_etq, y)
    if serie:
        centrar(f"Serie: {serie}", f_ser, y + int(w * 0.14))

    salida = io.BytesIO()
    lienzo.save(salida, format="PNG")
    return salida.getvalue()


def generar_carpeta_por_departamento(activos: list[dict], carpeta_raiz: str,
                                     base_url: str = "", progreso=None) -> dict:
    """Genera un PNG por activo (QR + etiqueta) dentro de `carpeta_raiz`, en
    subcarpetas por DEPARTAMENTO. Cada archivo se nombra con la etiqueta.

    `progreso(hechos, total)`: callback opcional. Devuelve
    {generados, departamentos}."""
    con_etiqueta = [a for a in activos if (a.get("etiqueta") or "").strip()]
    total = len(con_etiqueta)
    os.makedirs(carpeta_raiz, exist_ok=True)
    departamentos, generados = set(), 0
    usados: set[str] = set()  # rutas ya escritas (evita pisar etiquetas repetidas)
    for a in con_etiqueta:
        depto = _sanear_nombre(a.get("departamento") or _SIN_DEPTO)
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
    return {"generados": generados, "departamentos": len(departamentos)}
