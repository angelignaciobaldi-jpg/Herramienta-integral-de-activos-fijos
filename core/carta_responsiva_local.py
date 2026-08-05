"""Generación LOCAL de la carta responsiva (PDF), replicando el formato del SIPP.

El generador de cartas del propio SIPP (`ActivosFijosNuevo.cartaResponsiva`) está
roto en todos los ambientes (su backend exige `activosFijos` como string pero su JS
envía un array, y el motor colapsa mayúsculas: imposible llamarlo bien). Por eso la
herramienta arma la carta localmente con el MISMO formato del documento oficial
(3 páginas: texto legal + folio, ANEXO A con el inventario, y página legal +
observaciones + firma) y la imprime a PDF con Chromium.

Datos que se toman del SIPP (vía cfproxy, con una `SesionSipp` ya logueada):
  - Puesto del empleado -> ActivosFijosNuevo.listarEmpleadosActivos (NB_PUESTO).
  - Características por activo (Marca/Modelo/Cliente…) -> ActivosFijosNuevo.
        obtenerCamposDetalle {id_Empresa, id_ActivoFijo}.
El resto (serie, insumo, etiqueta, empresa, sucursal, departamento) viene en los
`ActivoCarta` que la pantalla ya trae de getActivosFijosPorEmpleado.

FOLIO: el SIPP no expone el consecutivo (getFoliosCartasResponsivas viene vacío),
así que se usa un contador LOCAL configurable (ver ui/cartas_responsivas).
"""

from __future__ import annotations

import html
import json
from datetime import date
from pathlib import Path

_RUTA_PROXY = "/componentes/cfproxy.cfc?method=proxy"

_MESES = ("Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio",
          "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre")

# Cláusulas (numeradas) de la página 1 — texto oficial del documento.
_CLAUSULAS = [
    "El INSTRUMENTO DE TRABAJO es propiedad de la empresa {empresa}, y lo utilizaré "
    "por el tiempo que dure mi relación laboral con la empresa, por lo que en caso de "
    "que dicha relación se rescinda o termine anticipadamente, me obligaré a "
    "devolverlo a la empresa.",
    "El INSTRUMENTO DE TRABAJO lo recibí en las condiciones en que se encuentra y me "
    "comprometo a mantenerlo en el mismo estado y a responsabilizarme de cualquier "
    "daño o perjuicio que en su uso le causare por dolo o negligencia, lo cual, de no "
    "ser así, estoy consciente de que esto traería como consecuencia la rescisión de "
    "mi contrato conforme a lo establecido en el artículo 47 fracción V y VI de la Ley "
    "Federal del Trabajo. En relación a cómo se realizará el descuento, no mencionar el "
    "artículo por la razón de que en este se establece que solo se podrá descontar el "
    "30% del excedente del salario mínimo.",
    "El INSTRUMENTO DE TRABAJO lo devolveré en el momento en que la empresa lo "
    "requiera, entregándose en el mismo estado físico y/o mecánico con que lo recibí.",
    "Por estar el INSTRUMENTO DE TRABAJO bajo mi responsabilidad y resguardo, asumo "
    "todas las obligaciones de tipo civil o penal, que pudieran presentarse del mal uso "
    "dado por mi persona a la herramienta de trabajo citada en este documento, durante "
    "el tiempo en que dure mi relación laboral.",
    "Me comprometo a dar al INSTRUMENTO DE TRABAJO el mantenimiento preventivo y/o "
    "correctivo que necesitare y me comprometo expresamente a informar de inmediato a "
    "la empresa, para la autorización de la reparación y los pasos a seguir para la "
    "atención del mismo.",
    "El INSTRUMENTO DE TRABAJO lo utilizaré en mis labores diarias en la empresa, de "
    "lunes a sábado o los días que me sea asignado, desde la hora de salida hasta la "
    "hora de entrada del día inmediato siguiente y así sucesivamente durante todos los "
    "días hábiles e inhábiles de la semana.",
    "Me someto a la opinión que emita la Comisión Mixta de Productividad, Capacitación "
    "y Adiestramiento quien tendrá facultades desde los estatutos, para valorar el "
    "desempeño y verificar que esté cumpliendo con las condiciones antes señaladas.",
    "Para realizar un descuento al salario en caso de error, perdida, averío, daño y/o "
    "cualquier otra situación análoga de conformidad con el artículo 110 fracción I de "
    "la Ley Federal del Trabajo, podrá someterse a investigación a través de la Comisión "
    "Mixta de Productividad, Capacitación y Adiestramiento, para determinar si el "
    "trabajador es responsable y efectuarse el descuento correspondiente.",
    "Si la relación laboral culmina por cualquier causa, EL TRABAJADOR autoriza a LA "
    "EMPRESA a compensar la deuda mencionada en el párrafo anterior con el pago de su "
    "finiquito, si la cantidad no fuera suficiente para cumplir con la obligación, LA "
    "EMPRESA podrá exigir al TRABAJADOR por medio de los Juzgados Civiles del Fuero "
    "Común el cumplimiento de esta deuda, de conformidad a lo establecido en el "
    "artículo 32 de la Ley Federal del Trabajo.",
]

