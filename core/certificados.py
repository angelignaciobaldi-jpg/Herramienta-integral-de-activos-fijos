"""Verificación TLS que respeta lo que Windows ya considera confiable.

Un antivirus o un proxy corporativo que inspecciona HTTPS no deja pasar el
certificado real del sitio: presenta uno firmado por una autoridad propia. El
producto instala esa autoridad en el almacén de Windows al entrar, así que Edge
y Chrome navegan sin quejarse, pero Python NO usa ese almacén como lo usa
Windows: OpenSSL arma su propia lista al arrancar y, si el emisor no está ahí,
falla con

    [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get
    local issuer certificate

Eso dejaba sin actualizaciones —y sin API del SIPP— a un equipo donde todo lo
demás funciona, con un mensaje que no apunta a nada accionable.

La verificación se delega entonces a **Schannel**, el mismo motor que usa
Windows (vía `truststore`): si el navegador de ese equipo abre la URL, la app
también. Si `truststore` no está disponible se arma un contexto con los
certificados de los almacenes de Windows, y como última salida se admite un
`.pem` propio en la variable de entorno QUETZALTIC_CA_BUNDLE.

Nada de esto DESACTIVA la verificación: un certificado en el que Windows no
confía sigue siendo un error. La alternativa —conectarse sin verificar— dejaría
la descarga del instalador expuesta a que cualquiera la sustituya, que es
justamente lo que no puede pasar en un canal de actualización.
"""

from __future__ import annotations

import os
import ssl

from core import entorno

# Ruta a un .pem con la autoridad del proxy, para el caso en que Windows tampoco
# confíe en ella (equipos donde el certificado se reparte a mano).
VAR_BUNDLE = "QUETZALTIC_CA_BUNDLE"

_contexto: ssl.SSLContext | None = None
_origen = ""


def contexto() -> ssl.SSLContext:
    """Contexto TLS para las conexiones de la app. Se arma una sola vez."""
    global _contexto, _origen
    if _contexto is None:
        _contexto, _origen = _construir()
    return _contexto


def origen() -> str:
    """Qué verificación quedó activa (para el diagnóstico)."""
    if _contexto is None:
        contexto()
    return _origen


def _construir() -> tuple[ssl.SSLContext, str]:
    # Por `entorno` y no por os.environ: así vale igual en el .env junto al .exe.
    bundle = (entorno.obtener(VAR_BUNDLE) or "").strip('"').strip()
    try:
        import truststore

        ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        detalle = "almacén de certificados de Windows (Schannel)"
    except Exception:  # noqa: BLE001 — sin truststore se sigue con lo de Python
        ctx = ssl.create_default_context()
        detalle = "lista de certificados de Python"
        detalle += _sumar_almacenes_windows(ctx)
    if bundle and os.path.isfile(bundle):
        try:
            ctx.load_verify_locations(cafile=bundle)
            detalle += f" + {VAR_BUNDLE}"
        except (OSError, ssl.SSLError):
            # Un .pem ilegible no debe impedir conectarse con lo demás.
            detalle += f" ({VAR_BUNDLE} no se pudo leer)"
    return ctx, detalle


def _sumar_almacenes_windows(ctx: ssl.SSLContext) -> str:
    """Agrega los certificados de los almacenes de Windows, sin filtrar por uso.

    `create_default_context()` ya lee ROOT y CA en Windows, pero descarta los que
    no declaran explícitamente «autenticación de servidor», y varias autoridades
    de antivirus se instalan sin esa marca. Aquí se admiten todas: son las mismas
    en las que el equipo ya confía para navegar."""
    if not hasattr(ssl, "enum_certificates"):
        return ""
    sumados = 0
    for almacen in ("ROOT", "CA", "Trust"):
        try:
            certificados = ssl.enum_certificates(almacen)
        except (OSError, ValueError):
            continue
        for cert, tipo, _uso in certificados:
            if tipo != "x509_asn":
                continue
            try:
                ctx.load_verify_locations(cadata=ssl.DER_cert_to_PEM_cert(cert))
                sumados += 1
            except (ssl.SSLError, ValueError):
                continue    # certificado ilegible o repetido: se ignora
    return f" + {sumados} del almacén de Windows" if sumados else ""


# Archivo (en DATOS) con los certificados de Windows en formato PEM, para los
# programas que NO usan el `ssl` de Python. Hoy: el descargador de navegadores de
# Playwright, que es Node.
NOMBRE_PEM = "certificados_windows.pem"


def ruta_bundle_pem() -> str:
    """Escribe los certificados de confianza de Windows como .pem y devuelve su ruta.

    Existe para **Node**: el navegador del RPA (Chromium) no viaja empaquetado, lo
    descarga el driver de Playwright, que es Node y trae su propia lista de
    autoridades. Ni el contexto TLS de Python ni el almacén de Windows le sirven,
    así que en un equipo donde un antivirus inspecciona HTTPS esa descarga falla
    igual que fallaba la actualización —y con un mensaje que habla de internet—.
    Node sí acepta un almacén extra por la variable NODE_EXTRA_CA_CERTS, que es
    lo que se le pasa apuntando aquí.

    Se reescribe en cada llamada (son milisegundos y ocurre solo cuando hay que
    descargar): un antivirus recién instalado cambia la lista, y un archivo viejo
    volvería a dejar el equipo sin navegador. Devuelve "" si no se pudo.
    """
    from core import rutas

    trozos = []
    if hasattr(ssl, "enum_certificates"):
        for almacen in ("ROOT", "CA", "Trust"):
            try:
                certificados = ssl.enum_certificates(almacen)
            except (OSError, ValueError):
                continue
            for cert, tipo, _uso in certificados:
                if tipo == "x509_asn":
                    try:
                        trozos.append(ssl.DER_cert_to_PEM_cert(cert))
                    except (ValueError, ssl.SSLError):
                        continue
    propio = (entorno.obtener(VAR_BUNDLE) or "").strip('"').strip()
    if propio and os.path.isfile(propio):
        try:
            with open(propio, encoding="utf-8", errors="ignore") as fh:
                trozos.append(fh.read())
        except OSError:
            pass
    if not trozos:
        return ""
    destino = os.path.join(rutas.DATOS, NOMBRE_PEM)
    try:
        os.makedirs(rutas.DATOS, exist_ok=True)
        with open(destino, "w", encoding="ascii") as fh:
            fh.write("\n".join(t.strip() for t in trozos) + "\n")
    except OSError:
        return ""      # sin PEM se intenta la descarga igual, como antes
    return destino
