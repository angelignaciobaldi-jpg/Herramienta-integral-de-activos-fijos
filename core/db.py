"""Persistencia local de activos fijos (SQLite, sin servidor).

La base de datos vive en la carpeta de datos del usuario ('activos_fijos.db').
El número de inventario (num_inventario) es la clave única del activo.

El esquema de abajo es una BASE razonable para arrancar el módulo de registro de
activos; amplíalo (o migra con ALTER TABLE en `inicializar`) conforme se definan
los campos definitivos con el área. El patrón de migración incremental (agregar
columnas sin romper bases existentes) ya está montado.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass

from . import rutas

RUTA_DB = os.path.join(rutas.DATOS, "activos_fijos.db")

# Estatus de un registro del levantamiento respecto al catálogo del SIPP.
EST_PENDIENTE = "pendiente"        # aún no se busca en el SIPP
EST_DADO_ALTA = "dado_de_alta"     # ya existe en el catálogo del SIPP
EST_NO_DADO_ALTA = "no_dado_de_alta"  # no existe -> hay que darlo de alta


@dataclass
class Activo:
    id: int
    num_inventario: str
    descripcion: str
    empresa: str
    categoria: str
    ubicacion: str
    marca: str
    modelo: str
    num_serie: str
    fecha_adquisicion: str
    valor_adquisicion: float | None
    estatus: str
    ruta_documento: str | None
    creado_en: str


# Columnas del activo (fuente única para INSERT/UPDATE y para las migraciones).
_COLUMNAS = [
    "num_inventario", "descripcion", "empresa", "categoria", "ubicacion",
    "marca", "modelo", "num_serie", "fecha_adquisicion", "valor_adquisicion",
    "estatus", "ruta_documento",
]


def _conectar() -> sqlite3.Connection:
    con = sqlite3.connect(RUTA_DB)
    con.row_factory = sqlite3.Row
    return con


# Esquema de la tabla del levantamiento. La CLAVE ÚNICA es `clave_unica`, que
# unifica los dos orígenes de datos (ver clave_levantamiento).
_SQL_CREAR_LEVANTAMIENTO = """
    CREATE TABLE IF NOT EXISTS {tabla} (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        empresa        TEXT,
        sucursal       TEXT,
        departamento   TEXT,
        nombre_insumo  TEXT    NOT NULL,
        etiqueta       TEXT,
        no_serie       TEXT,
        responsable    TEXT,
        ubicacion      TEXT,
        ruta_imagen    TEXT,
        estatus_registro TEXT  NOT NULL DEFAULT 'pendiente',
        id_tipo_activo INTEGER,
        datos_json     TEXT,
        factura        TEXT,
        id_activo_sipp TEXT,
        datos_sipp     TEXT,
        modificado     INTEGER NOT NULL DEFAULT 0,
        clave_unica    TEXT    NOT NULL UNIQUE,
        creado_en      TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
    )
