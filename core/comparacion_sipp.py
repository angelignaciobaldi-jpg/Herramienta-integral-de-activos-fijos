from __future__ import annotations

"""Comparación SIPP vs Excel para activos ya dados de alta.

Cuando un activo del levantamiento se identifica por etiqueta en el SIPP (dado de
alta), tenemos DOS versiones de sus datos:

- **Excel/levantamiento** — lo capturado en campo, en `Levantamiento.datos()`
  (claves de `core.tipos_activo`, p. ej. ``id_Departamento``). El importador de
  Excel guarda **nombres** (no ids) en los selects, igual que el SIPP.
- **SIPP** — lo real del catálogo, en `Levantamiento.info_sipp()` (claves del
  caché de `core.activos_sipp`, p. ej. ``departamento``).

Este módulo empareja ambos lados campo por campo y decide si difieren. La UI
resalta las diferencias y deja al usuario elegir, por campo, qué valor prevalece:

- **SIPP → Excel** (conservar el SIPP): actualiza `datos_json` local, sin tocar el
  SIPP. Barato y sin riesgo.
- **Excel → SIPP** (sobrescribir el SIPP): empuja el valor del Excel al catálogo
  vía el RPA de modificación (`SesionSipp.modificar_activo`). Solo aplica a los
  campos con `ng_model`; insumo y empleado se eligen por modal en el SIPP, así que
  no se empujan como texto (`empujable=False`).

Sin Flet ni navegador: la comparación trabaja sobre datos ya persistidos tras
«Buscar en SIPP». Solo el empuje Excel → SIPP usa el RPA.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class CampoComparable:
    """Un campo que se compara entre el Excel y el SIPP."""
    clave: str          # clave en datos_json (Excel) y para escribir Excel←SIPP
    etiqueta: str       # rótulo en la UI
    clave_sipp: str     # clave en info_sipp() (caché del SIPP)
    control: str = "text"   # text | number | date | select (afecta la normalización)
    ng_model: str = ""      # filtrosAgregar.* para empujar Excel→SIPP ("" = no)
    empujable: bool = True  # False: insumo/empleado (selección por modal, no texto)


# Orden = orden en que se muestran. Mantiene paridad con core/tipos_activo.
CAMPOS: list[CampoComparable] = [
    CampoComparable("nb_NombreInsumo", "Insumo", "insumo", "text", "", empujable=False),
    CampoComparable("nu_Serie", "No. de serie", "serie", "text",
                    "filtrosAgregar.nu_Serie"),
    CampoComparable("de_DescripcionActivo", "Descripción", "descripcion", "text",
                    "filtrosAgregar.de_DescripcionActivo"),
    CampoComparable("id_Situacion", "Situación", "situacion", "select",
                    "filtrosAgregar.id_Situacion"),
    CampoComparable("im_Costo", "Costo", "costo", "number",
                    "filtrosAgregar.im_Costo"),
    CampoComparable("id_GrupoCentroCosto", "Grupo centro de costo",
                    "grupo_centro_costo", "select",
                    "filtrosAgregar.id_GrupoCentroCosto"),
    CampoComparable("id_CentroCosto", "Centro de costo", "centro_costo", "select",
                    "filtrosAgregar.id_CentroCosto"),
    CampoComparable("id_Departamento", "Departamento", "departamento", "select",
                    "filtrosAgregar.id_Departamento"),
    CampoComparable("de_Ubicacion", "Ubicación", "ubicacion", "text",
                    "filtrosAgregar.de_Ubicacion"),
    CampoComparable("nb_Empleado", "Empleado resguardo", "empleado", "text", "",
                    empujable=False),
    CampoComparable("FH_ADQUISICION", "Fecha de adquisición", "fecha_adquisicion",
                    "date", "dt_FH_ADQUISICION"),
    CampoComparable("FH_GARANTIA", "Fecha de garantía", "fecha_garantia", "date",
                    "dt_FH_GARANTIA"),
    CampoComparable("FH_ASIGNACION", "Fecha de asignación", "fecha_asignacion",
                    "date", "dt_FH_ASIGNACION"),
]


@dataclass
class Diferencia:
    """Resultado de comparar un campo. Guarda el valor CRUDO (para aplicar) y el
    de PANTALLA (normalizado para mostrar)."""
    campo: CampoComparable
    sipp_crudo: str
    excel_crudo: str
    sipp: str       # para mostrar ("—" si vacío)
    excel: str
    difiere: bool


def _texto(valor) -> str:
    return "" if valor is None else str(valor).strip()


def _clave_comparar(valor, control: str) -> str:
    """Normaliza un valor para DECIDIR si dos lados son iguales (no para mostrar).
    Costos como número (4500 == 4,500.00), fechas por su parte AAAA-MM-DD/DD-MM,
    texto sin distinguir mayúsculas ni espacios repetidos."""
    s = _texto(valor)
    if not s:
        return ""
    if control == "number":
        try:
            return f"{float(s.replace(',', '')):.2f}"
        except ValueError:
            pass
    if control == "date":
        return s[:10]
    return " ".join(s.upper().split())


def _mostrar(valor, control: str) -> str:
    s = _texto(valor)
    if not s:
        return "—"
    if control == "number":
        try:
            return f"{float(s.replace(',', '')):,.2f}"
        except ValueError:
            return s
    return s


def comparar(registro) -> list[Diferencia]:
    """Compara TODOS los campos comparables del registro (dado de alta). Devuelve
    una `Diferencia` por campo, marque o no diferencia."""
    info = registro.info_sipp()
    datos = registro.datos()
    resultado: list[Diferencia] = []
    for c in CAMPOS:
        v_sipp = info.get(c.clave_sipp)
        v_excel = datos.get(c.clave)
        difiere = _clave_comparar(v_sipp, c.control) != _clave_comparar(v_excel, c.control)
        resultado.append(Diferencia(
            campo=c,
            sipp_crudo=_texto(v_sipp),
            excel_crudo=_texto(v_excel),
            sipp=_mostrar(v_sipp, c.control),
            excel=_mostrar(v_excel, c.control),
            difiere=difiere,
        ))
    return resultado


def campos_distintos(registro) -> list[Diferencia]:
    """Solo los campos que difieren."""
    return [d for d in comparar(registro) if d.difiere]


def hay_diferencias(registro) -> bool:
    """¿El activo dado de alta tiene algún dato distinto entre SIPP y Excel?
    Requiere info del SIPP; si no la hay (no dado de alta), no hay comparación."""
    if not registro.info_sipp():
        return False
    return any(d.difiere for d in comparar(registro))
