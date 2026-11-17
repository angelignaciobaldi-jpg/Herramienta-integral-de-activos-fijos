"""Alta en el SIPP de las ubicaciones y sub-ubicaciones que trae la plantilla.

El catálogo de ubicaciones del portal está recién estrenado y HOY ESTÁ VACÍO, así
que la carga masiva no puede limitarse a elegir de una lista: la plantilla acepta
nombres nuevos y este módulo los registra en el portal ANTES de dar de alta los
activos, que es cuando ya tienen que existir para poder seleccionarlos.

Dos pasos, y en este orden por una razón: el portal no devuelve el id de lo que
acaba de crear —su propia pantalla recarga el catálogo completo tras cada
guardado—, y una sub-ubicación se crea colgada del `id_UbicacionActivoFijo` de su
ubicación. Así que primero se crean las ubicaciones, se vuelve a descargar el
catálogo para conocer sus ids reales, y con esos ids se crean las
sub-ubicaciones. Nada se adivina a partir de la respuesta del guardado.

Lo que este módulo NO hace, a propósito:

  - No empareja nombres parecidos con los del catálogo. Solo se crea lo que no
    existe; la ubicación heredada del levantamiento («de_Ubicacion», texto libre)
    se queda donde está. Las ubicaciones viejas van a cambiar de nombre al
    estandarizarse, así que un emparejado aproximado hoy ataría activos a una
    ubicación que mañana se llama de otra forma.
  - No asigna responsable. El portal lo deja opcional (`id_ResponsableUbicacion`
    vacío) y nadie puede decidir por el área quién resguarda un lugar.
  - No reactiva ubicaciones apagadas. Se avisa y se para: reactivar es una
    decisión del área, y crear otra con el mismo nombre lo rechaza el SIPP.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from . import db
from .catalogos_sipp import _RUTA, _listar_sububicaciones, _listar_ubicaciones

# Límites del formulario del portal (`validarFormUbicacion` /
# `validarFormSububicacion`). Se validan AQUÍ para no gastar un viaje al SIPP en
# algo que ya se sabe que va a rechazar, y para poder decir qué fila lo provoca.
LARGO_MIN_UBICACION = 3
LARGO_MIN_SUBUBICACION = 2
LARGO_MAX_NOMBRE = 80


class ErrorUbicaciones(Exception):
    """Falla al registrar una ubicación en el SIPP."""


@dataclass
class UbicacionPendiente:
    """Una ubicación (y sus sub-ubicaciones) que hay que crear en el SIPP."""

    sucursal: str
    id_sucursal: int
    nombre: str
    # Solo las sub-ubicaciones que FALTAN. Una ubicación que ya existe puede
    # quedar aquí con `crear_ubicacion=False` solo para colgarle subs nuevas.
    sububicaciones: list[str] = field(default_factory=list)
    crear_ubicacion: bool = True
    id_ubicacion: "int | None" = None   # si ya existe, su id

    @property
    def total(self) -> int:
        return (1 if self.crear_ubicacion else 0) + len(self.sububicaciones)


@dataclass
class Pendientes:
    """Lo que se va a crear y lo que no se puede, con su motivo."""

    ubicaciones: list[UbicacionPendiente] = field(default_factory=list)
    problemas: list[str] = field(default_factory=list)

    @property
    def total_altas(self) -> int:
        return sum(u.total for u in self.ubicaciones)

    def __bool__(self) -> bool:
        return bool(self.ubicaciones)


def _limpio(valor) -> str:
    return " ".join(str(valor or "").split())


def _motivo_invalido(nombre: str, minimo: int, que: str) -> str:
    if len(nombre) < minimo:
        return f"«{nombre}»: {que} necesita al menos {minimo} caracteres."
    if len(nombre) > LARGO_MAX_NOMBRE:
        return f"«{nombre[:40]}…»: {que} no puede pasar de {LARGO_MAX_NOMBRE}."
    return ""


def faltantes(id_empresa: int, filas) -> Pendientes:
    """Qué hay que crear en el SIPP para las `filas` de la plantilla.

    `filas`: iterable de `(sucursal, ubicacion, sububicacion)` con los NOMBRES
    tal como se capturaron (los dos últimos pueden venir vacíos). Se reciben
    triples y no registros para que esto sea probable sin levantamiento: la
    pantalla los arma de `datos_json` (`id_Ubicacion` / `id_SubUbicacion`).

    Se compara con la caché normalizando como el portal (`nombre_norm`), así que
    «ALMACEN TECNICO» y «Almacén  Técnico» cuentan como la misma: crear la segunda
    la rechazaría el SIPP por duplicada.
    """
    ids_suc = db.ids_sucursales_sipp(id_empresa)
    res = Pendientes()
    # Clave normalizada -> pendiente, para agrupar las mil filas de la plantilla
    # en una sola alta por ubicación.
    indice: dict = {}
    vistos_problema: set = set()
    # Las sub ya pedidas, para no contarlas dos veces desde filas distintas.
    subs_vistas: set = set()

    def problema(texto: str) -> None:
        if texto not in vistos_problema:
            vistos_problema.add(texto)
            res.problemas.append(texto)

    for sucursal, ubicacion, sububicacion in filas:
        sucursal = _limpio(sucursal)
        ubicacion = _limpio(ubicacion)
        sububicacion = _limpio(sububicacion)
        if not ubicacion:
            # Una sub sin su ubicación no se puede colgar de ningún sitio.
            if sububicacion:
                problema(f"«{sububicacion}»: se capturó la sub-ubicación sin su "
                         f"ubicación principal.")
            continue
        id_suc = ids_suc.get(db._norm_nombre(sucursal))
        if id_suc is None:
            problema(f"«{ubicacion}»: la sucursal «{sucursal or '(vacía)'}» no "
                     f"está en el catálogo del SIPP de esta empresa.")
            continue

        malo = _motivo_invalido(ubicacion, LARGO_MIN_UBICACION, "la ubicación")
        if malo:
            problema(malo)
            continue

        clave = (id_suc, db._norm_nombre(ubicacion))
        pendiente = indice.get(clave)
        if pendiente is None:
            existente = db.ubicacion_por_nombre(id_empresa, id_suc, ubicacion)
            if existente and not existente["activo"]:
                problema(f"«{ubicacion}» ya existe en «{sucursal}» pero está "
                         f"INACTIVA en el SIPP: hay que reactivarla ahí.")
                continue
            pendiente = UbicacionPendiente(
                sucursal=sucursal, id_sucursal=id_suc, nombre=ubicacion,
                crear_ubicacion=existente is None,
                id_ubicacion=existente["id_ubicacion"] if existente else None)
            indice[clave] = pendiente
            res.ubicaciones.append(pendiente)

        if not sububicacion:
            continue
        malo = _motivo_invalido(sububicacion, LARGO_MIN_SUBUBICACION,
                               "la sub-ubicación")
        if malo:
            problema(malo)
            continue
        if pendiente.id_ubicacion is not None:
            sub = db.sububicacion_por_nombre(
                id_empresa, pendiente.id_ubicacion, sububicacion)
            if sub is not None:
                if not sub["activo"]:
                    problema(f"«{sububicacion}» (en «{ubicacion}») ya existe pero "
                             f"está INACTIVA en el SIPP.")
                continue
        clave_sub = clave + (db._norm_nombre(sububicacion),)
        if clave_sub not in subs_vistas:
            subs_vistas.add(clave_sub)
            pendiente.sububicaciones.append(sububicacion)

    # Una ubicación que ya existe y no aportó subs nuevas no es un pendiente.
    res.ubicaciones = [u for u in res.ubicaciones if u.total]
    return res


async def _guardar(sesion, metodo: str, payload: dict) -> dict:
    """POST al cfproxy de un método que ESCRIBE. Devuelve la respuesta cruda.

    A diferencia de las consultas, el guardado no trae `QUERY`: contesta HTTP 200
    con `ISOK` y, si falla, el motivo en `MSG` (p. ej. el nombre duplicado). Hay
    que mirar el cuerpo para saber si se registró algo.
    """
    cuerpo = json.dumps({"component": "ActivosFijosNuevo", "execMethod": metodo,
                         "argumentcollection": payload})
    try:
        resp = await sesion.context.request.post(
            sesion.BASE_URL + _RUTA, data=cuerpo,
            headers={"Content-Type": "application/json"})
        datos = await resp.json()
    except Exception as exc:  # noqa: BLE001 — se reporta como ErrorUbicaciones
        raise ErrorUbicaciones(f"No se pudo invocar {metodo}: {exc}") from exc
    if not isinstance(datos, dict):
        raise ErrorUbicaciones(f"{metodo}: el SIPP no contestó un resultado.")
    if not datos.get("ISOK"):
        motivo = str(datos.get("MSG") or "").strip() or "el SIPP rechazó el alta"
        raise ErrorUbicaciones(motivo)
    return datos


async def crear(sesion, id_empresa: int, pendientes: list[UbicacionPendiente],
                progreso=None, mensaje=None, punto_control=None) -> dict:
    """Crea en el SIPP las ubicaciones y sub-ubicaciones de `pendientes`.

    Devuelve {ubicaciones, sububicaciones, errores}. Un fallo en una NO aborta el
    resto: se recoge su motivo y se sigue, porque a mitad de una carga de cientos
    de activos dejar lo demás sin registrar es peor que reportar lo que no entró.
    Al terminar se refresca la caché local, que es de donde el alta de activos
    saca los nombres para los desplegables del portal.
    """
    hechos, errores = 0, []
    total = sum(u.total for u in pendientes)

    async def paso() -> None:
        nonlocal hechos
        hechos += 1
        if progreso:
            progreso(hechos, total)
        if punto_control is not None:
            await punto_control()

    # --- 1) Ubicaciones ------------------------------------------------------
    if mensaje:
        mensaje("Registrando ubicaciones…")
    for u in pendientes:
        if not u.crear_ubicacion:
            continue
        try:
            await _guardar(sesion, "guardarUbicacionAF", {
                "id_Empresa": id_empresa,
                "id_Sucursal": u.id_sucursal,
                "id_UbicacionActivoFijo": "",     # vacío = nueva
                "nb_Ubicacion": u.nombre,
                "de_Ubicacion": "",
                "id_ResponsableUbicacion": "",    # opcional en el portal
                "sn_Activo": "1",
            })
        except ErrorUbicaciones as exc:
            errores.append(f"Ubicación «{u.nombre}» ({u.sucursal}): {exc}")
        await paso()

    # --- 2) Refrescar para conocer los ids reales ---------------------------
    # El guardado no devuelve el id, así que se vuelve a leer el catálogo (igual
    # que hace la pantalla del portal). Sin esto no hay de dónde colgar las subs.
    ubicaciones = await _listar_ubicaciones(sesion, id_empresa)
    db.reemplazar_ubicaciones(id_empresa, ubicaciones)
    creadas = sum(1 for u in pendientes if u.crear_ubicacion
                  and db.ubicacion_por_nombre(id_empresa, u.id_sucursal, u.nombre))

    # --- 3) Sub-ubicaciones --------------------------------------------------
    if any(u.sububicaciones for u in pendientes):
        if mensaje:
            mensaje("Registrando sub-ubicaciones…")
    creadas_sub = 0
    for u in pendientes:
        if not u.sububicaciones:
            continue
        actual = db.ubicacion_por_nombre(id_empresa, u.id_sucursal, u.nombre)
        if actual is None:
            errores.append(
                f"«{u.nombre}» ({u.sucursal}): no quedó registrada, así que sus "
                f"{len(u.sububicaciones)} sub-ubicación(es) no se crearon.")
            hechos += len(u.sububicaciones)
            continue
        for nombre_sub in u.sububicaciones:
            try:
                await _guardar(sesion, "guardarSububicacionAF", {
                    "id_Empresa": id_empresa,
                    "id_Sucursal": u.id_sucursal,
                    "id_UbicacionActivoFijo": actual["id_ubicacion"],
                    "id_SububicacionActivoFijo": "",
                    "nb_Sububicacion": nombre_sub,
                    "de_Sububicacion": "",
                    "id_ResponsableSububicacion": "",
                    "sn_Activo": "1",
                })
                creadas_sub += 1
            except ErrorUbicaciones as exc:
                errores.append(
                    f"Sub-ubicación «{nombre_sub}» (en «{u.nombre}»): {exc}")
            await paso()

    # --- 4) Caché al día -----------------------------------------------------
    ids = {u["id_ubicacion"] for u in ubicaciones}
    db.reemplazar_sububicaciones(
        id_empresa, await _listar_sububicaciones(sesion, id_empresa, ids))
    return {"ubicaciones": creadas, "sububicaciones": creadas_sub,
            "errores": errores}
