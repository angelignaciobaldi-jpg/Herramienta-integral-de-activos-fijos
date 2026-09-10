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
    motivo: str = ""      # por qué se emparejó (etiqueta / serie / insumo / carpeta)
    candidato_de: "int | None" = None   # a qué activo apunta, si hay que preguntar
    carpeta: str = ""     # subcarpeta de la que salió (suele ser el responsable)

    @property
    def emparejado(self) -> bool:
        return self.id_registro is not None


def _clave_id(texto) -> str:
    """Identificador comparable: sin acentos, signos ni espacios."""
    return re.sub(r"[^A-Z0-9]+", "", str(texto or "").upper())


def _tokens_nombre(texto) -> frozenset:
    """Palabras comparables de un nombre de persona, sin acentos ni signos.

    Se descartan los fragmentos de una sola letra (iniciales, 'DE', 'Y'): no
    distinguen a nadie y solo generan coincidencias falsas."""
    import unicodedata

    t = unicodedata.normalize("NFD", str(texto or "").upper())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return frozenset(x for x in re.split(r"[^A-Z0-9]+", t) if len(x) > 1)


def _mismo_empleado(a: frozenset, b: frozenset) -> bool:
    """¿Dos nombres se refieren a la misma persona?

    Uno tiene que estar contenido en el otro y compartir AL MENOS DOS palabras:
    las carpetas suelen traer el nombre corto ('JUAN PEREZ') y el registro el
    completo ('JUAN PEREZ LOPEZ'). Con una sola palabra en común no basta —medio
    padrón se llama JUAN— y emparejar por ahí asignaría la foto de un empleado al
    activo de otro.
    """
    if not a or not b:
        return False
    return (a <= b or b <= a) and len(a & b) >= 2


def _carpetas_de(ruta: str, raiz: str) -> list[str]:
    """Nombres de las subcarpetas entre `raiz` y el archivo, de la más profunda a
    la más externa.

    Se devuelven TODAS porque el responsable puede estar a cualquier nivel: unos
    levantamientos vienen como 'JUAN PEREZ/foto.jpg' y otros como
    'SISTEMAS/JUAN PEREZ/foto.jpg'. Adivinar el nivel sería frágil; probarlos
    todos y quedarse con el que nombre a un empleado real, no.
    """
    if not raiz:
        return []
    try:
        relativa = os.path.relpath(os.path.dirname(ruta), raiz)
    except ValueError:      # unidades distintas en Windows
        return []
    if relativa.startswith(".."):
        return []
    partes = [x for x in relativa.replace("\\", "/").split("/")
              if x not in ("", ".")]
    return list(reversed(partes))


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