"""


def clave_levantamiento(nombre_insumo: str, etiqueta: str = "",
                        no_serie: str = "") -> str:
    """Clave única de un registro del levantamiento.

    Unifica los dos orígenes de datos de la herramienta:
      - Carga masiva de inventario -> la ETIQUETA (número de inventario) es el
        identificador real del activo en el SIPP.
      - Carga de imágenes del levantamiento físico -> no hay etiqueta, así que se
        identifica por insumo + serie (como venía funcionando).
    """
    etiqueta = (etiqueta or "").strip()
    if etiqueta:
        return "ETQ:" + etiqueta
    return "INS:%s|%s" % ((nombre_insumo or "").strip().upper(),
                          (no_serie or "").strip().upper())


def _migrar_levantamiento_a_clave_unica(con: sqlite3.Connection,
                                        existentes: set) -> None:
    """Reconstruye `levantamiento` con el esquema nuevo conservando los datos.

    SQLite no permite cambiar una restricción UNIQUE con ALTER TABLE, así que se
    crea la tabla nueva, se copian las filas (calculando su clave_unica) y se
    reemplaza. Las filas que colisionen en la nueva clave se descartan (INSERT OR
    IGNORE): serían duplicados reales del mismo activo."""
    con.execute("DROP TABLE IF EXISTS _levantamiento_nuevo")
    con.execute(_SQL_CREAR_LEVANTAMIENTO.format(tabla="_levantamiento_nuevo"))
    # Solo se copian las columnas que existan en la tabla vieja.
    comunes = [c for c in (
        "id", "empresa", "sucursal", "departamento", "nombre_insumo", "no_serie",
        "ruta_imagen", "estatus_registro", "id_tipo_activo", "datos_json",
        "factura", "id_activo_sipp", "modificado", "creado_en",
    ) if c in existentes]
    filas = con.execute(f"SELECT {', '.join(comunes)} FROM levantamiento").fetchall()
    for fila in filas:
        d = dict(fila)
        d["clave_unica"] = clave_levantamiento(
            d.get("nombre_insumo", ""), "", d.get("no_serie", ""))
        cols = list(d.keys())
        con.execute(
            f"INSERT OR IGNORE INTO _levantamiento_nuevo ({', '.join(cols)}) "
            f"VALUES ({', '.join(['?'] * len(cols))})",
            [d[c] for c in cols])
    con.execute("DROP TABLE levantamiento")
    con.execute("ALTER TABLE _levantamiento_nuevo RENAME TO levantamiento")


def inicializar() -> None:
    """Crea la tabla de activos si no existe y aplica migraciones incrementales."""
    with _conectar() as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS activos (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                num_inventario    TEXT    NOT NULL UNIQUE,
                descripcion       TEXT    NOT NULL,
                empresa           TEXT,
                categoria         TEXT,
                ubicacion         TEXT,
                marca             TEXT,
                modelo            TEXT,
                num_serie         TEXT,
                fecha_adquisicion TEXT,
                valor_adquisicion REAL,
                estatus           TEXT    DEFAULT 'Activo',
                ruta_documento    TEXT,
                creado_en         TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
            )
            """
        )
        # Migraciones: agrega columnas nuevas a bases creadas con un esquema previo.
        existentes = {fila["name"] for fila in con.execute("PRAGMA table_info(activos)")}
        tipos = {"valor_adquisicion": "REAL"}
        for col in _COLUMNAS:
            if col not in existentes:
                con.execute(f"ALTER TABLE activos ADD COLUMN {col} {tipos.get(col, 'TEXT')}")

        # Tabla del LEVANTAMIENTO (imágenes cargadas y su estatus vs. el SIPP).
        con.execute(_SQL_CREAR_LEVANTAMIENTO.format(tabla="levantamiento"))
        # Caché local del catálogo de insumos del SIPP (para elegir el insumo real
        # sin depender del nombre del levantamiento). Es por empresa del SIPP.
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS insumos_sipp (
                id_insumo      INTEGER NOT NULL,
                empresa_id     INTEGER NOT NULL,
                empresa_nombre TEXT,
                nombre         TEXT    NOT NULL,
                unidad         TEXT,
                familia        TEXT,
                subfamilia     TEXT,
                activo_fijo    INTEGER NOT NULL DEFAULT 0,
                seriado        INTEGER NOT NULL DEFAULT 0,
                actualizado_en TEXT,
                PRIMARY KEY (id_insumo, empresa_id)
            )
            """
        )
        con.execute("CREATE INDEX IF NOT EXISTS ix_insumos_nombre "
                    "ON insumos_sipp (nombre)")

        # Caché del catálogo de EMPLEADOS del SIPP (global, para el resguardo).
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS empleados_sipp (
                id_empleado    INTEGER PRIMARY KEY,
                nombre         TEXT    NOT NULL,
                puesto         TEXT,
                email          TEXT,
                actualizado_en TEXT
            )
            """
        )
        con.execute("CREATE INDEX IF NOT EXISTS ix_empleados_nombre "
                    "ON empleados_sipp (nombre)")

        # Caché de los ACTIVOS del SIPP por empresa (para generar sus QR/etiquetas).
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS activos_sipp (
                id_empresa     INTEGER NOT NULL,
                empresa_nombre TEXT,
                etiqueta       TEXT    NOT NULL,
                insumo         TEXT,
                serie          TEXT,
                ubicacion      TEXT,
                empleado       TEXT,
                sucursal       TEXT,
                departamento   TEXT,
                id_tipo        INTEGER,
                tipo           TEXT,
                extra          TEXT,
                actualizado_en TEXT,
                PRIMARY KEY (id_empresa, etiqueta)
            )
            """
        )
        # Migración: las bases creadas antes de estas columnas no las tienen (la
        # tabla ya existía y CREATE IF NOT EXISTS no las agrega).
        cols_sipp = {fila["name"] for fila in con.execute("PRAGMA table_info(activos_sipp)")}
        for col, tipo_sql in (("sucursal", "TEXT"), ("departamento", "TEXT"),
                              ("id_tipo", "INTEGER"), ("tipo", "TEXT"),
                              ("extra", "TEXT")):
            if col not in cols_sipp:
                con.execute(f"ALTER TABLE activos_sipp ADD COLUMN {col} {tipo_sql}")

        # Catálogos del SIPP para el alta/modificación (por empresa). Departamento
        # es por empresa; grupo y centro de costo son por SUCURSAL (el grupo guarda
        # su sucursal normalizada para casar con la del activo), y el centro depende
        # del grupo.
        con.execute(
            """CREATE TABLE IF NOT EXISTS departamentos_sipp (
                id_empresa     INTEGER NOT NULL,
                id_departamento INTEGER NOT NULL,
                nb_departamento TEXT,
                PRIMARY KEY (id_empresa, id_departamento))""")
        con.execute(
            """CREATE TABLE IF NOT EXISTS grupos_cc_sipp (
                id_empresa    INTEGER NOT NULL,
                id_grupo      INTEGER NOT NULL,
                nb_grupo      TEXT,
                id_sucursal   INTEGER,
                sucursal_norm TEXT,
                PRIMARY KEY (id_empresa, id_grupo))""")
        con.execute(
            """CREATE TABLE IF NOT EXISTS centros_cc_sipp (
                id_empresa    INTEGER NOT NULL,
                id_grupo      INTEGER NOT NULL,
                id_centro     INTEGER NOT NULL,
                nb_centro     TEXT,
                PRIMARY KEY (id_empresa, id_grupo, id_centro))""")

        existentes_lev = {fila["name"] for fila in con.execute("PRAGMA table_info(levantamiento)")}
        if existentes_lev and "clave_unica" not in existentes_lev:
            # Esquema viejo: la clave única era (no_serie, nombre_insumo), que no
            # sirve para el inventario masivo (el 82% de los activos NO tiene
            # serie y la clave real es la ETIQUETA). SQLite no permite cambiar un
            # UNIQUE con ALTER, así que se reconstruye la tabla conservando datos.
            _migrar_levantamiento_a_clave_unica(con, existentes_lev)
        else:
            tipos_lev = {"id_tipo_activo": "INTEGER", "modificado": "INTEGER"}
            for col in _COLS_LEV:
                if col not in existentes_lev:
                    con.execute(
                        f"ALTER TABLE levantamiento ADD COLUMN {col} "
                        f"{tipos_lev.get(col, 'TEXT')}")
        # Índice por estatus: acelera el conteo por pestaña y el filtrado (la tabla
        # puede tener miles de registros y se consulta en cada cambio de pestaña).
        con.execute("CREATE INDEX IF NOT EXISTS ix_lev_estatus "
                    "ON levantamiento(estatus_registro)")


class InventarioDuplicado(Exception):
    """Ya existe un activo con ese número de inventario."""


def guardar(**campos) -> int:
    """Inserta un activo. Devuelve su id. Lanza InventarioDuplicado si el
    num_inventario ya existe. Acepta las columnas de _COLUMNAS como keyword args."""
    valores = [campos.get(c) for c in _COLUMNAS]
    marcadores = ", ".join(["?"] * len(_COLUMNAS))
    try:
        with _conectar() as con:
            cur = con.execute(
                f"INSERT INTO activos ({', '.join(_COLUMNAS)}) VALUES ({marcadores})",
                valores,
            )
            return cur.lastrowid
    except sqlite3.IntegrityError as exc:
        raise InventarioDuplicado(campos.get("num_inventario")) from exc


def actualizar(id_activo: int, **campos) -> None:
    """Modifica un activo existente. Lanza InventarioDuplicado si el nuevo
    num_inventario ya pertenece a otro registro."""
    asignaciones = ", ".join(f"{c} = ?" for c in _COLUMNAS)
    valores = [campos.get(c) for c in _COLUMNAS] + [id_activo]
    try:
        with _conectar() as con:
            con.execute(f"UPDATE activos SET {asignaciones} WHERE id = ?", valores)
    except sqlite3.IntegrityError as exc:
        raise InventarioDuplicado(campos.get("num_inventario")) from exc


def listar() -> list[Activo]:
    with _conectar() as con:
        filas = con.execute(
            "SELECT * FROM activos ORDER BY creado_en DESC, id DESC"
        ).fetchall()
    return [Activo(**dict(f)) for f in filas]


def eliminar(id_activo: int) -> None:
    with _conectar() as con:
        con.execute("DELETE FROM activos WHERE id = ?", (id_activo,))


# ===========================================================================
# LEVANTAMIENTO (imágenes cargadas del levantamiento físico + estatus vs. SIPP)
# ===========================================================================

@dataclass
class Levantamiento:
    id: int
    empresa: str | None
    sucursal: str | None
    departamento: str | None
    nombre_insumo: str
    etiqueta: str | None
    no_serie: str | None
    responsable: str | None
    ubicacion: str | None
    ruta_imagen: str | None
    estatus_registro: str
    id_tipo_activo: int | None
    datos_json: str | None
    factura: str | None
    id_activo_sipp: str | None
    modificado: int
    clave_unica: str
    creado_en: str
    datos_sipp: str | None = None

    def identificador(self) -> str:
        """Con qué se busca este activo en el SIPP: la etiqueta (número de
        inventario) y, si no tiene, el número de serie."""
        return (self.etiqueta or "").strip() or (self.no_serie or "").strip()

    def datos(self) -> dict:
        """Campos de alta capturados (datos_json deserializado; {} si vacío)."""
        if not self.datos_json:
            return {}
        try:
            valor = json.loads(self.datos_json)
            return valor if isinstance(valor, dict) else {}
        except (ValueError, TypeError):
            return {}

    def info_sipp(self) -> dict:
        """Datos REALES del activo en el SIPP (los que trae el catálogo), para
        consultarlos cuando el activo está dado de alta. {} si no hay."""
        if not self.datos_sipp:
            return {}
        try:
            valor = json.loads(self.datos_sipp)
            return valor if isinstance(valor, dict) else {}
        except (ValueError, TypeError):
            return {}


# Columnas del levantamiento (fuente única para migraciones incrementales).
_COLS_LEV = [
    "empresa", "sucursal", "departamento",
    "nombre_insumo", "etiqueta", "no_serie", "responsable", "ubicacion",
    "ruta_imagen", "estatus_registro",
    "id_tipo_activo", "datos_json", "factura", "id_activo_sipp", "datos_sipp",
    "modificado",
]


def guardar_levantamiento(nombre_insumo: str, no_serie: str = "",
                          ruta_imagen: str | None = None,
                          empresa: str = "", sucursal: str = "",
                          departamento: str = "", etiqueta: str = "",
                          responsable: str = "", ubicacion: str = "") -> int | None:
    """Inserta un registro del levantamiento. Devuelve su id, o None si ya existía
    otro con la misma clave (ver clave_levantamiento): misma ETIQUETA, o mismo
    insumo+serie cuando no hay etiqueta. En ese caso se ignora (no duplica)."""
    clave = clave_levantamiento(nombre_insumo, etiqueta, no_serie)
    try:
        with _conectar() as con:
            cur = con.execute(
                """INSERT INTO levantamiento
                   (empresa, sucursal, departamento, nombre_insumo, etiqueta,
                    no_serie, responsable, ubicacion, ruta_imagen, clave_unica)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (empresa, sucursal, departamento, nombre_insumo, etiqueta or None,
                 no_serie, responsable, ubicacion, ruta_imagen, clave),
            )
            return cur.lastrowid
    except sqlite3.IntegrityError:
        return None  # ya existe ese activo: no se duplica


