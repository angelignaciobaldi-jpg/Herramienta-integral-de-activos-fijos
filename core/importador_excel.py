"""Carga masiva de inventarios de activos fijos desde Excel.

Pensado para los levantamientos que el área ya tiene capturados en hojas de
cálculo (p. ej. el inventario de la clínica Oceánica), donde cada hoja es un área
y cada fila un activo (o VARIOS, ver abajo).

Estructura esperada de cada hoja (las columnas se detectan por su encabezado, sin
importar en qué fila empiece ni el orden):

    INSUMO · CANTIDAD · ETIQUETA · SERIE · RESPONSABLE · UBICACIÓN

Dos particularidades del formato real que este módulo resuelve:

1. **Una fila puede ser varios activos.** Cuando CANTIDAD > 1, la celda ETIQUETA
   trae todas las etiquetas juntas ("0048399/0048400/0048401", a veces separadas
   por saltos de línea). Cada etiqueta es un activo distinto en el SIPP, así que
   la fila se EXPANDE en un registro por etiqueta.
2. **La mayoría de los activos no tiene número de serie** (en el inventario real,
   ~82%). El identificador es la ETIQUETA (número de inventario); la serie queda
   como dato opcional (ver core/db.clave_levantamiento).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import openpyxl

from . import db

# Encabezados que se buscan (en MAYÚSCULAS, sin acentos) -> campo interno.
# Las sábanas estandarizadas traen EMPRESA, SUCURSAL, ORIGEN (sitio) y AREA (área),
# que se usan por fila para autollenar el registro (ver _importar_hoja).
_ENCABEZADOS = {
    "INSUMO": "insumo",
    "CANTIDAD": "cantidad",
    "ETIQUETA": "etiqueta",
    "SERIE": "serie",
    "RESPONSABLE": "responsable",
    "UBICACION": "ubicacion",
    "EMPRESA": "empresa",
    "SUCURSAL": "sucursal",
    "ORIGEN": "origen",
    "AREA": "area",
}
# Cuántos encabezados deben coincidir para dar una fila por "fila de encabezados".
_MIN_COINCIDENCIAS = 3
# Hasta qué fila se busca el encabezado (las hojas reales lo tienen entre la 6 y la 12).
_MAX_FILA_ENCABEZADO = 25
# Separadores con los que vienen varias etiquetas/series en una misma celda.
_RE_SEPARADORES = re.compile(r"[\/\n\r,;]+")


def _norm(texto) -> str:
    """Normaliza un encabezado: mayúsculas, sin acentos ni espacios sobrantes."""
    if texto is None:
        return ""
    t = str(texto).strip().upper()
    for a, b in (("Á", "A"), ("É", "E"), ("Í", "I"), ("Ó", "O"), ("Ú", "U"), ("Ñ", "N")):
        t = t.replace(a, b)
    return re.sub(r"\s+", " ", t)


def _partes(celda) -> list[str]:
    """Separa una celda que puede traer varios valores ('0048399/0048400')."""
    if celda is None:
        return []
    # Los números de etiqueta suelen venir como número: se evita el '.0' del float.
    if isinstance(celda, float) and celda.is_integer():
        celda = int(celda)
    return [p.strip() for p in _RE_SEPARADORES.split(str(celda)) if p.strip()]


@dataclass
class HojaDetectada:
    """Resultado del análisis de una hoja del archivo."""

    nombre: str
    fila_encabezado: int
    columnas: dict            # campo interno -> índice de columna (1-based)
    filas_datos: int          # filas con INSUMO
    activos_estimados: int    # tras expandir las etiquetas múltiples
    importable: bool = True
    motivo: str = ""


@dataclass
class ResultadoImportacion:
    """Estadísticas de una importación."""

    agregados: int = 0
    duplicados: int = 0
    sin_etiqueta: int = 0
    filas_leidas: int = 0
    errores: list = field(default_factory=list)


# Encabezados de la PLANTILLA de carga masiva (orden y nombres canónicos que el
# detector reconoce). El TIPO de activo NO va: se asigna después en la herramienta.
PLANTILLA_ENCABEZADOS = ["INSUMO", "CANTIDAD", "ETIQUETA", "SERIE", "RESPONSABLE",
                         "EMPRESA", "SUCURSAL", "UBICACION"]


def generar_plantilla(ruta: str) -> str:
    """Crea en `ruta` un Excel plantilla de carga masiva: hoja «Activos» con los
    encabezados y una de «Instrucciones». Devuelve la ruta escrita.

    Se deja SIN filas de datos para no importar ejemplos por error; el formato y el
    truco de varias etiquetas por fila se explican en la hoja de instrucciones."""
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Activos"
    ws.append(PLANTILLA_ENCABEZADOS)
    for celda in ws[1]:
        celda.font = Font(bold=True, color="FFFFFF")
        celda.fill = PatternFill("solid", fgColor="1F3A5F")
        celda.alignment = Alignment(horizontal="center", vertical="center")
    anchos = [34, 10, 24, 22, 30, 22, 22, 26]
    for i, ancho in enumerate(anchos, 1):
        ws.column_dimensions[get_column_letter(i)].width = ancho
    ws.freeze_panes = "A2"

    ins = wb.create_sheet("Instrucciones")
    ins.column_dimensions["A"].width = 100
    guia = [
        "CARGA MASIVA DE ACTIVOS — INSTRUCCIONES",
        "",
        "Captura un activo por fila en la hoja «Activos». Columnas:",
        "• INSUMO (obligatorio): nombre del insumo tal como está en el SIPP.",
        "• CANTIDAD: cuántas piezas iguales. Si es más de 1, pon todas sus ETIQUETAS "
        "en la misma celda separadas por «/» (ej. 0048399/0048400/0048401): cada "
        "etiqueta se registra como un activo.",
        "• ETIQUETA: número(s) de inventario. Es el identificador principal.",
        "• SERIE: número de serie (opcional). Si hay varias, sepáralas por «/» en el "
        "mismo orden que las etiquetas.",
        "• RESPONSABLE: empleado resguardante.",
        "• EMPRESA y SUCURSAL: si las dejas vacías, se usan las del selector de la "
        "pantalla al importar.",
        "• UBICACION: ubicación física del activo.",
        "",
        "El TIPO de activo y el centro de costo se asignan después en la herramienta "
        "(al capturar cada activo), no en esta plantilla.",
        "No cambies los nombres de los encabezados de la hoja «Activos».",
    ]
    for i, linea in enumerate(guia, 1):
        ins.cell(row=i, column=1, value=linea)
    ins["A1"].font = Font(bold=True, size=13)

    wb.save(ruta)
    return ruta


def analizar(ruta: str) -> list[HojaDetectada]:
    """Analiza el archivo y devuelve qué hojas se pueden importar y cuántos
    activos saldrían de cada una (ya expandidos). No modifica nada."""
    wb = openpyxl.load_workbook(ruta, data_only=True, read_only=True)
    try:
        return [_analizar_hoja(wb[nombre]) for nombre in wb.sheetnames]
    finally:
        wb.close()


def _analizar_hoja(ws) -> HojaDetectada:
    fila_hdr, columnas = _detectar_encabezado(ws)
    if not columnas or "insumo" not in columnas:
        return HojaDetectada(
            ws.title, 0, {}, 0, 0, importable=False,
            motivo="No se encontraron los encabezados esperados (INSUMO, ETIQUETA…).")
    filas = activos = 0
    for valores in _filas_datos(ws, fila_hdr, columnas):
        filas += 1
        activos += max(1, len(_partes(valores.get("etiqueta"))))
    return HojaDetectada(ws.title, fila_hdr, columnas, filas, activos)


def _detectar_encabezado(ws) -> tuple[int, dict]:
    """Busca la fila de encabezados y mapea campo interno -> índice de columna."""
    for i, fila in enumerate(ws.iter_rows(min_row=1, max_row=_MAX_FILA_ENCABEZADO,
                                          values_only=True), 1):
        columnas = {}
        for j, celda in enumerate(fila, 1):
            texto = _norm(celda)
            if not texto:
                continue
            for clave, campo in _ENCABEZADOS.items():
                # 'startswith' tolera variantes como "N° SERIE" o "UBICACION ".
                if campo not in columnas and (texto == clave or clave in texto):
                    columnas[campo] = j
                    break
        if len(columnas) >= _MIN_COINCIDENCIAS and "insumo" in columnas:
            return i, columnas
    return 0, {}


def _filas_datos(ws, fila_hdr: int, columnas: dict):
    """Itera las filas con dato, ya mapeadas a {campo: valor}."""
    for fila in ws.iter_rows(min_row=fila_hdr + 1, max_row=ws.max_row, values_only=True):
        insumo = fila[columnas["insumo"] - 1] if columnas["insumo"] - 1 < len(fila) else None
        if insumo is None or not str(insumo).strip():
            continue
        yield {campo: (fila[idx - 1] if idx - 1 < len(fila) else None)
               for campo, idx in columnas.items()}


def importar(ruta: str, hojas: list[str], empresa: str = "", sucursal: str = "",
             departamento: str = "", progreso=None) -> ResultadoImportacion:
    """Importa las `hojas` indicadas del archivo al levantamiento.

    Cada fila se expande en un registro por ETIQUETA. EMPRESA, SUCURSAL y
    DEPARTAMENTO (columna AREA) se autollenan por fila desde el archivo cuando
    existen esas columnas; si la hoja no las trae, se usan los argumentos
    `empresa`/`sucursal`/`departamento` como respaldo. La ubicación combina el
    sitio (ORIGEN) con la UBICACIÓN. En sábanas antiguas sin columna SUCURSAL,
    ORIGEN se usa como sucursal (compatibilidad). El TIPO de activo se deja vacío
    para asignarlo después desde la herramienta.

    `progreso(hecho, total, hoja)`: callback opcional para reflejar el avance.
    """
    res = ResultadoImportacion()
    wb = openpyxl.load_workbook(ruta, data_only=True, read_only=True)
    try:
        total = len(hojas)
        for n, nombre in enumerate(hojas, 1):
            if nombre not in wb.sheetnames:
                res.errores.append(f"La hoja '{nombre}' no existe en el archivo.")
                continue
            ws = wb[nombre]
            fila_hdr, columnas = _detectar_encabezado(ws)
            if not columnas or "insumo" not in columnas:
                res.errores.append(f"'{nombre}': no se detectaron los encabezados.")
                continue
            if progreso:
                progreso(n, total, nombre)
            _importar_hoja(ws, fila_hdr, columnas, empresa, sucursal,
                           departamento, res)
    finally:
        wb.close()
    return res


def _importar_hoja(ws, fila_hdr: int, columnas: dict, empresa: str, sucursal: str,
                   departamento: str, res: ResultadoImportacion) -> None:
    """Arma los registros de la hoja (expandiendo etiquetas) y los inserta EN
    LOTE: con miles de filas, una transacción por registro es muchísimo más lenta."""
    registros = []
    for valores in _filas_datos(ws, fila_hdr, columnas):
        res.filas_leidas += 1
        insumo = str(valores.get("insumo") or "").strip()
        etiquetas = _partes(valores.get("etiqueta"))
        series = _partes(valores.get("serie"))
        responsable = str(valores.get("responsable") or "").strip()
        # Empresa/sucursal se autollenan por fila desde sus columnas; si la hoja no
        # trae la columna (o la celda está vacía) se usa el valor de la UI.
        emp_fila = str(valores.get("empresa") or "").strip() or empresa
        # El DEPARTAMENTO del Excel (AREA) NO corresponde al del SIPP, así que NO se
        # usa: queda vacío para elegirlo del desplegable (catálogo del SIPP).
        dep_fila = departamento
        origen = str(valores.get("origen") or "").strip()
        ubic = str(valores.get("ubicacion") or "").strip()
        suc_col = str(valores.get("sucursal") or "").strip()
        if suc_col:
            # Formato con columna SUCURSAL: esa es la sucursal; ORIGEN (el sitio)
            # enriquece la ubicación para no perderlo.
            suc_fila = suc_col
            ubic_fila = " — ".join(p for p in (origen, ubic) if p)
        else:
            # Formato anterior sin columna SUCURSAL: ORIGEN es la sucursal.
            suc_fila = origen or sucursal
            ubic_fila = ubic

        if not etiquetas:
            # Sin etiqueta: se guarda un único registro (se identificará por
            # insumo + serie) y se reporta para que el área lo revise.
            res.sin_etiqueta += 1
            etiquetas = [""]

        for i, etiqueta in enumerate(etiquetas):
            # La serie se aparea posicionalmente con la etiqueta; si hay menos
            # series que etiquetas, las restantes quedan sin serie (lo normal:
            # una fila de 10 sillas trae 10 etiquetas y ninguna serie).
            registros.append({
                "nombre_insumo": insumo,
                "etiqueta": etiqueta,
                "no_serie": series[i] if i < len(series) else "",
                "responsable": responsable,
                "ubicacion": ubic_fila,
                "empresa": emp_fila,
                "sucursal": suc_fila,
                "departamento": dep_fila,
            })

    agregados, duplicados = db.guardar_levantamiento_lote(registros)
    res.agregados += agregados
    res.duplicados += duplicados
