"""Utilidades de archivos para la carga del levantamiento (ZIP e imágenes).

Los levantamientos suelen llegar como una CARPETA COMPRIMIDA (.zip) con las
fotos, a veces organizadas en subcarpetas por área. Este módulo se encarga de
descomprimirlas y de localizar las imágenes, para que la pantalla de Registro
siga su proceso normal (el nombre de cada archivo trae el insumo y la serie).

Sin dependencias de la interfaz: es backend puro.
"""

from __future__ import annotations

import os
import shutil
import zipfile

from . import rutas

# Extensiones de imagen que se consideran parte de un levantamiento.
EXTENSIONES_IMAGEN = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}

# Carpeta (persistente) donde se extraen los ZIP. Debe sobrevivir a la sesión:
# cada registro guarda la RUTA de su imagen para poder abrirla después, así que
# no puede vivir en una carpeta temporal que el sistema borre.
_SUBCARPETA = "imagenes_levantamiento"


class ErrorArchivo(Exception):
    """Falla esperada al descomprimir o leer los archivos del levantamiento."""


def carpeta_extraccion() -> str:
    """Carpeta base donde se extraen los levantamientos comprimidos."""
    destino = os.path.join(rutas.DATOS, _SUBCARPETA)
    os.makedirs(destino, exist_ok=True)
    return destino


def _nombre_seguro(info: zipfile.ZipInfo) -> str:
    """Nombre del archivo dentro del ZIP, corrigiendo la codificación.

    Los ZIP creados en Windows suelen guardar los nombres en cp437 cuando no
    marcan UTF-8; sin esta corrección, un archivo como 'SEÑALAMIENTO_123.jpg'
    llegaría con caracteres rotos — y como el nombre ES el dato (insumo y serie),
    se registraría mal."""
    nombre = info.filename
    if not (info.flag_bits & 0x800):  # sin bandera UTF-8
        try:
            nombre = nombre.encode("cp437").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass  # se queda con el nombre original
    return nombre


def es_imagen(nombre: str) -> bool:
    return os.path.splitext(nombre)[1].lower() in EXTENSIONES_IMAGEN


def extraer_zip(ruta_zip: str, subcarpeta: str | None = None) -> tuple[str, int]:
    """Extrae las IMÁGENES de un .zip a una carpeta persistente.

    Solo se extraen archivos de imagen (se ignoran otros contenidos y la basura
    que agregan algunos compresores, como '__MACOSX'). La estructura de
    subcarpetas del ZIP se aplana: lo que importa es el nombre de cada archivo.

    Devuelve (carpeta_destino, cantidad_extraida).

    Raises:
        ErrorArchivo: si el archivo no es un ZIP válido o no se puede leer.
    """
    if not os.path.exists(ruta_zip):
        raise ErrorArchivo(f"No se encontró el archivo: {ruta_zip}")
    nombre = subcarpeta or os.path.splitext(os.path.basename(ruta_zip))[0]
    destino = os.path.join(carpeta_extraccion(), _sanear(nombre))
    os.makedirs(destino, exist_ok=True)

    extraidas = 0
    try:
        with zipfile.ZipFile(ruta_zip) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                interno = _nombre_seguro(info)
                base = os.path.basename(interno)
                if not base or base.startswith(".") or "__MACOSX" in interno:
                    continue
                if not es_imagen(base):
                    continue
                # Se aplana la estructura y se sanea el nombre: así no hay forma
                # de que una ruta del ZIP escriba fuera de la carpeta destino
                # (zip slip) ni de que un separador se cuele en el nombre.
                final = _ruta_libre(destino, _sanear(base))
                with z.open(info) as origen, open(final, "wb") as salida:
                    shutil.copyfileobj(origen, salida)
                extraidas += 1
    except zipfile.BadZipFile as exc:
        raise ErrorArchivo(
            "El archivo no es un ZIP válido o está dañado.") from exc
    except OSError as exc:
        raise ErrorArchivo(f"No se pudo extraer el ZIP: {exc}") from exc
    return destino, extraidas


def listar_imagenes(carpeta: str) -> list[tuple[str, str]]:
    """Devuelve [(nombre_archivo, ruta_completa)] de las imágenes de `carpeta`,
    recorriendo también sus SUBCARPETAS (los levantamientos suelen venir
    organizados por área). Ordenadas por nombre."""
    encontradas: list[tuple[str, str]] = []
    for raiz, _dirs, archivos in os.walk(carpeta):
        if "__MACOSX" in raiz:
            continue
        for nombre in archivos:
            if es_imagen(nombre) and not nombre.startswith("."):
                encontradas.append((nombre, os.path.join(raiz, nombre)))
    return sorted(encontradas, key=lambda p: p[0].lower())


def _sanear(nombre: str) -> str:
    """Quita de un nombre lo que Windows no admite (y cualquier separador)."""
    limpio = nombre.replace("\\", "/").split("/")[-1]
    for c in '<>:"|?*':
        limpio = limpio.replace(c, "")
    return limpio.strip() or "archivo"


def _ruta_libre(carpeta: str, nombre: str) -> str:
    """Ruta que no pise un archivo existente: agrega ' (n)' antes de la extensión.
    Evita que dos fotos con el mismo nombre en subcarpetas distintas se
    sobrescriban al aplanar la estructura."""
    destino = os.path.join(carpeta, nombre)
    if not os.path.exists(destino):
        return destino
    base, ext = os.path.splitext(nombre)
    n = 2
    while os.path.exists(os.path.join(carpeta, f"{base} ({n}){ext}")):
        n += 1
    return os.path.join(carpeta, f"{base} ({n}){ext}")