def emparejar_imagenes(entradas: list, registros: list, raiz: str = "",
                       carpetas: dict | None = None) -> list:
    """Relaciona cada imagen con un registro por su NOMBRE y por su CARPETA.

    `entradas`: [(nombre_archivo, ruta)] · `registros`: objetos con id, etiqueta,
    no_serie, nombre_insumo y responsable · `raiz`: carpeta que se subió, para
    deducir en qué subcarpeta cayó cada imagen · `carpetas`: {ruta: subcarpeta}
    para cuando esa estructura no está en disco, que es el caso del ZIP (se
    extrae aplanado por el límite de ruta de Windows). Devuelve un
    `Emparejamiento` por imagen.

    Los levantamientos vienen organizados en una carpeta por responsable, y ese
    dato desambigua lo que el nombre del archivo no puede: entre los tres
    'MONITOR.jpg' de un levantamiento, el que está en la carpeta de Juan es el
    monitor de Juan. Pasos, de más a menos seguro:

      1. ETIQUETA o SERIE dentro del nombre: identificadores únicos, relación
         inequívoca.
      2. INSUMO acotado por el RESPONSABLE de la carpeta: entre los registros de
         esa persona, uno solo se llama así.
      3. INSUMO a secas: solo si UN registro lo lleva, la imagen NO nombra otro
         identificador (ver `_trae_identificador_ajeno`) y ninguna otra imagen
         compite por ese activo. Si compiten, todas quedan con `candidato_de`
         para que la interfaz pregunte: entre varias fotos de 'SWITCH' la
         herramienta no puede saberlo, y equivocarse asigna la foto de un equipo
         a otro sin dejar rastro.
      4. RESPONSABLE a secas: la carpeta nombra a alguien que tiene UN solo
         registro suelto y contiene UNA sola foto suelta. Con más de uno de
         cualquiera de los dos lados no se adivina: se deja para el usuario.

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

    # Empleados del levantamiento, para reconocerlos en el nombre de la carpeta.
    empleados: dict = {}       # tokens del nombre -> [ids de sus registros]
    for r in registros:
        toks = _tokens_nombre(getattr(r, "responsable", "") or "")
        if toks:
            empleados.setdefault(toks, []).append(r.id)

    def _ids_del_responsable(carpetas: list) -> tuple:
        """(nombre_carpeta, ids) del empleado que nombra alguna de las carpetas.

        Si una carpeta cuadra con DOS empleados distintos no se usa: un nombre
        ambiguo apuntando a dos personas es peor que no tener nombre."""
        for nombre in carpetas:
            toks = _tokens_nombre(nombre)
            if not toks:
                continue
            coincidencias = [ids for emp, ids in empleados.items()
                             if _mismo_empleado(toks, emp)]
            if len(coincidencias) == 1:
                return nombre, coincidencias[0]
        return "", []

    def _origen(ru: str) -> list:
        """Subcarpetas de las que pudo salir la imagen, de la más profunda a la
        más externa. Del mapa si lo hay (ZIP) y, si no, del disco (carpeta)."""
        if carpetas and ru in carpetas:
            return [carpetas[ru]]
        return _carpetas_de(ru, raiz)

    pares = []
    for n, ru in entradas:
        subs = _origen(ru)
        pares.append(Emparejamiento(archivo=os.path.basename(n), ruta=ru,
                                    carpeta=subs[0] if subs else ""))
    # La carpeta reconocida se calcula una vez por imagen: se usa en dos pasos.
    responsables = {id(par): _ids_del_responsable(_origen(par.ruta))
                    for par in pares}
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

    # --- Paso 2: insumo DENTRO de los registros del responsable ------------
    for par in pares:
        if par.emparejado:
            continue
        nombre_carpeta, ids_emp = responsables[id(par)]
        if not ids_emp:
            continue
        base = _clave_id(os.path.splitext(par.archivo)[0])
        coinciden = [rid for rid in ids_emp
                     if rid not in usados
                     and _clave_id(por_id[rid].nombre_insumo)
                     and _clave_id(por_id[rid].nombre_insumo) in base]
        if len(coinciden) != 1:
            continue
        registro = por_id[coinciden[0]]
        if _trae_identificador_ajeno(par.archivo, registro):
            continue    # la foto nombra otro activo: no es de este
        par.id_registro = coinciden[0]
        par.motivo = f"insumo · carpeta de {nombre_carpeta}"
        usados.add(coinciden[0])

    # --- Paso 3: nombre del insumo, con desempate por el usuario -----------
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

    # --- Paso 4: la carpeta del responsable, cuando no queda duda ----------
    sueltas: dict = {}          # nombre de carpeta -> [fotos sin emparejar]
    for par in pares:
        if par.emparejado or par.candidato_de is not None:
            continue
        nombre_carpeta, ids_emp = responsables[id(par)]
        if ids_emp:
            sueltas.setdefault(nombre_carpeta, []).append(par)
    for nombre_carpeta, fotos in sueltas.items():
        _, ids_emp = responsables[id(fotos[0])]
        libres = [rid for rid in ids_emp if rid not in usados]
        if len(fotos) == 1 and len(libres) == 1:
            fotos[0].id_registro = libres[0]
            fotos[0].motivo = f"carpeta de {nombre_carpeta}"
            usados.add(libres[0])
    return pares


def es_imagen(nombre: str) -> bool:
    return os.path.splitext(nombre)[1].lower() in EXTENSIONES_IMAGEN


def extraer_zip(ruta_zip: str, subcarpeta: str | None = None) -> tuple[str, int]:
    """Extrae las IMÁGENES de un .zip a una carpeta persistente.

    Solo se extraen archivos de imagen (se ignoran otros contenidos y la basura
    que agregan algunos compresores, como '__MACOSX').

    La estructura de subcarpetas SE APLANA en disco, pero NO se pierde: el nombre
    de la carpeta que contenía cada imagen se devuelve aparte, porque en los
    levantamientos es el del responsable y con él se asigna la foto a su activo
    (ver `emparejar_imagenes`).

    Se aplana por el límite de 260 caracteres de Windows: la carpeta de datos ya
    es larga y, al recrear el árbol del ZIP —'Piso 1/IVAN ALCANTARA AYONDO/
    WhatsApp Image ....jpeg'— la ruta se pasaba y la extracción fallaba con un
    «No such file or directory» que no dice nada de la causa real.

    Devuelve (carpeta_destino, cantidad_extraida, carpetas), donde `carpetas` es
    {ruta_extraida: nombre de la subcarpeta de la que salió}.

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
    carpetas: dict = {}
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
                # (zip slip) ni de que un separador se cuele en el nombre. La
                # última subcarpeta se guarda aparte, en memoria.
                partes = [x for x in interno.replace("\\", "/").split("/")
                          if x not in ("", ".", "..")]
                final = _ruta_libre(destino, _sanear(base))
                with z.open(info) as origen, open(final, "wb") as salida:
                    shutil.copyfileobj(origen, salida)
                if len(partes) > 1:
                    carpetas[final] = partes[-2]
                extraidas += 1
    except zipfile.BadZipFile as exc:
        raise ErrorArchivo(
            "El archivo no es un ZIP válido o está dañado.") from exc
    except OSError as exc:
        raise ErrorArchivo(f"No se pudo extraer el ZIP: {exc}") from exc
    return destino, extraidas, carpetas


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
