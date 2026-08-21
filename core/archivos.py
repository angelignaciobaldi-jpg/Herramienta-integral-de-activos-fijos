"""Utilidades de archivos para la carga del levantamiento (ZIP e imágenes).

Los levantamientos suelen llegar como una CARPETA COMPRIMIDA (.zip) con las
fotos, a veces organizadas en subcarpetas por área. Este módulo se encarga de
descomprimirlas y de localizar las imágenes, para que la pantalla de Registro
siga su proceso normal (el nombre de cada archivo trae el insumo y la serie).

Sin dependencias de la interfaz: es backend puro.
"""

from __future__ import annotations

import os
import re
import shutil
import zipfile
from dataclasses import dataclass

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


# --- Nombre de archivo -> datos del activo --------------------------------
# La foto del levantamiento se nombra  NOMBREINSUMO<sep>ETIQUETA, con «_», «-» o
# un espacio como separador (en campo se usan los tres).
_SEPARADORES = ("_", "-", " ")
# Largos VÁLIDOS de una etiqueta del SIPP. Un número al final con otro largo no es
# etiqueta: casi siempre es el número de serie, y como no se puede distinguir solo
# por el nombre, se marca para que el usuario lo confirme.
LARGOS_ETIQUETA = (5, 7, 10)


@dataclass
class DatosImagen:
    """Lo que se puede deducir del NOMBRE de una foto del levantamiento."""

    insumo: str
    etiqueta: str = ""
    serie: str = ""            # sufijo alfanumérico: es serie, no etiqueta
    numero_ambiguo: str = ""   # número final con largo != etiqueta (¿serie?)
    base: str = ""             # nombre sin extensión, tal cual venía

    @property
    def necesita_confirmar(self) -> bool:
        return bool(self.numero_ambiguo)


def parsear_nombre(nombre_archivo: str) -> DatosImagen:
    """Deduce insumo y ETIQUETA del nombre de la imagen.

        'DETECTOR DE HUMO_0048040.jpg'  -> insumo='DETECTOR DE HUMO', etiqueta='0048040'
        'DETECTOR DE HUMO-0048040.jpg'  -> igual (guion)
        'DETECTOR DE HUMO 0048040.jpg'  -> igual (espacio)
        'DETECTOR DE HUMO_SN12345678.jpg' -> no hay número final: todo es insumo
        'DETECTOR DE HUMO_123.jpg'      -> número de largo raro: numero_ambiguo='123'
        '17 MESAS.png'                  -> el número va al INICIO: todo es insumo

    Solo se mira el último trozo, y se clasifica así:

      - Solo dígitos, con largo de etiqueta (5, 7 o 10)  -> ETIQUETA.
      - Solo dígitos, con otro largo                     -> `numero_ambiguo`
        (probablemente la serie, pero hay que confirmarlo).
      - Mezcla de dígitos con letras o signos            -> SERIE. Una etiqueta
        del SIPP es puramente numérica, así que un código como 'SN12345' o
        'JAA0GB-51' solo puede ser el número de serie.
      - Sin ningún dígito                                -> parte del NOMBRE. Es
        lo que salva a 'BOTE DE BASURA METALICO' o '2 AROMATIZANTES COMEDOR' de
        que su última palabra se registre como serie.
    """
    base = os.path.splitext(os.path.basename(str(nombre_archivo or "")))[0].strip()
    if not base:
        return DatosImagen(insumo="", base=base)
    # Se corta por el separador MÁS A LA DERECHA de los tres admitidos.
    corte = max(base.rfind(sep) for sep in _SEPARADORES)
    if corte <= 0:                      # sin separador (o al inicio): no hay sufijo
        return DatosImagen(insumo=base, base=base)
    izquierda, derecha = base[:corte].strip(), base[corte + 1:].strip()
    if not izquierda:
        return DatosImagen(insumo=base, base=base)
    if not derecha.isdigit():
        # Alfanumérico: es serie SOLO si trae algún dígito. Sin dígitos es una
        # palabra más del nombre del insumo, no un código.
        if any(c.isdigit() for c in derecha):
            return DatosImagen(insumo=izquierda, serie=derecha, base=base)
        return DatosImagen(insumo=base, base=base)
    if len(derecha) in LARGOS_ETIQUETA:
        return DatosImagen(insumo=izquierda, etiqueta=derecha, base=base)
    return DatosImagen(insumo=izquierda, numero_ambiguo=derecha, base=base)


def nombre_sin_serie(nombre_insumo: str, serie: str, etiqueta: str = "") -> str:
    """Quita del nombre del insumo la SERIE que quedó registrada en su columna.

    El nombre de la foto trae el código pegado al insumo ('PANTALLA QS97G32...'),
    así que al extraerlo el dato acaba duplicado: en «Nombre insumo» y en «No. de
    serie». Se recorta solo si va AL FINAL, que es donde el nombre de archivo lo
    pone; en medio sería parte del nombre real.

    No se toca cuando la serie ES la etiqueta: ahí el número no sobra en el nombre,
    y además el SIPP registra la etiqueta como serie cuando el activo no tiene una
    (ver `core.rpa_sipp.serie_para_alta`).
    """
    nombre = str(nombre_insumo or "").strip()
    serie = str(serie or "").strip()
    if not nombre or not serie:
        return nombre
    if serie.casefold() == str(etiqueta or "").strip().casefold():
        return nombre
    if not nombre.casefold().endswith(serie.casefold()):
        return nombre
    recortado = nombre[:len(nombre) - len(serie)].rstrip()
    # Se limpia el separador que unía ambos ('_', '-' o el espacio ya quitado).
    recortado = recortado.rstrip("_-").rstrip()
    # Si el nombre era SOLO la serie, se conserva tal cual: quedarse sin insumo
    # sería peor que el dato repetido (el registro se omitiría al importar).
    return recortado or nombre