def guardar_levantamiento_lote(registros: list[dict]) -> tuple[int, int]:
    """Inserta muchos registros del levantamiento en UNA sola transacción.

    Pensado para la carga masiva desde Excel (miles de filas): abrir una conexión
    por registro es órdenes de magnitud más lento. Los que choquen con la clave
    única se ignoran (son el mismo activo). Devuelve (agregados, duplicados).

    Cada dict acepta: nombre_insumo (obligatorio), etiqueta, no_serie,
    responsable, ubicacion, empresa, sucursal, departamento, ruta_imagen.
    """
    if not registros:
        return 0, 0
    filas = []
    for r in registros:
        insumo = r.get("nombre_insumo", "")
        etiqueta = (r.get("etiqueta") or "").strip()
        serie = r.get("no_serie", "") or ""
        filas.append((
            r.get("empresa", ""), r.get("sucursal", ""), r.get("departamento", ""),
            insumo, etiqueta or None, serie, r.get("responsable", ""),
            r.get("ubicacion", ""), r.get("ruta_imagen"),
            clave_levantamiento(insumo, etiqueta, serie),
        ))
    with _conectar() as con:
        antes = con.execute("SELECT COUNT(*) FROM levantamiento").fetchone()[0]
        con.executemany(
            """INSERT OR IGNORE INTO levantamiento
               (empresa, sucursal, departamento, nombre_insumo, etiqueta,
                no_serie, responsable, ubicacion, ruta_imagen, clave_unica)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            filas)
        despues = con.execute("SELECT COUNT(*) FROM levantamiento").fetchone()[0]
    agregados = despues - antes
    return agregados, len(filas) - agregados


def actualizar_ubicacion_levantamiento(id_lev: int, empresa: str | None = None,
                                       sucursal: str | None = None,
                                       departamento: str | None = None) -> None:
    """Actualiza la empresa/sucursal/departamento de un registro (edición por fila).
    Solo toca los argumentos que se pasen (los None se omiten)."""
    sets, valores = [], []
    if empresa is not None:
        sets.append("empresa = ?"); valores.append(empresa)
    if sucursal is not None:
        sets.append("sucursal = ?"); valores.append(sucursal)
    if departamento is not None:
        sets.append("departamento = ?"); valores.append(departamento)
    if not sets:
        return
    valores.append(id_lev)
    with _conectar() as con:
        con.execute(f"UPDATE levantamiento SET {', '.join(sets)} WHERE id = ?", valores)


def listar_levantamiento() -> list[Levantamiento]:
    with _conectar() as con:
        filas = con.execute(
            "SELECT * FROM levantamiento ORDER BY creado_en DESC, id DESC"
        ).fetchall()
    return [Levantamiento(**dict(f)) for f in filas]


def listar_levantamiento_por_estatus(estatus: str) -> list[Levantamiento]:
    with _conectar() as con:
        filas = con.execute(
            "SELECT * FROM levantamiento WHERE estatus_registro = ? "
            "ORDER BY creado_en DESC, id DESC",
            (estatus,),
        ).fetchall()
    return [Levantamiento(**dict(f)) for f in filas]


def actualizar_estatus_levantamiento(id_lev: int, estatus: str,
                                     id_activo_sipp: str | None = None,
                                     datos_sipp: dict | None = None) -> None:
    """Fija el estatus (pendiente/dado_de_alta/no_dado_de_alta) y, si aplica, el
    id y los datos del activo en el SIPP. `datos_sipp` se guarda como JSON (o se
    limpia con {} / None) para poder consultarlo después."""
    dj = json.dumps(datos_sipp, ensure_ascii=False) if datos_sipp else None
    with _conectar() as con:
        con.execute(
            "UPDATE levantamiento SET estatus_registro = ?, id_activo_sipp = ?, "
            "datos_sipp = ? WHERE id = ?",
            (estatus, id_activo_sipp, dj, id_lev),
        )


def fijar_etiqueta_levantamiento(id_lev: int, etiqueta: str) -> None:
    """Fija la ETIQUETA de un registro (p. ej. al adoptar la del SIPP tras una
    coincidencia parcial). No toca clave_unica (evita colisiones del UNIQUE)."""
    with _conectar() as con:
        con.execute("UPDATE levantamiento SET etiqueta = ? WHERE id = ?",
                    (etiqueta or None, id_lev))


def actualizar_datos_levantamiento(id_lev: int, id_tipo_activo: int | None = None,
                                   datos: dict | None = None, factura: str | None = None,
                                   modificado: bool | None = None,
                                   no_serie: str | None = None) -> None:
    """Actualiza los campos de captura del alta (tipo, datos_json, factura), la
    marca de modificado y/o el No. de serie. Solo toca los argumentos que se pasen.

    `no_serie` se refleja en la COLUMNA del registro (no solo en datos_json), que
    es la que se muestra en la tabla y con la que se busca en el SIPP/bandeja."""
    sets, valores = [], []
    if id_tipo_activo is not None:
        sets.append("id_tipo_activo = ?"); valores.append(id_tipo_activo)
    if datos is not None:
        sets.append("datos_json = ?"); valores.append(json.dumps(datos, ensure_ascii=False))
    if factura is not None:
        sets.append("factura = ?"); valores.append(factura)
    if modificado is not None:
        sets.append("modificado = ?"); valores.append(1 if modificado else 0)
    if no_serie is not None:
        sets.append("no_serie = ?"); valores.append(no_serie)
    if not sets:
        return
    valores.append(id_lev)
    with _conectar() as con:
        con.execute(f"UPDATE levantamiento SET {', '.join(sets)} WHERE id = ?", valores)


# Filtros por columna (estilo Excel). Categóricos = coincidencia EXACTA (para los
# desplegables de valores distintos); de texto = CONTIENE. Lista blanca: solo estas
# columnas son filtrables (evita inyección al construir el SQL con el nombre).
_FILTRO_EXACTO = ("empresa", "sucursal", "departamento")
_FILTRO_CONTIENE = ("nombre_insumo", "etiqueta", "no_serie", "ubicacion")
COLUMNAS_FILTRABLES = _FILTRO_EXACTO + _FILTRO_CONTIENE


def _filtro_sql(estatus: str | None, filtro: str,
                filtros: dict | None = None) -> tuple[str, list]:
    """Arma el WHERE compartido por las consultas paginadas del levantamiento.

    `filtro` es la búsqueda global (varios campos). `filtros` son los filtros por
    columna: {columna: valor} — exacto para las categóricas, contiene para texto.
    """
    cond, params = [], []
    if estatus:
        cond.append("estatus_registro = ?")
        params.append(estatus)
    if filtro:
        like = f"%{filtro.strip().lower()}%"
        cond.append("(LOWER(nombre_insumo) LIKE ? OR LOWER(IFNULL(etiqueta,'')) LIKE ?"
                    " OR LOWER(IFNULL(no_serie,'')) LIKE ?"
                    " OR LOWER(IFNULL(ubicacion,'')) LIKE ?)")
        params += [like] * 4
    for col, val in (filtros or {}).items():
        val = str(val or "").strip()
        if not val or col not in COLUMNAS_FILTRABLES:
            continue
        if col in _FILTRO_EXACTO:
            cond.append(f"IFNULL({col},'') = ?")
            params.append(val)
        else:
            cond.append(f"LOWER(IFNULL({col},'')) LIKE ?")
            params.append(f"%{val.lower()}%")
    return (" WHERE " + " AND ".join(cond)) if cond else "", params


def valores_distintos_levantamiento(columna: str) -> list[str]:
    """Valores distintos (no vacíos) de una columna filtrable, para poblar los
    desplegables de filtro. Devuelve [] si la columna no es filtrable."""
    if columna not in COLUMNAS_FILTRABLES:
        return []
    with _conectar() as con:
        filas = con.execute(
            f"SELECT DISTINCT {columna} AS v FROM levantamiento "
            f"WHERE IFNULL({columna},'') <> '' ORDER BY {columna}").fetchall()
    return [f["v"] for f in filas]


_ORDEN_LEV = " ORDER BY creado_en DESC, id DESC"


def listar_levantamiento_pagina(estatus: str | None = None, filtro: str = "",
                                limite: int = 25, offset: int = 0,
                                filtros: dict | None = None) -> list[Levantamiento]:
    """Devuelve SOLO la página pedida. Con inventarios de miles de activos,
    materializar la tabla completa para mostrar 25 filas es el mayor costo de la
    pantalla; aquí el filtrado y el recorte los hace SQLite."""
    where, params = _filtro_sql(estatus, filtro, filtros)
    with _conectar() as con:
        filas = con.execute(
            f"SELECT * FROM levantamiento{where}{_ORDEN_LEV} LIMIT ? OFFSET ?",
            [*params, limite, offset]).fetchall()
    return [Levantamiento(**dict(f)) for f in filas]


def contar_levantamiento(estatus: str | None = None, filtro: str = "",
                         filtros: dict | None = None) -> int:
    """Cuántos registros cumplen el filtro (para la paginación)."""
    where, params = _filtro_sql(estatus, filtro, filtros)
    with _conectar() as con:
        return con.execute(
            f"SELECT COUNT(*) FROM levantamiento{where}", params).fetchone()[0]


def ids_levantamiento(estatus: str | None = None, filtro: str = "",
                      filtros: dict | None = None) -> list[int]:
    """Ids de todos los registros que cumplen el filtro (para 'Seleccionar todos'
    sin traer las filas completas)."""
    where, params = _filtro_sql(estatus, filtro, filtros)
    with _conectar() as con:
        return [f[0] for f in con.execute(
            f"SELECT id FROM levantamiento{where}", params).fetchall()]


def contar_levantamiento_por_estatus() -> dict[str, int]:
    """Devuelve {estatus: cantidad} más 'total'. Es una sola consulta agregada:
    con miles de registros, listar la tabla completa solo para contarla es caro."""
    with _conectar() as con:
        filas = con.execute(
            "SELECT estatus_registro, COUNT(*) AS n FROM levantamiento "
            "GROUP BY estatus_registro").fetchall()
    conteos = {f["estatus_registro"]: f["n"] for f in filas}
    conteos["total"] = sum(conteos.values())
    return conteos


def actualizar_tipo_lote(ids: list[int], id_tipo: int | None) -> int:
    """Asigna el mismo tipo de activo a muchos registros de una vez.

    Pensado para el inventario importado, que llega sin tipo: seleccionar cientos
    de activos y clasificarlos de golpe. Devuelve cuántos se actualizaron.

    Los ids se procesan por bloques porque SQLite limita el número de parámetros
    de una sola sentencia."""
    if not ids:
        return 0
    TAM = 500
    with _conectar() as con:
        for i in range(0, len(ids), TAM):
            bloque = ids[i:i + TAM]
            marcadores = ", ".join(["?"] * len(bloque))
            con.execute(
                f"UPDATE levantamiento SET id_tipo_activo = ? WHERE id IN ({marcadores})",
                [id_tipo, *bloque])
    return len(ids)


def eliminar_levantamiento(id_lev: int) -> None:
    with _conectar() as con:
        con.execute("DELETE FROM levantamiento WHERE id = ?", (id_lev,))


# ===========================================================================
# CATÁLOGO DE INSUMOS DEL SIPP (caché local, por empresa)
# ===========================================================================

@dataclass
class Insumo:
    id_insumo: int
    empresa_id: int
    empresa_nombre: str | None
    nombre: str
    unidad: str | None
    familia: str | None
    subfamilia: str | None
    activo_fijo: int
    seriado: int


def reemplazar_insumos(empresa_id: int, empresa_nombre: str,
                       registros: list[dict], actualizado_en: str) -> int:
    """Reemplaza el catálogo de insumos cacheado de una empresa por `registros`.

    Cada dict: id_insumo, nombre, unidad, familia, subfamilia, activo_fijo,
    seriado. Se hace en una transacción (borrar + insertar en lote). Devuelve
    cuántos se guardaron."""
    with _conectar() as con:
        con.execute("DELETE FROM insumos_sipp WHERE empresa_id = ?", (empresa_id,))
        con.executemany(
            """INSERT OR REPLACE INTO insumos_sipp
               (id_insumo, empresa_id, empresa_nombre, nombre, unidad, familia,
                subfamilia, activo_fijo, seriado, actualizado_en)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(r["id_insumo"], empresa_id, empresa_nombre, r["nombre"], r.get("unidad"),
              r.get("familia"), r.get("subfamilia"), 1 if r.get("activo_fijo") else 0,
              1 if r.get("seriado") else 0, actualizado_en) for r in registros])
    return len(registros)