# Párrafos de la página 3 (texto legal + observaciones + firma).
_PARRAFOS_P3 = [
    "En base al artículo 110 fracción I, de la Ley federal el Trabajo, se faculta al "
    "patrón a exigir el pago de deudas contraídas con motivo de errores, pérdidas, "
    "averías o adquisición de artículos producidos por la empresa, esto, en razón de "
    "que son herramientas necesarias de trabajo para el íntegro funcionamiento de la "
    "empresa, y de las cuales, se obliga a conservar el producto o material de trabajo "
    "en el estado que se entrega, exceptuando por el deterioro natural generado por el "
    "uso. Dicho pago se llevará a cabo de acuerdo a las necesidades del patrón. Este "
    "documento está intimamente ligado y vinculado a la carta responsiva del mismo "
    "folio que este documento, así como al Reglamento Interior de Trabajo y a las "
    "Comisones Mixtas que tengan facultades de resolver sobre temas de herramientas de "
    "trabajo.",
    "En caso de que la relación laboral culmine antes de liquidar el adeudo derivado de "
    "las causales del artículo 110 de la Ley Federal del Trabajo, el patrón está "
    "facultado para el descuento por medio del pago de prestaciones laborales "
    "(finiquito); en caso contrario, de no solventar el adeudo con dicha prestación, de "
    "conformidad con el artículo 32 de la Ley Federal del Trabajo, recaerá en "
    "responsabilidad civil.",
    "En cuanto a equipo de cómputo, celulares o equipos electrónicos de uso personal, "
    "en el acto se hace saber que la empresa le cobrará a usted el 100% del costo en "
    "que se incurra, derivado de la reparación por fallas originadas por mal uso del "
    "equipo o daños por negligencia o en caso de que se suscite robo o pérdida del "
    "equipo.",
    "Se acuerda también que en cualquier momento que así disponga la empresa, el equipo "
    "de cómputo y/o periféricos que se describen en el presente escrito, le serán "
    "requeridos para su devolución.",
]


class ErrorCartaLocal(Exception):
    """Falla al reunir datos o generar la carta responsiva local."""


def _e(texto) -> str:
    """Escapa para HTML."""
    return html.escape(str(texto or ""))


def fecha_larga(hoy: "date | None" = None) -> str:
    hoy = hoy or date.today()
    return f"{hoy.day} de {_MESES[hoy.month - 1]} del {hoy.year}."


async def _invoke(sesion, component: str, metodo: str, args: dict) -> dict:
    payload = json.dumps({"component": component, "execMethod": metodo,
                          "argumentcollection": args})
    resp = await sesion.context.request.post(
        sesion.BASE_URL + _RUTA_PROXY, data=payload,
        headers={"Content-Type": "application/json"})
    return await resp.json()


async def _puesto_empleado(sesion, id_empleado) -> str:
    if not id_empleado:
        return ""
    try:
        datos = await _invoke(sesion, "ActivosFijosNuevo", "listarEmpleadosActivos",
                              {"id_Empleado": id_empleado, "nb_empleado": ""})
        q = datos.get("QUERY", {}) or {}
        cols = {c: i for i, c in enumerate(q.get("COLUMNS") or [])}
        filas = q.get("DATA") or []
        if filas and "NB_PUESTO" in cols:
            return str(filas[0][cols["NB_PUESTO"]] or "")
    except Exception:  # noqa: BLE001 — el puesto es opcional
        pass
    return ""


