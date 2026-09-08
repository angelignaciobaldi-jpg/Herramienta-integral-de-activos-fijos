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
from datetime import date as _date
from datetime import datetime as _datetime

import openpyxl

from . import db
from .empresas import ID_POR_EMPRESA
from .tipos_activo import ID_POR_NOMBRE, SITUACIONES, TIPOS_ACTIVO

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
# Campos del FORMULARIO DE ALTA en la plantilla completa (encabezado normalizado ->
# clave que consume el RPA en datos_json). Así el Excel es la base del alta: el
# usuario llena aquí lo que antes tecleaba en el formulario. El TIPO se traduce a
# id_tipo_activo aparte (no va en datos_json).
_ENCABEZADOS_ALTA = {
    "TIPO DE ACTIVO": "id_TipoActivo",
    "DESCRIPCION": "de_DescripcionActivo",
    "SITUACION": "id_Situacion",
    "COSTO": "im_Costo",
    "FACTURA": "nb_Factura",
    "PROVEEDOR": "nb_Proveedor",
    "EMPRESA COMPRA": "id_EmpresaAgregar",
    "SUCURSAL COMPRA": "id_SucursalAgregar",
    "GRUPO CENTRO DE COSTO": "id_GrupoCentroCosto",
    "CENTRO DE COSTO": "id_CentroCosto",
    "DEPARTAMENTO": "id_Departamento",
    "FECHA ADQUISICION": "FH_ADQUISICION",
    "FECHA GARANTIA": "FH_GARANTIA",
    "FECHA ASIGNACION": "FH_ASIGNACION",
    "MARCA": "marca",
    "MODELO": "modelo",
    "CLIENTE": "cliente",
    "PLACA": "placa",
}
# Detección combinada: encabezado -> destino (campo básico O clave del alta).
_HEADERS = {**_ENCABEZADOS, **_ENCABEZADOS_ALTA}

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
    # El «*» de los obligatorios (y el que alguien agregue a mano) se descarta: es
    # una marca visual, no parte del nombre de la columna. Sin esto, marcar un
    # encabezado rompería su detección al importar.
    t = t.replace("*", " ")
    return re.sub(r"\s+", " ", t).strip()


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
# detector reconoce). Incluye TODOS los campos del formulario de alta para que el
# Excel sea la base del registro; lo no llenado se completa después en la herramienta.
PLANTILLA_ENCABEZADOS = [
    "INSUMO", "CANTIDAD", "ETIQUETA", "SERIE", "RESPONSABLE",
    "EMPRESA", "SUCURSAL", "UBICACION", "TIPO DE ACTIVO", "DESCRIPCION",
    "SITUACION", "COSTO", "FACTURA", "PROVEEDOR", "EMPRESA COMPRA",
    "SUCURSAL COMPRA", "GRUPO CENTRO DE COSTO", "CENTRO DE COSTO", "DEPARTAMENTO",
    "FECHA ADQUISICION", "FECHA GARANTIA", "FECHA ASIGNACION",
    "MARCA", "MODELO", "CLIENTE", "PLACA",
]


# Dos campos obligatorios del alta se capturan en columnas que se llaman DISTINTO:
# el formulario los conoce como `nb_NombreInsumo` y `nb_Empleado`, pero en la
# plantilla viven en las columnas del levantamiento (INSUMO y RESPONSABLE), que es
# de donde el RPA los toma. El resto se empareja solo por su clave.
_OBLIGATORIO_EN_COLUMNA = {
    "nb_NombreInsumo": "INSUMO",
    "nb_Empleado": "RESPONSABLE",
    # El resguardo del activo sale de la ubicación del levantamiento; en el
    # portal son «Empresa» y «Sucursal» de «Asignación del Activo».
    "id_EmpresaResguardo": "EMPRESA",
    "id_SucursalResguardo": "SUCURSAL",
}