def buscar_insumos(texto: str = "", empresa_id: int | None = None,
                   solo_activo_fijo: bool = False, limite: int = 50) -> list[Insumo]:
    """Busca insumos en el catálogo cacheado por nombre o por id (Cve Insumo)."""
    cond, params = [], []
    if empresa_id is not None:
        cond.append("empresa_id = ?"); params.append(empresa_id)
    if solo_activo_fijo:
        cond.append("activo_fijo = 1")
    texto = (texto or "").strip()
    if texto:
        if texto.isdigit():
            cond.append("(CAST(id_insumo AS TEXT) LIKE ? OR LOWER(nombre) LIKE ?)")
            params += [f"{texto}%", f"%{texto.lower()}%"]
        else:
            cond.append("LOWER(nombre) LIKE ?"); params.append(f"%{texto.lower()}%")
    where = (" WHERE " + " AND ".join(cond)) if cond else ""
    # El mismo insumo (Cve) puede estar cacheado para varias empresas; el catálogo
    # es prácticamente global, así que se DEDUPLICA por id_insumo para no mostrarlo
    # repetido en el selector.
    with _conectar() as con:
        filas = con.execute(
            f"SELECT id_insumo, empresa_id, empresa_nombre, nombre, unidad, familia, "
            f"subfamilia, activo_fijo, seriado FROM insumos_sipp{where} "
            f"GROUP BY id_insumo ORDER BY nombre LIMIT ?", [*params, limite]).fetchall()
    return [Insumo(**dict(f)) for f in filas]


