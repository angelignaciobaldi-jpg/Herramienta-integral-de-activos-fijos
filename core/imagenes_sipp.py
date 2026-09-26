from __future__ import annotations

"""Preparación de fotografías antes de subirlas al SIPP.

El portal valida los archivos **en el navegador**, no en el servidor: más de
2 MB o una extensión fuera de JPG/JPEG/PNG/PDF y `subirFotografia*` muestra un
alert, limpia el input y no sube nada (ver `js/controllers/ActivosFijosNuevo.js`).
Para el RPA eso es lo peor de los dos mundos: la foto se pierde y encima queda un
modal encima del formulario que puede tragarse el clic de «Guardar».

Por eso el filtro vive aquí, antes de tocar el navegador: lo que no cumple se
recomprime a JPEG y, si aun así no pasa (un HEIC que Pillow no abre), se descarta
con un motivo legible para el reporte. Las fotos de campo vienen de teléfonos, así
que pasar de 2 MB es lo normal, no la excepción.
"""

import hashlib
import os
import tempfile

# Límites del portal (mismos en alta y edición).
LIMITE_BYTES = 2 * 1024 * 1024
MAXIMO = 3
EXTENSIONES = (".jpg", ".jpeg", ".png", ".pdf")

# Las copias recomprimidas no son datos del usuario: son un intermedio del envío.
# Viven en el temporal del sistema y se reutilizan entre activos de la misma
# corrida (la misma foto puede tocar a varios registros).
_CARPETA_TEMP = "activos_fijos_fotos_sipp"


def _carpeta() -> str:
    ruta = os.path.join(tempfile.gettempdir(), _CARPETA_TEMP)
    os.makedirs(ruta, exist_ok=True)
    return ruta


def _reducir(origen: str) -> str:
    """Devuelve una copia JPEG del archivo que quepa en el límite del portal.

    Baja primero la calidad y, si no alcanza, también la resolución: una foto de
    teléfono de 3 MB entra holgada con calidad 70 sin perder nada útil para
    identificar el activo. Lanza si Pillow no puede abrir el archivo (HEIC, por
    ejemplo, que necesita un complemento aparte)."""
    from PIL import Image  # import tardío: el RPA no lo necesita si no hay fotos

    # El nombre lleva un resumen de la RUTA completa: dos responsables pueden
    # tener un «ESCRITORIO.jpg» distinto y una copia pisaría a la otra.
    firma = hashlib.md5(os.path.abspath(origen).encode("utf-8")).hexdigest()[:8]
    destino = os.path.join(
        _carpeta(),
        f"{os.path.splitext(os.path.basename(origen))[0]}_{firma}.jpg")
    # Reutiliza la copia si ya se hizo en esta corrida y sigue siendo válida.
    if os.path.exists(destino) and os.path.getsize(destino) <= LIMITE_BYTES \
            and os.path.getmtime(destino) >= os.path.getmtime(origen):
        return destino

    with Image.open(origen) as img:
        img = img.convert("RGB")  # descarta alfa: JPEG no lo admite
        for escala in (1.0, 0.75, 0.5, 0.35):
            copia = img
            if escala < 1.0:
                copia = img.resize((max(1, int(img.width * escala)),
                                    max(1, int(img.height * escala))))
            for calidad in (85, 70, 55, 40):
                copia.save(destino, "JPEG", quality=calidad, optimize=True)
                if os.path.getsize(destino) <= LIMITE_BYTES:
                    return destino
    return destino  # último intento: se devuelve igual y el filtro lo descartará


def preparar(rutas, maximo: int = MAXIMO) -> "tuple[list[str], list[str]]":
    """Convierte las rutas locales en archivos que el SIPP acepte.

    Devuelve `(aptas, avisos)`: las rutas listas para el input de archivo (como
    mucho `maximo`) y los motivos, en texto, de lo que quedó fuera. Los avisos van
    al reporte: una foto que no se subió tiene que decirse, no desaparecer.
    """
    aptas: list[str] = []
    avisos: list[str] = []
    for ruta in (rutas or []):
        if len(aptas) >= maximo:
            avisos.append(f"El SIPP admite {maximo} fotografías: "
                          f"«{os.path.basename(ruta)}» no se subió.")
            continue
        if not ruta or not os.path.exists(ruta):
            continue
        nombre = os.path.basename(ruta)
        ext = os.path.splitext(ruta)[1].lower()
        try:
            if ext in EXTENSIONES and os.path.getsize(ruta) <= LIMITE_BYTES:
                aptas.append(ruta)
                continue
            if ext == ".pdf":  # un PDF pesado no se puede recomprimir aquí
                avisos.append(f"«{nombre}» pasa de 2 MB y el SIPP no lo admite.")
                continue
            copia = _reducir(ruta)
            if os.path.getsize(copia) > LIMITE_BYTES:
                avisos.append(f"«{nombre}» no bajó de 2 MB: no se subió.")
                continue
            aptas.append(copia)
        except Exception:  # noqa: BLE001 — formato que Pillow no abre (HEIC, etc.)
            avisos.append(f"«{nombre}» no se pudo convertir a un formato que el "
                          "SIPP acepte (JPG, PNG o PDF).")
    return aptas, avisos