def columnas_obligatorias() -> list[str]:
    """Encabezados de la plantilla que cubren un campo OBLIGATORIO del alta.

    Se derivan de `core/tipos_activo` en vez de listarse a mano: si algún día un
    campo pasa a requerido allá, la plantilla lo marca sin tocar este archivo.
    Hoy los tres son iguales para los 12 tipos de activo.
    """
    from core.tipos_activo import TIPOS_ACTIVO, campos_de_tipo

    inverso = {clave: hdr for hdr, clave in _ENCABEZADOS_ALTA.items()}
    columnas: list[str] = []
    for id_tipo in TIPOS_ACTIVO:
        for campo in campos_de_tipo(id_tipo):
            if not campo.requerido:
                continue
            col = inverso.get(campo.clave) or _OBLIGATORIO_EN_COLUMNA.get(campo.clave)
            if col in PLANTILLA_ENCABEZADOS and col not in columnas:
                columnas.append(col)
    return columnas


# Hasta qué fila alcanzan las listas desplegables y el sombreado de la plantilla.
# Es el tope de activos por archivo; más que eso conviene partir el levantamiento.
_FILAS_PLANTILLA = 1000

# Colores de la plantilla. Van como literales —y no como roles de Material, que es
# la regla dentro de la herramienta— porque este archivo se ve en Excel, fuera de
# la app y de su tema.
_AZUL_ENCABEZADO = "1F3A5F"
_AMBAR_ENCABEZADO = "B26A00"
_AMBAR_CUERPO = "FFF3E0"      # relleno tenue de la columna obligatoria


def _hoja_listas(wb, empresa: str):
    """Crea la hoja OCULTA con los catálogos y define sus rangos con nombre.

    Devuelve `(nombres, arbol)`: los rangos creados y el árbol
    sucursal->grupos->centros con que se armaron, para que el llamador escriba las
    fórmulas de validación.

    Los catálogos van en una hoja y no escritos dentro de la validación porque
    Excel limita esas listas a 255 caracteres: con 10,499 centros de costo no hay
    otra forma. Se oculta para que nadie la edite creyendo que es parte de la
    captura.

    Los rangos de centros se nombran por POSICIÓN (`CC_<i>_<j>`) y no por el
    nombre del grupo: dentro de una misma empresa hay grupos homónimos en
    distintas sucursales —«NOMINAS» aparece en 33 de Abastecedora—, así que un
    nombre derivado del texto colisionaría y ofrecería los centros de la sucursal
    equivocada.
    """
    from openpyxl.utils import get_column_letter
    from openpyxl.workbook.defined_name import DefinedName

    from core import db, insumos_depurados

    ws = wb.create_sheet("Listas")
    ws.sheet_state = "hidden"
    nombres: dict = {}
    col = 1

    def _volcar(titulo: str, valores: list, nombre_rango: str) -> None:
        """Escribe una lista en la siguiente columna libre y le pone nombre."""
        nonlocal col
        letra = get_column_letter(col)
        ws.cell(row=1, column=col, value=titulo)
        for i, v in enumerate(valores, 2):
            ws.cell(row=i, column=col, value=v)
        if valores:
            ref = f"Listas!${letra}$2:${letra}${len(valores) + 1}"
            wb.defined_names.add(DefinedName(nombre_rango, attr_text=ref))
            nombres[nombre_rango] = ref
        col += 1

    _volcar("INSUMOS DEPURADOS", insumos_depurados.nombres(), "INSUMOS")

    id_empresa = ID_POR_EMPRESA.get(empresa) if empresa else None
    arbol = db.catalogo_cc_empresa(id_empresa) if id_empresa is not None else []
    if arbol:
        _volcar("SUCURSALES", [s["sucursal"] for s in arbol], "SUCURSALES")
        for i, suc in enumerate(arbol, 1):
            _volcar(f"GRUPOS {suc['sucursal']}",
                    [g["nb_grupo"] for g in suc["grupos"]], f"GRP_{i}")
            for j, grupo in enumerate(suc["grupos"], 1):
                _volcar(f"CC {suc['sucursal']} / {grupo['nb_grupo']}",
                        grupo["centros"], f"CC_{i}_{j}")
    if id_empresa is not None:
        _volcar("DEPARTAMENTOS", db.listar_departamentos(id_empresa), "DEPARTAMENTOS")
    return nombres, arbol