@dataclass
class Emparejamiento:
    """Una imagen y el registro del levantamiento al que corresponde."""

    archivo: str          # nombre del archivo
    ruta: str
    id_registro: "int | None" = None
    motivo: str = ""      # por qué se emparejó (etiqueta / serie / insumo)
    candidato_de: "int | None" = None   # a qué activo apunta, si hay que preguntar

    @property
    def emparejado(self) -> bool:
        return self.id_registro is not None


def _clave_id(texto) -> str:
    """Identificador comparable: sin acentos, signos ni espacios."""
    return re.sub(r"[^A-Z0-9]+", "", str(texto or "").upper())


def _trae_identificador_ajeno(archivo: str, registro) -> bool:
    """¿El nombre del archivo trae una serie/etiqueta que NO es la del registro?

    Es la señal más fuerte de que la foto es de OTRO activo: 'SWITCH
    101625...0042G.jpg' nombra un switch distinto al de la serie 225302B000052,
    aunque ambos se llamen 'SWITCH'. Sin esta comprobación, el único registro con
    ese nombre se quedaba con la primera foto que llegara.
    """
    datos = parsear_nombre(archivo)
    sufijo = _clave_id(datos.etiqueta or datos.serie or datos.numero_ambiguo)
    if not sufijo:
        return False   # el nombre no dice más: no hay motivo para descartarla
    propios = {p for p in (_clave_id(registro.etiqueta),
                           _clave_id(registro.no_serie)) if p}
    if not propios:
        # El registro no tiene con qué contrastar (ni etiqueta ni serie): el
        # sufijo no prueba nada. Se deja pasar como candidata y, si hay varias,
        # decide el usuario en vez de descartarlas por una corazonada.
        return False
    return sufijo not in propios


def emparejar_imagenes(entradas: list, registros: list) -> list:
    """Relaciona cada imagen con un registro por lo que dice su NOMBRE.

    `entradas`: [(nombre_archivo, ruta)] · `registros`: objetos con id, etiqueta,
    no_serie y nombre_insumo. Devuelve un `Emparejamiento` por imagen.

    Dos pasos, de más a menos seguro:

      1. ETIQUETA o SERIE dentro del nombre: son identificadores únicos, así que
         la relación es inequívoca.
      2. NOMBRE DEL INSUMO: solo si UN registro lo lleva, la imagen NO nombra otro
         identificador (ver `_trae_identificador_ajeno`) y ninguna otra imagen
         compite por ese mismo activo. Si compiten, todas quedan marcadas con
         `candidato_de` para que la interfaz pregunte cuál es la correcta: entre
         varias fotos de 'SWITCH' la herramienta no puede saberlo, y equivocarse
         aquí asigna la foto de un equipo a otro sin dejar rastro.

    Un registro no se empareja dos veces: el primero que lo reclama se lo queda.
    """
    por_etiqueta, por_serie, por_insumo = {}, {}, {}
    for r in registros:
        etq, serie = _clave_id(r.etiqueta), _clave_id(r.no_serie)
        if etq:
            por_etiqueta.setdefault(etq, r.id)
        if serie:
            por_serie.setdefault(serie, r.id)
        por_insumo.setdefault(_clave_id(r.nombre_insumo), []).append(r.id)
    por_id = {r.id: r for r in registros}

    pares = [Emparejamiento(archivo=os.path.basename(n), ruta=ru) for n, ru in entradas]
    usados: set = set()

    # --- Paso 1: identificadores (etiqueta y serie) ------------------------
    for par in pares:
        base = _clave_id(os.path.splitext(par.archivo)[0])
        for mapa, nombre_motivo in ((por_etiqueta, "etiqueta"), (por_serie, "serie")):
            for valor, rid in mapa.items():
                if rid in usados or valor not in base:
                    continue
                par.id_registro, par.motivo = rid, f"{nombre_motivo} {valor}"
                usados.add(rid)
                break
            if par.emparejado:
                break

    # --- Paso 2: nombre del insumo, con desempate por el usuario -----------
    candidatas: dict = {}
    for par in pares:
        if par.emparejado:
            continue
        base = _clave_id(os.path.splitext(par.archivo)[0])
        for valor, ids in por_insumo.items():
            if not valor or valor not in base or len(ids) != 1 or ids[0] in usados:
                continue
            registro = por_id[ids[0]]
            if _trae_identificador_ajeno(par.archivo, registro):
                break   # la foto nombra otro activo: no es de este
            candidatas.setdefault(ids[0], []).append(par)
            break

    for rid, fotos in candidatas.items():
        if len(fotos) == 1:
            fotos[0].id_registro, fotos[0].motivo = rid, "nombre del insumo"
            usados.add(rid)
        else:
            # Varias fotos para el mismo activo: que elija el usuario.
            for foto in fotos:
                foto.candidato_de = rid
    return pares


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
    # Extracción LIMPIA: si ya se extrajo antes este mismo ZIP, se vacía primero.
    # Si no, los archivos existentes forzarían nombres " (2)", " (3)"… y como el
    # nombre del archivo lleva la serie, cada re-subida generaría registros
    # NUEVOS (con serie distinta) en vez de detectarse como duplicados. Al extraer
    # en limpio, el mismo ZIP produce siempre los mismos nombres y el de-duplicado
    # por clave (insumo+serie) funciona. Las rutas ya registradas siguen válidas
    # (se recrean con el mismo nombre).
    if os.path.isdir(destino):
        shutil.rmtree(destino, ignore_errors=True)
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
