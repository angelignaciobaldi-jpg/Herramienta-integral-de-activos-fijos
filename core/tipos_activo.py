"""Config declarativa de tipos de activo y sus campos de alta (SIPP).

Fuente única de: los tipos de activo del SIPP, las situaciones, y —lo importante—
QUÉ CAMPOS pide el alta de un activo y cuáles son OBLIGATORIOS, por tipo. La
pantalla de Registro (Fase 2) arma el formulario dinámico leyendo de aquí, y el
RPA usa el `ng_model` de cada campo como localizador en el portal.

Cómo ampliarlo (sin tocar código de la UI):
  - Agrega/edita entradas en CAMPOS_COMUNES (campos que aplican a todos los tipos).
  - En CAMPOS_POR_TIPO, por cada id de tipo, agrega una lista de campos EXTRA o
    de OVERRIDE (mismo `clave` que un común -> lo reemplaza; `clave` nueva -> se
    añade). Así cada tipo declara sus particularidades.

Los nombres (`ng_model`) y los tipos provienen del DOM real del catálogo del SIPP
(app `appActivosNuevo`, `filtrosAgregar.*`); ver docs/SIPP_Modulo_Activos_Fijos.md.
El detalle fino de campos por tipo se confirma capturando el alta con cada tipo
seleccionado (los `ng-if` por tipo viven en el JS del controlador, no en el DOM
estático).
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --- Tipos de activo (id_TipoActivoFijo del SIPP -> nombre) --------------------
# Catálogo OFICIAL provisto por el área (id_TipoActivoFijo real del SIPP).
TIPOS_ACTIVO: dict[int, str] = {
    1: "Edificios",
    2: "Terrenos",
    3: "Maquinaria y Equipo",
    4: "Vehículos Utilitarios",
    5: "Vehículos Pesados",
    6: "Mobiliario y equipo de Oficina",
    7: "Equipo informático",
    8: "Celulares",
    9: "Embarcaciones",
    10: "Aeronaves",
    11: "Tanques",
    12: "Activos Intangibles",
}

# Mapeo inverso nombre -> id. Útil porque en la interfaz los combos se manejan
# por NOMBRE (es lo que ve el usuario y lo que el RPA busca en el SIPP), pero se
# persiste el id del tipo.
ID_POR_NOMBRE: dict[str, int] = {v: k for k, v in TIPOS_ACTIVO.items()}


# --- Situación del activo (id -> nombre) --------------------------------------
SITUACIONES: dict[int, str] = {
    0: "Activo Fijo",
    1: "Arrendamiento",
    2: "Comodato",
    3: "Solo Resguardo",
    4: "Usado",
}


# --- Definición de un campo del formulario de alta ----------------------------
@dataclass
class CampoActivo:
    """Un campo del alta del activo en el SIPP.

    clave:      identificador interno estable (para persistir en datos_json).
    ng_model:   localizador en el portal (ng-model del control), para el RPA.
    etiqueta:   texto que ve el usuario en el formulario dinámico.
    control:    'text' | 'number' | 'date' | 'select'.
    requerido:  si es obligatorio para el alta.
    opciones:   para 'select', dict {valor -> etiqueta} (o None si es dinámico
                del SIPP, p. ej. insumos/empresas que se resuelven aparte).
    grupo:      agrupador visual ('Identificación', 'Compra', 'Resguardo'…).
    detalle:    True si es una CARACTERÍSTICA dinámica del insumo ("Detalles
                Insumo" / camposDetalle en el SIPP). Estas NO tienen un ng-model
                fijo: se llenan iterando el ng-repeat "(key, item) in camposDetalle"
                y emparejando por el rótulo (item.NB_CAMPODETALLE == etiqueta); el
                valor va en camposDetalle[$index]['DE_VALORCAMPODETALLE']. Para esos
                campos, `etiqueta` ES el nombre a emparejar y `ng_model` se ignora.
    """

    clave: str
    ng_model: str
    etiqueta: str
    control: str = "text"
    requerido: bool = False
    opciones: "dict | None" = None
    grupo: str = "General"
    detalle: bool = False


# --- Campos COMUNES a todos los tipos (base del alta real `filtrosAgregar.*`) --
CAMPOS_COMUNES: list[CampoActivo] = [
    # Identificación
    CampoActivo("id_TipoActivo", "filtrosAgregar.id_TipoActivo", "Tipo de activo",
                "select", requerido=True, opciones=TIPOS_ACTIVO, grupo="Identificación"),
    CampoActivo("nb_NombreInsumo", "filtrosAgregar.nb_NombreInsumo", "Insumo",
                "text", requerido=True, grupo="Identificación"),
    CampoActivo("nu_Serie", "filtrosAgregar.nu_Serie", "No. de serie",
                "text", requerido=False, grupo="Identificación"),
    CampoActivo("de_DescripcionActivo", "filtrosAgregar.de_DescripcionActivo",
                "Descripción", "text", grupo="Identificación"),
    CampoActivo("id_Situacion", "filtrosAgregar.id_Situacion", "Situación",
                "select", opciones=SITUACIONES, grupo="Identificación"),
    # Compra
    CampoActivo("im_Costo", "filtrosAgregar.im_Costo", "Costo", "number",
                grupo="Compra"),
    CampoActivo("nb_Factura", "filtrosAgregar.nb_Factura", "Factura", "text",
                grupo="Compra"),
    CampoActivo("nb_Proveedor", "filtrosAgregar.nb_Proveedor", "Proveedor", "text",
                grupo="Compra"),
    CampoActivo("id_EmpresaAgregar", "filtrosAgregar.id_EmpresaAgregar",
                "Empresa (compra)", "select", grupo="Compra"),
    CampoActivo("id_SucursalAgregar", "filtrosAgregar.id_SucursalAgregar",
                "Sucursal (compra)", "select", grupo="Compra"),
    CampoActivo("FH_ADQUISICION", "dt_FH_ADQUISICION", "Fecha de adquisición", "date",
                grupo="Compra"),
    CampoActivo("FH_GARANTIA", "dt_FH_GARANTIA", "Fecha de garantía", "date",
                grupo="Compra"),
    # Resguardo
    CampoActivo("nb_Empleado", "filtrosAgregar.nb_Empleado", "Empleado (resguardo)",
                "text", requerido=True, grupo="Resguardo"),
    # Obligatorios en el portal (llevan asterisco en «Asignación del Activo»).
    # La herramienta los toma de las columnas EMPRESA y SUCURSAL del registro.
    CampoActivo("id_EmpresaResguardo", "filtrosAgregar.id_EmpresaResguardo",
                "Empresa (resguardo)", "select", requerido=True, grupo="Resguardo"),
    CampoActivo("id_SucursalResguardo", "filtrosAgregar.id_SucursalResguardo",
                "Sucursal (resguardo)", "select", requerido=True, grupo="Resguardo"),
    # Grupo/centro/departamento: el portal los tiene DUPLICADOS (compra y
    # resguardo). Los de compra están deshabilitados mientras no se llene la
    # sucursal de compra —sección oculta que no tocamos—, así que escribir ahí no
    # surtía efecto: se usan los de RESGUARDO, que son los de «Asignación del
    # Activo». Van DESPUÉS de empresa/sucursal de resguardo porque cuelgan de
    # ellas (ng-disabled), y grupo antes que centro (el centro depende del grupo).
    CampoActivo("id_GrupoCentroCosto",
                "filtrosAgregar.id_GrupoCentroCostoResguardo",
                "Grupo centro de costo", "select", grupo="Resguardo"),
    CampoActivo("id_CentroCosto", "filtrosAgregar.id_CentroCostoResguardo",
                "Centro de costo", "select", grupo="Resguardo"),
    CampoActivo("id_Departamento", "filtrosAgregar.id_DepartamentoResguardo",
                "Departamento", "select", grupo="Resguardo"),
    CampoActivo("de_Ubicacion", "filtrosAgregar.de_Ubicacion", "Ubicación", "text",
                grupo="Resguardo"),
    CampoActivo("FH_ASIGNACION", "dt_FH_ASIGNACION", "Fecha de asignación", "date",
                grupo="Resguardo"),
]

# --- Campos EXTRA / OVERRIDE por tipo -----------------------------------------
# Clave existente -> reemplaza el común; clave nueva -> se agrega.
#
# IMPORTANTE: en el SIPP, los campos particulares por tipo son en realidad las
# CARACTERÍSTICAS del insumo ("Detalles Insumo" / camposDetalle), una lista
# DINÁMICA que depende del insumo elegido (no ng-models fijos). Aquí se declaran
# con detalle=True y su `etiqueta` = el rótulo a emparejar (NB_CAMPODETALLE).
# Se siembran ejemplos confirmados con capturas reales; el área los amplía.
CAMPOS_POR_TIPO: dict[int, list[CampoActivo]] = {
    # Maquinaria y Equipo (id 3): CONFIRMADO con captura real (insumo "SONDA
    # RIGIDA"). Características "Detalles Insumo": Marca, Modelo, Cliente.
    3: [
        CampoActivo("marca", "", "Marca", "text", grupo="Detalles Insumo", detalle=True),
        CampoActivo("modelo", "", "Modelo", "text", grupo="Detalles Insumo", detalle=True),
        CampoActivo("cliente", "", "Cliente", "text", grupo="Detalles Insumo", detalle=True),
    ],
    # Vehículos Pesados (id 5): placa, motor, modelo/año (EJEMPLO a confirmar).
    5: [
        CampoActivo("marca", "", "Marca", "text", grupo="Detalles Insumo", detalle=True),
        CampoActivo("modelo", "", "Modelo", "text", grupo="Detalles Insumo", detalle=True),
        CampoActivo("placa", "", "Placa", "text", grupo="Detalles Insumo", detalle=True),
    ],
    # Vehículos Utilitarios (id 4): idem (EJEMPLO a confirmar).
    4: [
        CampoActivo("marca", "", "Marca", "text", grupo="Detalles Insumo", detalle=True),
        CampoActivo("modelo", "", "Modelo", "text", grupo="Detalles Insumo", detalle=True),
        CampoActivo("placa", "", "Placa", "text", grupo="Detalles Insumo", detalle=True),
    ],
    # Equipo informático (id 7): CONFIRMADO con captura real del formulario.
    # Los rótulos van TAL COMO los escribe el portal (sin acentos en «Portatil» y
    # «Tactil», con los paréntesis): el emparejado es por texto y, aunque la
    # comparación ignora acentos y dos puntos, cualquier otra diferencia de
    # redacción dejaría el campo sin llenar. El orden es el de la captura.
    7: [
        CampoActivo("marca", "", "Marca", "text", grupo="Detalles Insumo",
                    detalle=True),
        CampoActivo("modelo", "", "Modelo", "text", grupo="Detalles Insumo",
                    detalle=True),
        CampoActivo("tipo_equipo", "", "Tipo (Escritorio/Portatil)", "text",
                    grupo="Detalles Insumo", detalle=True),
        CampoActivo("memoria_ram", "", "Memoria RAM", "text",
                    grupo="Detalles Insumo", detalle=True),
        CampoActivo("procesador", "", "Procesador", "text",
                    grupo="Detalles Insumo", detalle=True),
        CampoActivo("sistema_operativo", "", "Sistema Operativo", "text",
                    grupo="Detalles Insumo", detalle=True),
        CampoActivo("pantalla_tactil", "", "Pantalla Tactil", "text",
                    grupo="Detalles Insumo", detalle=True),
        CampoActivo("tipo_sistema", "", "Tipo Sistema (x86/x64)", "text",
                    grupo="Detalles Insumo", detalle=True),
        CampoActivo("cliente", "", "Cliente", "text", grupo="Detalles Insumo",
                    detalle=True),
        CampoActivo("cargador", "", "Cargador", "text", grupo="Detalles Insumo",
                    detalle=True),
    ],
}


def campos_de_tipo(id_tipo: "int | None") -> list[CampoActivo]:
    """Devuelve la lista de campos del alta para un tipo de activo: los comunes,
    con los override/extra del tipo aplicados. Si `id_tipo` es None, solo comunes.

    Un campo del tipo con la MISMA `clave` que un común lo REEMPLAZA (override);
    con clave nueva se AÑADE al final."""
    resultado = {c.clave: c for c in CAMPOS_COMUNES}
    if id_tipo is not None:
        for campo in CAMPOS_POR_TIPO.get(id_tipo, []):
            resultado[campo.clave] = campo
    # Conserva el orden: primero comunes (en su orden), luego los extra nuevos.
    orden = [c.clave for c in CAMPOS_COMUNES]
    extra = [k for k in resultado if k not in orden]
    return [resultado[k] for k in orden if k in resultado] + [resultado[k] for k in extra]


def nombre_tipo(id_tipo: "int | None") -> str:
    """Nombre del tipo de activo (o '' si es None/desconocido)."""
    return TIPOS_ACTIVO.get(id_tipo, "") if id_tipo is not None else ""


def faltantes_obligatorios(id_tipo_activo, datos: dict, nombre_insumo: str = "",
                           responsable: str = "", empresa: str = "",
                           sucursal: str = "") -> list[str]:
    """Rótulos de los campos OBLIGATORIOS del alta que este registro no tiene.

    Es la misma exigencia del formulario del SIPP: sin ellos el alta se queda a
    medias en el portal, con el activo creado pero incompleto, que es más caro de
    arreglar que no haberlo intentado.

    Dos campos se capturan en más de un sitio y se aceptan de cualquiera:
    el INSUMO (`nb_NombreInsumo` o la columna del registro) y el EMPLEADO
    (`nb_Empleado` o el responsable del levantamiento).
    """
    if id_tipo_activo is None:
        return ["Tipo de activo"]
    datos = datos or {}
    # Varios obligatorios se capturan en la COLUMNA del registro y no en
    # `datos_json`; se aceptan desde cualquiera de los dos sitios.
    respaldos = {"nb_NombreInsumo": nombre_insumo, "nb_Empleado": responsable,
                 "id_EmpresaResguardo": empresa, "id_SucursalResguardo": sucursal}
    faltan = []
    for campo in campos_de_tipo(id_tipo_activo):
        # El tipo NO vive en `datos` sino en su propia columna, y ya se validó
        # arriba: buscarlo aquí lo daría siempre por faltante.
        if not campo.requerido or campo.clave == "id_TipoActivo":
            continue
        valor = (datos.get(campo.clave) or respaldos.get(campo.clave) or "")
        if not str(valor).strip():
            faltan.append(campo.etiqueta.replace(" *", ""))
    return faltan