def estado_catalogo_insumos() -> list[dict]:
    """Por cada empresa cacheada: id, nombre, cuántos insumos y cuándo se bajó."""
    with _conectar() as con:
        filas = con.execute(
            "SELECT empresa_id, empresa_nombre, COUNT(*) AS n, MAX(actualizado_en) AS cuando "
            "FROM insumos_sipp GROUP BY empresa_id, empresa_nombre "
            "ORDER BY empresa_nombre").fetchall()
    return [dict(f) for f in filas]


# --------------------------------------------------------- empleados (SIPP)
@dataclass
class Empleado:
    id_empleado: int
    nombre: str
    puesto: str | None
    email: str | None


def reemplazar_empleados(registros: list[dict], actualizado_en: str) -> int:
    """Reemplaza TODO el catálogo de empleados cacheado (es global, no por empresa).
    Cada dict: id_empleado, nombre, puesto, email. Devuelve cuántos se guardaron."""
    with _conectar() as con:
        con.execute("DELETE FROM empleados_sipp")
        con.executemany(
            """INSERT OR REPLACE INTO empleados_sipp
               (id_empleado, nombre, puesto, email, actualizado_en)
               VALUES (?, ?, ?, ?, ?)""",
            [(r["id_empleado"], r["nombre"], r.get("puesto"), r.get("email"),
              actualizado_en) for r in registros])
    return len(registros)


