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

import segno


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
