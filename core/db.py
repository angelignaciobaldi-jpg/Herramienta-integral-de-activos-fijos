"""Persistencia local de activos fijos (SQLite, sin servidor).

La base de datos vive en la carpeta de datos del usuario ('activos_fijos.db').
El número de inventario (num_inventario) es la clave única del activo.

El esquema de abajo es una BASE razonable para arrancar el módulo de registro de
activos; amplíalo (o migra con ALTER TABLE en `inicializar`) conforme se definan
los campos definitivos con el área. El patrón de migración incremental (agregar
columnas sin romper bases existentes) ya está montado.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass

from . import rutas

RUTA_DB = os.path.join(rutas.DATOS, "activos_fijos.db")


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