def buscar_empleados(texto: str = "", limite: int = 50) -> list[Empleado]:
    """Busca empleados en el catálogo cacheado por nombre o por id."""
    cond, params = [], []
    texto = (texto or "").strip()
    if texto:
        if texto.isdigit():
            cond.append("(CAST(id_empleado AS TEXT) LIKE ? OR LOWER(nombre) LIKE ?)")
            params += [f"{texto}%", f"%{texto.lower()}%"]
        else:
            cond.append("LOWER(nombre) LIKE ?"); params.append(f"%{texto.lower()}%")
    where = (" WHERE " + " AND ".join(cond)) if cond else ""
    with _conectar() as con:
        filas = con.execute(
            f"SELECT id_empleado, nombre, puesto, email FROM empleados_sipp{where} "
            f"ORDER BY nombre LIMIT ?", [*params, limite]).fetchall()
    return [Empleado(**dict(f)) for f in filas]


def estado_catalogo_empleados() -> dict:
    """Cuántos empleados hay cacheados y cuándo se bajaron."""
    with _conectar() as con:
        fila = con.execute(
            "SELECT COUNT(*) AS n, MAX(actualizado_en) AS cuando "
            "FROM empleados_sipp").fetchone()
    return dict(fila)


# --------------------------------------------------- activos del SIPP (por empresa)
def reemplazar_activos_sipp(id_empresa: int, empresa_nombre: str,
                            registros: list[dict], actualizado_en: str) -> int:
    """Reemplaza los activos cacheados de una empresa. Cada dict: etiqueta
    (obligatorio), insumo, serie, ubicacion, empleado. Devuelve cuántos se
    guardaron (con etiqueta no vacía)."""
    filas = [r for r in registros if (r.get("etiqueta") or "").strip()]
    with _conectar() as con:
        con.execute("DELETE FROM activos_sipp WHERE id_empresa = ?", (id_empresa,))
        con.executemany(
            """INSERT OR REPLACE INTO activos_sipp
               (id_empresa, empresa_nombre, etiqueta, insumo, serie, ubicacion,
                empleado, sucursal, departamento, id_tipo, tipo, extra, actualizado_en)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(id_empresa, empresa_nombre, r["etiqueta"].strip(), r.get("insumo"),
              r.get("serie"), r.get("ubicacion"), r.get("empleado"),
              r.get("sucursal"), r.get("departamento"),
              r.get("id_tipo"), r.get("tipo"),
              json.dumps(r.get("extra"), ensure_ascii=False) if r.get("extra") else None,
              actualizado_en)
             for r in filas])
    return len(filas)


def listar_activos_sipp(id_empresa: int, sucursal: str | None = None) -> list[dict]:
    """Activos cacheados de una empresa (para generar sus QR/etiquetas y consultar
    el detalle). Si se pasa `sucursal`, filtra por ella. Los campos EXTRA (guardados
    como JSON) se fusionan en el dict de cada activo."""
    cond, params = ["id_empresa = ?"], [id_empresa]
    if sucursal:
        cond.append("IFNULL(sucursal,'') = ?"); params.append(sucursal)
    with _conectar() as con:
        filas = con.execute(
            "SELECT empresa_nombre, etiqueta, insumo, serie, ubicacion, empleado, "
            "sucursal, departamento, id_tipo, tipo, extra FROM activos_sipp "
            f"WHERE {' AND '.join(cond)} ORDER BY etiqueta", params).fetchall()
    activos = []
    for f in filas:
        base = {"empresa": f["empresa_nombre"], "etiqueta": f["etiqueta"],
                "insumo": f["insumo"], "serie": f["serie"],
                "ubicacion": f["ubicacion"], "empleado": f["empleado"],
                "sucursal": f["sucursal"], "departamento": f["departamento"],
                "id_tipo": f["id_tipo"], "tipo": f["tipo"]}
        if f["extra"]:
            try:
                extra = json.loads(f["extra"])
                if isinstance(extra, dict):
                    base.update(extra)
            except (ValueError, TypeError):
                pass
        activos.append(base)
    return activos


def sucursales_activos_sipp(id_empresa: int) -> list[str]:
    """Sucursales distintas presentes en los activos cacheados de una empresa."""
    with _conectar() as con:
        filas = con.execute(
            "SELECT DISTINCT sucursal FROM activos_sipp "
            "WHERE id_empresa = ? AND IFNULL(sucursal,'') <> '' ORDER BY sucursal",
            (id_empresa,)).fetchall()
    return [f["sucursal"] for f in filas]


def estado_activos_sipp() -> list[dict]:
    """Por empresa cacheada: id, nombre, cuántos activos y cuándo se bajaron."""
    with _conectar() as con:
        filas = con.execute(
            "SELECT id_empresa, empresa_nombre, COUNT(*) AS n, MAX(actualizado_en) AS cuando "
            "FROM activos_sipp GROUP BY id_empresa, empresa_nombre "
            "ORDER BY empresa_nombre").fetchall()
    return [dict(f) for f in filas]


# --------------------------------------- catálogos de alta (depto / centro costo)
def _norm_suc(texto) -> str:
    """Normaliza un nombre de sucursal para casar el del activo con el del catálogo
    (mayúsculas, sin acentos ni espacios sobrantes)."""
    t = str(texto or "").strip().upper()
    for a, b in (("Á", "A"), ("É", "E"), ("Í", "I"), ("Ó", "O"), ("Ú", "U"), ("Ñ", "N")):
        t = t.replace(a, b)
    return " ".join(t.split())


def reemplazar_departamentos(id_empresa: int, registros: list[dict]) -> int:
    """Reemplaza los departamentos cacheados de una empresa. Cada dict:
    id_departamento, nb_departamento."""
    with _conectar() as con:
        con.execute("DELETE FROM departamentos_sipp WHERE id_empresa = ?", (id_empresa,))
        con.executemany(
            "INSERT OR REPLACE INTO departamentos_sipp "
            "(id_empresa, id_departamento, nb_departamento) VALUES (?, ?, ?)",
            [(id_empresa, r["id_departamento"], r.get("nb_departamento"))
             for r in registros if r.get("id_departamento") is not None])
    return len(registros)


def listar_departamentos(id_empresa: int) -> list[str]:
    """Nombres de departamento de una empresa (para el desplegable del alta)."""
    with _conectar() as con:
        filas = con.execute(
            "SELECT nb_departamento FROM departamentos_sipp "
            "WHERE id_empresa = ? AND IFNULL(nb_departamento,'') <> '' "
            "ORDER BY nb_departamento", (id_empresa,)).fetchall()
    return [f["nb_departamento"] for f in filas]


def reemplazar_grupos_cc(id_empresa: int, registros: list[dict]) -> int:
    """Reemplaza los grupos de centro de costo de una empresa. Cada dict:
    id_grupo, nb_grupo, id_sucursal, sucursal (nombre; se normaliza)."""
    with _conectar() as con:
        con.execute("DELETE FROM grupos_cc_sipp WHERE id_empresa = ?", (id_empresa,))
        con.executemany(
            "INSERT OR REPLACE INTO grupos_cc_sipp "
            "(id_empresa, id_grupo, nb_grupo, id_sucursal, sucursal_norm) "
            "VALUES (?, ?, ?, ?, ?)",
            [(id_empresa, r["id_grupo"], r.get("nb_grupo"), r.get("id_sucursal"),
              _norm_suc(r.get("sucursal")))
             for r in registros if r.get("id_grupo") is not None])
    return len(registros)


def listar_grupos_cc(id_empresa: int, sucursal: str) -> list[dict]:
    """Grupos de centro de costo de una empresa para la sucursal dada (por nombre,
    normalizado). Devuelve [{id_grupo, nb_grupo}]."""
    with _conectar() as con:
        filas = con.execute(
            "SELECT id_grupo, nb_grupo FROM grupos_cc_sipp "
            "WHERE id_empresa = ? AND sucursal_norm = ? "
            "AND IFNULL(nb_grupo,'') <> '' ORDER BY nb_grupo",
            (id_empresa, _norm_suc(sucursal))).fetchall()
    return [{"id_grupo": f["id_grupo"], "nb_grupo": f["nb_grupo"]} for f in filas]


def reemplazar_centros_cc(id_empresa: int, registros: list[dict]) -> int:
    """Reemplaza los centros de costo de una empresa. Cada dict:
    id_grupo, id_centro, nb_centro."""
    with _conectar() as con:
        con.execute("DELETE FROM centros_cc_sipp WHERE id_empresa = ?", (id_empresa,))
        con.executemany(
            "INSERT OR REPLACE INTO centros_cc_sipp "
            "(id_empresa, id_grupo, id_centro, nb_centro) VALUES (?, ?, ?, ?)",
            [(id_empresa, r["id_grupo"], r["id_centro"], r.get("nb_centro"))
             for r in registros
             if r.get("id_grupo") is not None and r.get("id_centro") is not None])
    return len(registros)


def listar_centros_cc(id_empresa: int, id_grupo: int) -> list[str]:
    """Nombres de centro de costo de un grupo (para el desplegable dependiente)."""
    with _conectar() as con:
        filas = con.execute(
            "SELECT nb_centro FROM centros_cc_sipp "
            "WHERE id_empresa = ? AND id_grupo = ? AND IFNULL(nb_centro,'') <> '' "
            "ORDER BY nb_centro", (id_empresa, id_grupo)).fetchall()
    return [f["nb_centro"] for f in filas]


def eliminar_levantamientos(ids: list[int]) -> None:
    """Elimina varios registros del levantamiento en una sola transacción."""
    if not ids:
        return
    with _conectar() as con:
        con.executemany("DELETE FROM levantamiento WHERE id = ?", [(i,) for i in ids])
