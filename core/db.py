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
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS levantamiento (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                empresa        TEXT,
                sucursal       TEXT,
                departamento   TEXT,
                nombre_insumo  TEXT    NOT NULL,
                no_serie       TEXT    NOT NULL,
                ruta_imagen    TEXT,
                estatus_registro TEXT  NOT NULL DEFAULT 'pendiente',
                id_tipo_activo INTEGER,
                datos_json     TEXT,
                factura        TEXT,
                id_activo_sipp TEXT,
                modificado     INTEGER NOT NULL DEFAULT 0,
                creado_en      TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
                UNIQUE (no_serie, nombre_insumo)
            )
            """
        )
        existentes_lev = {fila["name"] for fila in con.execute("PRAGMA table_info(levantamiento)")}
        tipos_lev = {"id_tipo_activo": "INTEGER", "modificado": "INTEGER"}
        for col in _COLS_LEV:
            if col not in existentes_lev:
                con.execute(
                    f"ALTER TABLE levantamiento ADD COLUMN {col} {tipos_lev.get(col, 'TEXT')}")


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
    no_serie: str
    ruta_imagen: str | None
    estatus_registro: str
    id_tipo_activo: int | None
    datos_json: str | None
    factura: str | None
    id_activo_sipp: str | None
    modificado: int
    creado_en: str

    def datos(self) -> dict:
        """Campos de alta capturados (datos_json deserializado; {} si vacío)."""
        if not self.datos_json:
            return {}
        try:
            valor = json.loads(self.datos_json)
            return valor if isinstance(valor, dict) else {}
        except (ValueError, TypeError):
            return {}


# Columnas del levantamiento (fuente única para migraciones incrementales).
_COLS_LEV = [
    "empresa", "sucursal", "departamento",
    "nombre_insumo", "no_serie", "ruta_imagen", "estatus_registro",
    "id_tipo_activo", "datos_json", "factura", "id_activo_sipp", "modificado",
]


def guardar_levantamiento(nombre_insumo: str, no_serie: str,
                          ruta_imagen: str | None = None,
                          empresa: str = "", sucursal: str = "",
                          departamento: str = "") -> int | None:
    """Inserta un registro del levantamiento (con su empresa/sucursal/departamento).
    Devuelve su id, o None si ya existía uno con la misma (no_serie, nombre_insumo)
    — en tal caso se ignora (no duplica)."""
    try:
        with _conectar() as con:
            cur = con.execute(
                """INSERT INTO levantamiento
                   (empresa, sucursal, departamento, nombre_insumo, no_serie, ruta_imagen)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (empresa, sucursal, departamento, nombre_insumo, no_serie, ruta_imagen),
            )
            return cur.lastrowid
    except sqlite3.IntegrityError:
        return None  # ya existe esa (serie, insumo): no se duplica


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
                                     id_activo_sipp: str | None = None) -> None:
    """Fija el estatus (pendiente/dado_de_alta/no_dado_de_alta) y, si aplica, el
    id del activo en el SIPP."""
    with _conectar() as con:
        con.execute(
            "UPDATE levantamiento SET estatus_registro = ?, id_activo_sipp = ? WHERE id = ?",
            (estatus, id_activo_sipp, id_lev),
        )


def actualizar_datos_levantamiento(id_lev: int, id_tipo_activo: int | None = None,
                                   datos: dict | None = None, factura: str | None = None,
                                   modificado: bool | None = None) -> None:
    """Actualiza los campos de captura del alta (tipo, datos_json, factura) y/o la
    marca de modificado. Solo toca los argumentos que se pasen (los None se omiten)."""
    sets, valores = [], []
    if id_tipo_activo is not None:
        sets.append("id_tipo_activo = ?"); valores.append(id_tipo_activo)
    if datos is not None:
        sets.append("datos_json = ?"); valores.append(json.dumps(datos, ensure_ascii=False))
    if factura is not None:
        sets.append("factura = ?"); valores.append(factura)
    if modificado is not None:
        sets.append("modificado = ?"); valores.append(1 if modificado else 0)
    if not sets:
        return
    valores.append(id_lev)
    with _conectar() as con:
        con.execute(f"UPDATE levantamiento SET {', '.join(sets)} WHERE id = ?", valores)


def eliminar_levantamiento(id_lev: int) -> None:
    with _conectar() as con:
        con.execute("DELETE FROM levantamiento WHERE id = ?", (id_lev,))


def eliminar_levantamientos(ids: list[int]) -> None:
    """Elimina varios registros del levantamiento en una sola transacción."""
    if not ids:
        return
    with _conectar() as con:
        con.executemany("DELETE FROM levantamiento WHERE id = ?", [(i,) for i in ids])