async def _caracteristicas(sesion, id_empresa, id_activo) -> list[str]:
    """Lista 'CAMPO:VALOR' (Marca/Modelo/Cliente…) del activo (obtenerCamposDetalle)."""
    if id_empresa is None or id_activo is None:
        return []
    try:
        datos = await _invoke(sesion, "ActivosFijosNuevo", "obtenerCamposDetalle",
                              {"id_Empresa": id_empresa, "id_ActivoFijo": id_activo})
        q = datos.get("QUERY", {}) or {}
        cols = {c: i for i, c in enumerate(q.get("COLUMNS") or [])}
        nb, de = cols.get("NB_CAMPODETALLE"), cols.get("DE_VALORCAMPODETALLE")
        lineas = []
        for f in q.get("DATA") or []:
            nombre = str(f[nb] or "").strip() if nb is not None else ""
            valor = str(f[de] or "").strip() if de is not None else ""
            if nombre and valor:
                lineas.append(f"{nombre.upper()}:{valor}")
        return lineas
    except Exception:  # noqa: BLE001 — las características son opcionales
        return []


def construir_html(folio: str, trabajador: str, puesto: str, empresa: str,
                   area: str, sucursal: str, filas: list[dict],
                   hoy: "date | None" = None) -> str:
    """Arma el HTML de la carta responsiva (3 páginas) con el formato oficial."""
    emp = _e(empresa)
    trab = _e(trabajador)

    def _clausula(texto: str) -> str:
        # Escapa y resalta el término clave del documento.
        return _e(texto.format(empresa=empresa)).replace(
            "INSTRUMENTO DE TRABAJO", "<b>INSTRUMENTO DE TRABAJO</b>")

    clausulas = "".join(f"<li>{_clausula(c)}</li>" for c in _CLAUSULAS)

    filas_html = ""
    for f in filas:
        carac = "<br>".join(_e(x) for x in f.get("caracteristicas", []))
        filas_html += (
            "<tr>"
            f"<td>{_e(f.get('serie'))}</td>"
            f"<td>{_e(f.get('descripcion'))}</td>"
            f"<td>{_e(f.get('etiqueta'))}</td>"
            f"<td>{_e(f.get('ubicacion'))}</td>"
            f"<td class='car'>{carac}</td>"
            "</tr>")

    parrafos_p3 = "".join(f"<p>{_e(p)}</p>" for p in _PARRAFOS_P3)
    lineas_obs = "".join(
        "<div class='obs'>" + "_ " * 60 + "</div>" for _ in range(5))

    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<style>
  @page {{ size: A4; margin: 14mm 16mm; }}
  body {{ font-family: 'Times New Roman', Georgia, serif; font-size: 11px;
          color: #000; line-height: 1.35; }}
  .pagina {{ page-break-after: always; }}
  .pagina:last-child {{ page-break-after: auto; }}
  .fecha {{ text-align: right; margin-bottom: 6px; }}
  h1 {{ text-align: center; font-size: 13px; margin: 0; }}
  .folio {{ text-align: center; font-weight: bold; margin: 0 0 14px; }}
  p {{ text-align: justify; margin: 8px 0; }}
  ol {{ padding-left: 22px; }}
  ol li {{ text-align: justify; margin: 6px 0; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 6px; }}
  th, td {{ border: 1px solid #000; padding: 4px 6px; font-size: 10px;
            vertical-align: top; }}
  th {{ background: #eee; text-align: center; }}
  .firma-tabla td {{ height: 42px; }}
  .datos td {{ font-size: 10px; }}
  .car {{ white-space: pre-line; }}
  .obs {{ letter-spacing: 1px; color: #444; margin: 6px 0; }}
  .firma {{ margin-top: 60px; text-align: center; }}
  .firma .linea {{ width: 320px; border-top: 1px solid #000; margin: 0 auto 4px; }}
  .pie {{ text-align: center; margin-top: 18px; color: #333; }}
</style></head><body>

<div class="pagina">
  <div class="fecha">{_e(fecha_larga(hoy))}</div>
  <h1>CARTA RESPONSIVA</h1>
  <div class="folio">Folio: {_e(folio)}</div>
  <p><b>{trab}</b>, en mi carácter de <b>TRABAJADOR</b> de la empresa <b>{emp}</b>
  en adelante <b>LA EMPRESA</b>, quien desempeña el puesto de <b>{_e(puesto)}</b>, a
  través del presente documento expresamente hago constar que recibí de parte del
  patrón, como herramienta de trabajo y para su uso y resguardo, a partir de la fecha
  consignada en este documento y hasta la fecha de terminación de nuestra relación
  laboral, <b>los articulos</b> que en adelante se le denominará como <b>INSTRUMENTO
  DE TRABAJO</b>, que se describe en el anexo A de este documento.</p>
  <p>Manifiesto expresamente que el <b>INSTRUMENTO DE TRABAJO</b> que me fue asignado
  lo utilizaré para la realización de las actividades propias de mi puesto y área de
  la que soy responsable en la empresa, por lo que de acuerdo al artículo 135 fracción
  IX de la Ley Federal del Trabajo, me obligo expresamente a usarlo con las condiciones
  que se manifiestan a continuación:</p>
  <ol>{clausulas}</ol>
  <table class="firma-tabla">
    <tr><th>Trabajador</th><th>Nombre en manuscrito</th><th>Firma</th></tr>
    <tr><td><b>{trab}</b></td><td></td><td></td></tr>
  </table>
  <div class="pie">Página 1 de 3</div>
</div>

<div class="pagina">
  <div style="text-align:right;font-weight:bold;">ANEXO A.</div>
  <h1>INVENTARIO DE HERRAMIENTA</h1>
  <p>El presente documento tiene como finalidad realizar el inventario de las
  herramientas de trabajo propiedad de <b>{emp}</b> que utilizará el trabajador durante
  su jornada laboral y exclusivamente para el desempeño de sus actividades laborales.</p>
  <table class="datos">
    <tr>
      <td><b>Trabajador:</b></td><td>{trab}</td>
      <td><b>Área de trabajo:</b></td><td>{_e(area)}</td>
      <td rowspan="2" style="text-align:center;"><b>Sucursal</b><br>{_e(sucursal)}</td>
    </tr>
    <tr>
      <td><b>Puesto:</b></td><td>{_e(puesto)}</td>
      <td><b>Empresa:</b></td><td>{emp}</td>
    </tr>
  </table>
  <p style="margin-top:10px;"><b>Descripción</b></p>
  <table>
    <tr><th>Serie</th><th>Descripción</th><th>Etiqueta</th><th>Ubicación</th>
        <th>Características</th></tr>
    {filas_html}
  </table>
  <div class="pie">Página 2 de 3</div>
</div>

<div class="pagina">
  {parrafos_p3}
  <p style="margin-top:14px;">Observaciones:</p>
  {lineas_obs}
  <div class="firma">
    <div class="linea"></div>
    FIRMA DE RESPONSABLE<br>{trab}
  </div>
  <div class="pie">Página 3 de 3</div>
</div>

</body></html>"""


async def generar_carta_local(sesion, activos: list, ruta_pdf: str, folio: str,
                              nombre_empleado: str = "", id_empleado=None,
                              id_empresa=None) -> str:
    """Reúne los datos del SIPP, arma el HTML y lo imprime a PDF en `ruta_pdf`.
    `activos`: lista de core.cartas_responsivas.ActivoCarta seleccionados.
    `id_empresa`: la empresa elegida (para consultar las características por activo)."""
    if not activos:
        raise ErrorCartaLocal("Selecciona al menos un activo para la carta.")
    from core import qr

    primero = activos[0]
    trabajador = nombre_empleado or primero.empleado or ""
    empresa = primero.empresa or ""
    sucursal = primero.sucursal or ""
    area = next((a.departamento for a in activos if a.departamento), "")
    puesto = await _puesto_empleado(sesion, id_empleado)

    filas = []
    for a in activos:
        carac = await _caracteristicas(sesion, id_empresa, a.id_activo)
        # CLIENTE = centro de costo (como en el documento oficial); si obtenerCamposDetalle
        # ya trae un "CLIENTE", se respeta; si no, se antepone el centro de costo.
        if a.centro_cc and not any(x.upper().startswith("CLIENTE:") for x in carac):
            carac = [f"CLIENTE:{a.centro_cc}"] + carac
        filas.append({
            "serie": a.serie, "descripcion": a.nombre, "etiqueta": a.etiqueta,
            "ubicacion": a.departamento or area, "caracteristicas": carac})

    html_str = construir_html(folio, trabajador, puesto, empresa, area, sucursal, filas)
    ruta = Path(ruta_pdf)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    await qr.html_a_pdf(html_str, str(ruta))
    return str(ruta)