def generar_plantilla(ruta: str, empresa: str = "") -> str:
    """Crea en `ruta` un Excel plantilla de carga masiva. Devuelve la ruta escrita.

    `empresa` (nombre del catálogo de Grupo Petroil) mete en el archivo los
    catálogos contables DE ESA EMPRESA. Sin ella sale la plantilla genérica, solo
    con los desplegables que no dependen de la empresa.

    Hoja «Activos» con todos los campos del alta, hoja «Instrucciones» y hoja
    oculta «Listas» con los catálogos. Se deja SIN filas de datos para no importar
    ejemplos por error.

    Las tres columnas contables ENCADENAN: la sucursal acota los grupos y el grupo
    acota los centros de costo. Es lo único que vuelve capturable esa parte
    —Abastecedora tiene 10,499 centros— y de paso evita la combinación imposible
    (un centro que no pertenece al grupo capturado), que hoy no se descubre hasta
    que el alta falla en el portal.
    """
    from openpyxl.comments import Comment
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    obligatorias = columnas_obligatorias()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Activos"
    nombres, arbol = _hoja_listas(wb, empresa)

    def _letra(encabezado: str) -> str:
        return get_column_letter(PLANTILLA_ENCABEZADOS.index(encabezado) + 1)

    # Los obligatorios llevan «*» en el rótulo, como en los formularios de la
    # herramienta, y color propio: el asterisco solo se ve de cerca y la fila de
    # encabezados es ancha.
    ws.append([f"{h} *" if h in obligatorias else h for h in PLANTILLA_ENCABEZADOS])
    relleno_cuerpo = PatternFill("solid", fgColor=_AMBAR_CUERPO)
    for i, celda in enumerate(ws[1], 1):
        encabezado = PLANTILLA_ENCABEZADOS[i - 1]
        es_obligatorio = encabezado in obligatorias
        celda.font = Font(bold=True, color="FFFFFF")
        celda.fill = PatternFill(
            "solid", fgColor=_AMBAR_ENCABEZADO if es_obligatorio else _AZUL_ENCABEZADO)
        celda.alignment = Alignment(horizontal="center", vertical="center")
        if es_obligatorio:
            celda.comment = Comment(
                "Campo OBLIGATORIO del alta en el SIPP: sin él el activo no se "
                "puede dar de alta.", "Herramienta de Activos Fijos")
            # El cuerpo de la columna también se tiñe: capturando en la fila 40 el
            # encabezado ya se salió de la pantalla, y el color era justo la pista
            # de que ese dato no se puede dejar en blanco.
            for fila in range(2, _FILAS_PLANTILLA + 1):
                ws.cell(row=fila, column=i).fill = relleno_cuerpo
        ws.column_dimensions[get_column_letter(i)].width = min(
            40, max(14, len(encabezado) + 4))
    ws.freeze_panes = "A2"

    def _validar(encabezado: str, formula: str, *, bloquear: bool,
                 mensaje: str = "") -> None:
        # La fórmula va SIN el «=» inicial: así la escribe Excel y así la pide el
        # OOXML. Con el signo, algunas versiones descartan la validación en
        # silencio y la columna queda sin desplegable sin decir por qué.
        formula = formula.lstrip("=")
        col = _letra(encabezado)
        dv = DataValidation(type="list", formula1=formula, allow_blank=True,
                            showErrorMessage=bloquear)
        if bloquear and mensaje:
            dv.errorTitle, dv.error = "Valor fuera del catálogo", mensaje
        ws.add_data_validation(dv)
        dv.add(f"{col}2:{col}{_FILAS_PLANTILLA}")

    def _validar_embebida(valores: list, encabezado: str) -> None:
        """Lista escrita dentro de la validación; solo si cabe en 255 caracteres."""
        formula = '"' + ",".join(valores) + '"'
        if len(formula) <= 255:
            _validar(encabezado, formula, bloquear=True,
                     mensaje="Elige uno de los valores de la lista.")

    _validar_embebida(list(TIPOS_ACTIVO.values()), "TIPO DE ACTIVO")
    _validar_embebida(list(SITUACIONES.values()), "SITUACION")

    # INSUMO sugiere, no obliga: el catálogo depurado es un recorte de genéricos y
    # un activo legítimo puede quedar fuera. Bloquear dejaría al capturista sin
    # salida dentro del archivo.
    if "INSUMOS" in nombres:
        _validar("INSUMO", "=INSUMOS", bloquear=False)
    if "DEPARTAMENTOS" in nombres:
        _validar("DEPARTAMENTO", "=DEPARTAMENTOS", bloquear=False)

    if arbol:
        col_suc, col_grupo = _letra("SUCURSAL"), _letra("GRUPO CENTRO DE COSTO")
        # `$X2` es relativo a la FILA: Excel lo corre solo en cada renglón del
        # rango, así que cada fila consulta su propia sucursal y su propio grupo.
        idx_suc = f'MATCH(${col_suc}2,SUCURSALES,0)'
        idx_grupo = f'MATCH(${col_grupo}2,INDIRECT("GRP_"&{idx_suc}),0)'
        _validar("SUCURSAL", "=SUCURSALES", bloquear=True,
                 mensaje="Elige una sucursal del catálogo del SIPP de esta empresa.")
        # Grupo y centro NO bloquean: su fórmula depende de lo capturado arriba y,
        # mientras la sucursal esté vacía o mal escrita, INDIRECT devuelve error.
        # Bloqueando, ese error volvería la celda imposible de llenar; así el
        # desplegable ayuda cuando puede y nunca estorba.
        _validar("GRUPO CENTRO DE COSTO", f'=INDIRECT("GRP_"&{idx_suc})',
                 bloquear=False)
        _validar("CENTRO DE COSTO",
                 f'=INDIRECT("CC_"&{idx_suc}&"_"&{idx_grupo})', bloquear=False)
        # La empresa del archivo es UNA: el desplegable de un solo valor lo deja
        # explícito y evita que se capture otra por costumbre.
        for encabezado in ("EMPRESA", "EMPRESA COMPRA"):
            _validar_embebida([empresa], encabezado)

    ins = wb.create_sheet("Instrucciones")
    ins.column_dimensions["A"].width = 100
    guia = [
        "CARGA MASIVA DE ACTIVOS — INSTRUCCIONES",
        "",
        (f"Plantilla de {empresa.upper()}: los desplegables de sucursal, grupo y "
         f"centro de costo traen el catálogo de esta empresa, así que no sirve "
         f"para otra." if empresa else
         "Plantilla genérica: sin empresa elegida no trae los catálogos contables "
         "(sucursal, grupo y centro de costo se capturan a mano)."),
        "",
        "COLUMNAS OBLIGATORIAS (encabezado en ÁMBAR y con «*», y la columna "
        "sombreada): " + ", ".join(obligatorias) + ".",
        "Son los campos que el alta del SIPP exige para CUALQUIER tipo de activo. "
        "Sin ellos el activo no se puede dar de alta: el registro se importa igual, "
        "pero queda pendiente hasta completarlos en la ficha.",
        "",
        "CELDAS CON LISTA DESPLEGABLE",
        "• INSUMO: catálogo depurado (los genéricos que sí son activo fijo). "
        "Sugiere, pero acepta otro nombre si el que necesitas no está en la lista.",
        "• SUCURSAL, GRUPO CENTRO DE COSTO y CENTRO DE COSTO están ENCADENADOS: "
        "elige primero la sucursal y el desplegable de grupo se limita a los de "
        "esa sucursal; al elegir el grupo, el de centro de costo se limita a los "
        "suyos. Si capturas el grupo antes que la sucursal, la lista sale vacía.",
        "• TIPO DE ACTIVO, SITUACION y DEPARTAMENTO también traen lista.",
        "",
        "Captura un activo por fila en la hoja «Activos». El Excel es la base del "
        "registro: lo que llenes aquí es lo que usará el alta automática (RPA); lo "
        "que dejes vacío se completa después en la herramienta.",
        "",
        "IDENTIFICACIÓN",
        "• INSUMO (obligatorio): nombre del insumo tal como está en el SIPP (se "
        "resuelve a su clave para el RPA; si no se encuentra, se elige en la ficha).",
        "• CANTIDAD: cuántas piezas iguales. Si es más de 1, pon todas sus ETIQUETAS "
        "en la misma celda separadas por «/» (ej. 0048399/0048400/0048401): cada "
        "etiqueta se registra como un activo.",
        "• ETIQUETA: número(s) de inventario (identificador principal).",
        "• SERIE: número de serie (opcional). Si hay varias, sepáralas por «/» en el "
        "mismo orden que las etiquetas.",
        "• TIPO DE ACTIVO y SITUACION: elige de sus listas. DESCRIPCION es libre.",
        "",
        "COMPRA",
        "• COSTO, FACTURA (folio), PROVEEDOR, EMPRESA COMPRA, SUCURSAL COMPRA.",
        "• GRUPO CENTRO DE COSTO y CENTRO DE COSTO: elígelos de sus desplegables "
        "(dependen de la SUCURSAL capturada). DEPARTAMENTO, del suyo.",
        "• FECHAS (ADQUISICION / GARANTIA / ASIGNACION): formato DD/MM/AAAA.",
        "",
        "RESGUARDO",
        "• RESPONSABLE: empleado resguardante (se resuelve a su id para el RPA).",
        "• EMPRESA y SUCURSAL: la empresa/sucursal del activo. Si las dejas vacías, se "
        "usan las del selector de la pantalla al importar.",
        "• UBICACION: ubicación física.",
        "",
        "CARACTERÍSTICAS (según el tipo): MARCA, MODELO, CLIENTE, PLACA.",
        "",
        "No cambies los nombres de los encabezados de la hoja «Activos».",
        "No borres la hoja oculta «Listas»: es de donde salen los desplegables.",
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
    # Las hojas OCULTAS no se importan. La «Listas» de nuestra propia plantilla
    # cae en el detector —sus títulos «INSUMOS DEPURADOS», «SUCURSALES» y
    # «DEPARTAMENTOS» contienen los encabezados que busca— y ofrecería importar
    # 1,693 renglones de catálogo como si fueran activos. Una hoja escondida no
    # es captura del usuario, sea de esta plantilla o de cualquier otro archivo.
    if getattr(ws, "sheet_state", "visible") != "visible":
        return HojaDetectada(
            ws.title, 0, {}, 0, 0, importable=False,
            motivo="Hoja oculta: contiene catálogos de apoyo, no activos.")
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


def _match_header(texto: str) -> "str | None":
    """Destino (campo básico o clave del alta) de un encabezado. Prefiere el match
    EXACTO; si no, el encabezado más específico contenido en el texto (el más largo),
    para que 'EMPRESA COMPRA' no lo capture 'EMPRESA' ni 'GRUPO CENTRO DE COSTO' a
    'CENTRO DE COSTO'."""
    if texto in _HEADERS:
        return _HEADERS[texto]
    candidatos = [(h, d) for h, d in _HEADERS.items() if h in texto]
    if candidatos:
        return max(candidatos, key=lambda hd: len(hd[0]))[1]
    return None


def _detectar_encabezado(ws) -> tuple[int, dict]:
    """Busca la fila de encabezados y mapea destino (campo básico o clave del alta)
    -> índice de columna."""
    for i, fila in enumerate(ws.iter_rows(min_row=1, max_row=_MAX_FILA_ENCABEZADO,
                                          values_only=True), 1):
        columnas = {}
        for j, celda in enumerate(fila, 1):
            texto = _norm(celda)
            if not texto:
                continue
            destino = _match_header(texto)
            if destino and destino not in columnas:
                columnas[destino] = j
        if len(columnas) >= _MIN_COINCIDENCIAS and "insumo" in columnas:
            return i, columnas
    return 0, {}


def _fmt_valor(clave: str, valor) -> str:
    """Normaliza el valor de una celda del alta a texto (fechas -> DD/MM/AAAA)."""
    if valor is None:
        return ""
    if clave.startswith("FH_") and isinstance(valor, (_datetime, _date)):
        return valor.strftime("%d/%m/%Y")
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    return str(valor).strip()


def _resolver_insumo(nombre: str, id_empresa, cache: dict) -> "int | None":
    """Clave (id) del insumo en la caché del SIPP; None si no está.

    Se queda con el MÁS GENERAL de los que coinciden (ver
    `core.insumos.elegir_mas_general`), que es el que debe registrarse."""
    n = _norm(nombre)
    if not n:
        return None
    if (n, id_empresa) in cache:
        return cache[(n, id_empresa)]
    from .insumos import resolver

    # Solo el nombre EXACTO se acepta sin preguntar. Lo ambiguo se resuelve en la
    # pantalla, donde el usuario elige el insumo del catálogo.
    elegido, exacto = resolver(nombre, id_empresa)
    encontrado = elegido.id_insumo if (elegido and exacto) else None
    cache[(n, id_empresa)] = encontrado
    return encontrado


def _resolver_empleado(nombre: str, cache: dict) -> "int | None":
    """id del empleado por nombre exacto en la caché del SIPP; None si no está."""
    n = _norm(nombre)
    if not n:
        return None
    if n in cache:
        return cache[n]
    encontrado = None
    for emp in db.buscar_empleados(nombre, limite=25):
        if _norm(emp.nombre) == n:
            encontrado = emp.id_empleado
            break
    cache[n] = encontrado
    return encontrado


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
    cache_insumo: dict = {}
    cache_empleado: dict = {}
    for valores in _filas_datos(ws, fila_hdr, columnas):
        res.filas_leidas += 1
        insumo = str(valores.get("insumo") or "").strip()
        etiquetas = _partes(valores.get("etiqueta"))
        series = _partes(valores.get("serie"))
        responsable = str(valores.get("responsable") or "").strip()
        # Empresa/sucursal se autollenan por fila desde sus columnas; si la hoja no
        # trae la columna (o la celda está vacía) se usa el valor de la UI.
        emp_fila = str(valores.get("empresa") or "").strip() or empresa
        # DEPARTAMENTO: ahora hay columna dedicada (clave id_Departamento); si no
        # viene, respaldo al del selector de la UI.
        dep_fila = _fmt_valor("id_Departamento", valores.get("id_Departamento")) or departamento
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

        # --- Campos del ALTA (plantilla completa) -> datos_json ---------------
        id_empresa = ID_POR_EMPRESA.get(emp_fila)
        tipo_nombre = _fmt_valor("id_TipoActivo", valores.get("id_TipoActivo"))
        id_tipo = ID_POR_NOMBRE.get(tipo_nombre) if tipo_nombre else None
        datos: dict = {}
        for clave in _ENCABEZADOS_ALTA.values():
            if clave == "id_TipoActivo":   # va como id_tipo_activo, no en datos
                continue
            v = _fmt_valor(clave, valores.get(clave))
            if v:
                datos[clave] = v
        # Lo que el formulario también guarda en datos y aquí ya conocemos.
        if insumo:
            datos["nb_NombreInsumo"] = insumo
        if responsable:
            datos["nb_Empleado"] = responsable
        if ubic_fila:
            datos.setdefault("de_Ubicacion", ubic_fila)
        if dep_fila:
            datos.setdefault("id_Departamento", dep_fila)
        # Ids del SIPP para que el RPA seleccione insumo/empleado por id (si no se
        # resuelven, se dejan para elegirlos en la ficha).
        id_ins = _resolver_insumo(insumo, id_empresa, cache_insumo)
        if id_ins:
            datos["id_InsumoOrigen"] = str(id_ins)
        id_emp = _resolver_empleado(responsable, cache_empleado)
        if id_emp:
            datos["id_EmpleadoResguardo"] = str(id_emp)

        if not etiquetas:
            # Sin etiqueta: se guarda un único registro (se identificará por
            # insumo + serie) y se reporta para que el área lo revise.
            res.sin_etiqueta += 1
            etiquetas = [""]

        for i, etiqueta in enumerate(etiquetas):
            # La serie se aparea posicionalmente con la etiqueta; si hay menos
            # series que etiquetas, las restantes quedan sin serie (lo normal:
            # una fila de 10 sillas trae 10 etiquetas y ninguna serie).
            serie_i = series[i] if i < len(series) else ""
            datos_fila = dict(datos)
            if serie_i:
                datos_fila["nu_Serie"] = serie_i
            registros.append({
                "nombre_insumo": insumo,
                "etiqueta": etiqueta,
                "no_serie": serie_i,
                "responsable": responsable,
                "ubicacion": ubic_fila,
                "empresa": emp_fila,
                "sucursal": suc_fila,
                "departamento": dep_fila,
                "id_tipo_activo": id_tipo,
                "datos": datos_fila or None,
            })

    agregados, duplicados = db.guardar_levantamiento_lote(registros)
    res.agregados += agregados
    res.duplicados += duplicados
