"""Pantalla "Generador de códigos QR".

Genera códigos QR (etiquetas imprimibles en PDF) para los activos de una empresa.
Cada QR codifica un enlace `URL_base/etiqueta`; al escanearlo, el PWA/API móvil
resuelve la etiqueta y muestra la información del activo. La URL base es un AJUSTE
GLOBAL: se captura en Configuración (ver core/qr.base_url), no aquí, porque la usan
por igual la etiqueta suelta, la carpeta y el PDF.

La pantalla está partida en tres bloques, que son tres decisiones distintas:
  1. Activos: empresa, sucursal y traer/actualizar su caché desde el SIPP.
  2. Etiqueta individual: buscar UN activo y guardar su PNG.
  3. Etiquetas por lote: carpeta por departamento o la hoja en PDF.
"""

from __future__ import annotations

import asyncio

import flet as ft

from core import db
from core.empresas import ID_POR_EMPRESA, NOMBRES_EMPRESAS
from ui.comun import GRIS, NARANJA, ROJO, VERDE, error_al_guardar
from ui.componentes import (Modal, boton_herramienta, boton_primario,
                            boton_secundario, buscador, campo_opciones,
                            fila_resultado, lista_resultados, tarjeta_seccion)

# Opción "todas las sucursales": se trata como "sin filtro".
_TODAS = "Todas las sucursales"


class SeccionGeneradorQR:
    """Descarga activos por empresa y genera etiquetas QR imprimibles."""

    def __init__(self, app):
        self.app = app
        self.page = app.page
        self._construir()

    @staticmethod
    def _encabezado(icono, titulo: str, ayuda: str) -> ft.Control:
        """Título + explicación de un bloque de la pantalla."""
        return ft.Column(
            [ft.Row([ft.Icon(icono, size=18, color=ft.Colors.PRIMARY),
                     ft.Text(titulo, size=15, weight=ft.FontWeight.BOLD,
                             color=ft.Colors.ON_SURFACE)],
                    spacing=8, tight=True,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER),
             ft.Text(ayuda, size=13, color=GRIS, no_wrap=False)],
            spacing=4, tight=True)

    def _construir(self) -> None:
        self.blq_empresa, self.dd_empresa = campo_opciones(
            "Empresa", list(NOMBRES_EMPRESAS), width=320,
            on_change=lambda _e: (self._recargar_sucursales(), self._actualizar_estado()))
        self.blq_sucursal, self.dd_sucursal = campo_opciones(
            "Sucursal", [], width=280,
            on_change=lambda _e: self._actualizar_estado())
        self.progreso = ft.ProgressRing(width=22, height=22, stroke_width=3, visible=False)
        self.txt_estado = ft.Text("", size=13, color=GRIS)
        # La URL base ya no se edita aquí (vive en Configuración): se muestra para
        # que se vea QUÉ va a codificar el QR antes de imprimir cientos.
        self.txt_url = ft.Text(size=12, no_wrap=False)
        self._pintar_url()

        # Búsqueda de UN activo (Enter genera directo). Se busca por etiqueta,
        # serie o insumo porque en campo no siempre se tiene el número legible.
        self.tf_buscar = buscador(
            "Etiqueta, serie o insumo… (Enter genera)", width=420,
            on_submit=self._generar_individual)

        # El botón rápido solo tiene sentido con la API configurada; sin ella la
        # única vía es la descarga completa por navegador (botón del encabezado).
        from core import activos_sipp
        self._con_api = activos_sipp.hay_api()
        fila_datos = [self.blq_empresa, self.blq_sucursal]
        if self._con_api:
            # Junto a empresa/sucursal: actualizar es lo que se hace JUSTO DESPUÉS
            # de elegirlas, y tenerlo abajo con las de generar mezclaba dos cosas
            # distintas (traer datos vs. producir etiquetas).
            fila_datos.append(
                boton_primario("Actualizar activos (rápido)", ft.Icons.BOLT,
                               self._actualizar_activos_api,
                               tooltip="Trae del SIPP solo los activos de la empresa, "
                                       "por API y sin abrir el navegador"))
        fila_datos.append(self.progreso)

        activos = tarjeta_seccion(ft.Column(
            [self._encabezado(
                ft.Icons.INVENTORY_2, "Activos",
                "Elige de qué empresa y sucursal se generarán las etiquetas."),
             ft.Row(fila_datos, spacing=14, wrap=True,
                    # Los campos llevan rótulo arriba y el botón no: sin alinear
                    # abajo, el botón queda flotando a media altura de los campos.
                    vertical_alignment=ft.CrossAxisAlignment.END),
             self.txt_estado],
            spacing=12, tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH))

        # Etiqueta suelta: lo habitual en campo es tener UN activo enfrente (se
        # despegó o se dañó su etiqueta) y no querer reimprimir la hoja completa
        # de su departamento.
        individual = tarjeta_seccion(ft.Column(
            [self._encabezado(
                ft.Icons.QR_CODE, "Etiqueta individual",
                "¿Solo necesitas una? Busca el activo y guarda su etiqueta en PNG."),
             ft.Row([self.tf_buscar,
                     boton_secundario("Generar etiqueta", ft.Icons.QR_CODE,
                                      self._generar_individual,
                                      tooltip="Genera el PNG de la etiqueta de ese "
                                              "activo (QR + número)")],
                    spacing=12, wrap=True,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER)],
            spacing=12, tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH))

        lote = tarjeta_seccion(ft.Column(
            [self._encabezado(
                ft.Icons.LIBRARY_BOOKS, "Etiquetas por lote",
                "Genera de una vez las etiquetas de la empresa y sucursal elegidas "
                "arriba: en carpetas por departamento o en una hoja para imprimir."),
             ft.Row([boton_secundario(
                         "Generar carpeta por departamento", ft.Icons.FOLDER_ZIP,
                         self._generar_carpeta,
                         tooltip="Un PNG por activo (QR + etiqueta) en subcarpetas "
                                 "por departamento"),
                     boton_secundario("Generar etiquetas (PDF)", ft.Icons.QR_CODE_2,
                                      self._generar_pdf)],
                    spacing=12, wrap=True)],
            spacing=12, tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH))

        pie = ft.Row(
            [self.txt_url,
             boton_herramienta("Configurar", ft.Icons.SETTINGS_OUTLINED,
                               self._abrir_configuracion,
                               tooltip="Cambiar la URL base en Configuración")],
            spacing=8, wrap=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER)

        self.contenido = ft.Column(
            [activos, individual, lote, pie],
            spacing=16, expand=True, scroll=ft.ScrollMode.AUTO,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
        self._actualizar_estado()

    # ------------------------------------------------------ estado
    def _empresa_id(self) -> "int | None":
        return ID_POR_EMPRESA.get(self.dd_empresa.value) if self.dd_empresa.value else None

    def _sucursal_sel(self) -> str:
        """Sucursal elegida ('' = todas)."""
        val = self.dd_sucursal.value or ""
        return "" if val == _TODAS else val

    def _recargar_sucursales(self) -> None:
        """Rellena el combo de sucursal con las presentes en la empresa cacheada."""
        idemp = self._empresa_id()
        sucs = db.sucursales_activos_sipp(idemp) if idemp is not None else []
        # _TODAS se interpreta como "sin filtro" (ver _sucursal_sel).
        self.dd_sucursal.options = (
            [ft.DropdownOption(key=_TODAS, text=_TODAS)]
            + [ft.DropdownOption(key=s, text=s) for s in sucs])
        self.dd_sucursal.value = _TODAS
        self._safe_update()

    def _actualizar_estado(self) -> None:
        idemp = self._empresa_id()
        if idemp is None:
            self.txt_estado.value = "Elige una empresa."
        else:
            n = len(db.listar_activos_sipp(idemp, self._sucursal_sel() or None))
            suf = (f" (sucursal «{self._sucursal_sel()}»)" if self._sucursal_sel()
                   else "")
            self.txt_estado.value = (
                f"{n} activo(s) con etiqueta en caché para «{self.dd_empresa.value}»{suf}."
                if n else f"Sin activos descargados para «{self.dd_empresa.value}». "
                          "Usa «Actualizar información del SIPP».")
        self._safe_update()

    def _url_base(self) -> str:
        """URL base de los QR, LEÍDA AL VUELO desde la configuración.

        Nunca se guarda en un atributo: si se cambia entre una generación y otra,
        imprimir con la anterior manda los QR a una ruta muerta, y eso se descubre
        con las etiquetas ya pegadas."""
        from core import qr
        return qr.base_url()

    def _pintar_url(self) -> None:
        base = self._url_base()
        if base:
            self.txt_url.value = f"El QR llevará: {base} + la etiqueta del activo."
            self.txt_url.color = GRIS
        else:
            self.txt_url.value = ("Sin URL base configurada: el QR llevará solo el "
                                  "número de etiqueta.")
            self.txt_url.color = NARANJA

    def _abrir_configuracion(self, _e=None) -> None:
        self.app.config.abrir()

    def tras_configurar(self) -> None:
        """Gancho del shell: la configuración cambió (p. ej. la URL base)."""
        self._pintar_url()
        self._safe_update()

    # ------------------------------------------------ etiqueta individual
    async def _generar_individual(self, _e=None) -> None:
        """Genera la etiqueta de UN activo: se teclea su etiqueta (o serie/insumo),
        se elige de los resultados y se guarda su PNG.

        Si hay empresa seleccionada acota la búsqueda a ella; si no, busca en toda
        la caché: quien tiene el número en la mano no suele saber de qué empresa
        es."""
        texto = (self.tf_buscar.value or "").strip()
        if not texto:
            self.app.avisar("Escribe una etiqueta, serie o insumo para buscarlo.",
                            NARANJA)
            return
        hallados = db.buscar_activos_sipp(texto, self._empresa_id(), limite=50)
        if not hallados:
            ambito = (f"«{self.dd_empresa.value}»" if self._empresa_id() is not None
                      else "los activos descargados")
            self.app.avisar(
                f"Sin coincidencias para «{texto}» en {ambito}. Actualiza los "
                "activos de la empresa o revisa el dato.", NARANJA, duracion=8000)
            return
        # Una sola coincidencia: se genera sin preguntar (el caso de teclear la
        # etiqueta completa, que es el más común).
        if len(hallados) == 1:
            await self._guardar_etiqueta_png(hallados[0])
            return
        self._elegir_activo(hallados)

    def _elegir_activo(self, hallados: list) -> None:
        """Lista las coincidencias para que el usuario elija de cuál generar."""
        lista = lista_resultados()
        modal = Modal(self.page, "¿De cuál activo?", ancho=620,
                      subtitulo=f"{len(hallados)} coincidencias")

        async def elegir(activo: dict) -> None:
            modal.cerrar()
            await self._guardar_etiqueta_png(activo)

        for a in hallados:
            detalle = " · ".join(p for p in (
                a.get("insumo") or "",
                f"serie {a['serie']}" if a.get("serie") else "",
                a.get("empresa") or "", a.get("sucursal") or "") if p)
            lista.controls.append(fila_resultado(
                a.get("etiqueta") or "", a.get("insumo") or "(sin insumo)", detalle,
                on_click=lambda _e, x=a: self.page.run_task(elegir, x)))
        modal.cuerpo.controls = [lista]
        modal.set_acciones([boton_secundario("Cancelar",
                                             on_click=lambda _e: modal.cerrar())])
        modal.abrir()

    async def _guardar_etiqueta_png(self, activo: dict) -> None:
        """Pide dónde guardar y escribe el PNG (QR + número) de ese activo."""
        from core import qr

        etq = (activo.get("etiqueta") or "").strip()
        destino = await self.app.picker.save_file(
            dialog_title="Guardar etiqueta QR", file_name=f"{etq}.png",
            allowed_extensions=["png"])
        if not destino:
            return
        ruta = destino if destino.lower().endswith(".png") else destino + ".png"
        base = self._url_base()

        def escribir() -> None:
            with open(ruta, "wb") as fh:
                fh.write(qr.png_etiqueta(activo, base))

        try:
            await asyncio.to_thread(escribir)
        except Exception as exc:  # noqa: BLE001 — se reporta al usuario
            self.app.avisar(error_al_guardar(exc, ruta), ROJO, duracion=10000)
            return
        self.app.avisar(
            f"Etiqueta {etq} generada.", VERDE, accion="Abrir",
            on_accion=lambda _e: self.app.abrir_en_sistema(ruta), duracion=8000)

    # --------------------------------------- actualizar activos (API, sin RPA)
    async def _actualizar_activos_api(self, _e=None) -> None:
        """Trae del SIPP los activos de la empresa por API y refresca la pantalla.

        Es la vía rápida de esta pantalla: las etiquetas solo necesitan etiqueta,
        insumo, serie y sucursal, que es justo lo que el endpoint entrega. Nada de
        navegador, login ni catálogos que aquí no se usan."""
        from core import activos_sipp

        idemp = self._empresa_id()
        if idemp is None:
            self.app.avisar("Elige una empresa.", NARANJA)
            return
        empresa = self.dd_empresa.value or ""
        self.progreso.visible = True
        self.txt_estado.value = "Consultando el SIPP…"
        self.txt_estado.color = GRIS
        self._safe_update()

        def avance(traidos: int, total: int) -> None:
            # Llega desde el hilo de la descarga; la UI se toca en su propio hilo.
            self.page.run_task(self._pintar_avance, traidos, total)

        try:
            res = await asyncio.to_thread(
                activos_sipp.descargar_activos_api, idemp, empresa, avance)
        except Exception as exc:  # noqa: BLE001 — se reporta al usuario
            self.progreso.visible = False
            self.txt_estado.value = ""
            self._safe_update()
            self.app.avisar(f"No se pudieron traer los activos: {exc}", ROJO,
                            duracion=9000)
            return

        self.progreso.visible = False
        self._recargar_sucursales()
        self._actualizar_estado()

        # Una etiqueta repetida es un activo que NO tendrá su QR: la caché guarda
        # uno por etiqueta, así que el otro no se imprime. Hay que decirlo.
        repetidas = res.get("duplicadas") or []
        if repetidas:
            muestra = ", ".join(repetidas[:3]) + ("…" if len(repetidas) > 3 else "")
            self.app.avisar(
                f"{res['guardados']} activo(s) actualizado(s). Ojo: {len(repetidas)} "
                f"etiqueta(s) están repetidas en el SIPP ({muestra}); de cada una "
                "solo se imprimirá un activo.", NARANJA, duracion=11000)
        else:
            self.app.avisar(f"{res['guardados']} activo(s) actualizado(s) "
                            f"de «{empresa}».", VERDE)

    async def _pintar_avance(self, traidos: int, total: int) -> None:
        self.txt_estado.value = f"Descargando activos… {traidos}/{total}"
        self.txt_estado.color = GRIS
        self._safe_update()

    # ------------------------------------------------ actualizar SIPP (RPA)
    # El botón vive en el encabezado (app.py); el shell llama a estos ganchos.
    def fijar_empresa(self, nombre: str) -> None:
        self.dd_empresa.value = nombre
        self._recargar_sucursales()

    def tras_actualizar_sipp(self) -> None:
        self._recargar_sucursales()
        self._actualizar_estado()

    # ------------------------------------------------ generar carpeta por depto
    async def _generar_carpeta(self, _e=None) -> None:
        """Genera un PNG por activo (QR + etiqueta) en subcarpetas por departamento."""
        idemp = self._empresa_id()
        if idemp is None:
            self.app.avisar("Elige una empresa.", NARANJA)
            return
        activos = db.listar_activos_sipp(idemp, self._sucursal_sel() or None)
        if not activos:
            self.app.avisar("No hay activos para esa empresa/sucursal. "
                            "Descárgalos primero.", NARANJA)
            return
        carpeta = await self.app.picker.get_directory_path(
            dialog_title="Elige dónde crear las carpetas de etiquetas QR")
        if not carpeta:
            return
        import os

        from core.qr import _sanear_nombre
        # Nombre de la carpeta raíz: "Etiquetas QR - Empresa[ - Sucursal]"
        # (la sucursal solo si se filtró por una en concreto).
        partes = ["Etiquetas QR", self.dd_empresa.value or ""]
        if self._sucursal_sel():
            partes.append(self._sucursal_sel())
        nombre_raiz = _sanear_nombre(" - ".join(p for p in partes if p))
        raiz = os.path.join(carpeta, nombre_raiz)

        ui_loop = asyncio.get_running_loop()
        txt = ft.Text(f"Generando {len(activos)} etiqueta(s)…", size=13)
        barra = ft.ProgressBar(value=0)
        modal = Modal(self.page, "Generando carpeta de etiquetas", ancho=440)
        modal.cuerpo.controls = [txt, barra]
        modal.abrir()

        def avance(hechos: int, total: int) -> None:
            def aplicar() -> None:
                txt.value = f"Generando etiquetas… {hechos}/{total}"
                barra.value = hechos / total if total else None
                modal.refrescar()
            ui_loop.call_soon_threadsafe(aplicar)

        base = self._url_base()
        # Sin sucursal fija (Todas): se agrupa además por sucursal
        # (raíz / Sucursal / Departamento). Con una sucursal elegida, solo por depto.
        por_sucursal = not self._sucursal_sel()
        from core import qr
        try:
            res = await asyncio.to_thread(
                qr.generar_carpeta_por_departamento, activos, raiz, base, avance,
                por_sucursal)
        except Exception as exc:  # noqa: BLE001 — se reporta al usuario
            modal.cerrar()
            self.app.avisar(f"No se pudo generar: {exc}", ROJO)
            return
        modal.cerrar()
        detalle = (f"en {res['sucursales']} sucursal(es), {res['departamentos']} "
                   f"departamento(s)" if por_sucursal
                   else f"en {res['departamentos']} carpeta(s) por departamento")
        self.app.avisar(
            f"{res['generados']} etiqueta(s) {detalle}.", VERDE,
            accion="Abrir carpeta", on_accion=lambda _e: self.app.abrir_en_sistema(raiz),
            duracion=8000)

    # ------------------------------------------------ generar PDF
    async def _generar_pdf(self, _e=None) -> None:
        idemp = self._empresa_id()
        if idemp is None:
            self.app.avisar("Elige una empresa.", NARANJA)
            return
        activos = db.listar_activos_sipp(idemp, self._sucursal_sel() or None)
        if not activos:
            self.app.avisar("No hay activos descargados para esa empresa. "
                            "Descárgalos primero.", NARANJA)
            return
        destino = await self.app.picker.save_file(
            dialog_title="Guardar etiquetas QR",
            file_name=f"Etiquetas QR {self.dd_empresa.value}.pdf",
            allowed_extensions=["pdf"])
        if not destino:
            return
        ruta = destino if destino.lower().endswith(".pdf") else destino + ".pdf"

        self.progreso.visible = True
        self._safe_update()
        base = self._url_base()
        # El PDF se genera con Chromium (Playwright), que en Windows necesita el
        # loop Proactor de BucleRpa para lanzar el subproceso del navegador.
        from core import qr
        from core.rpa_sipp import BucleRpa
        bucle = BucleRpa()
        try:
            n = await asyncio.wrap_future(
                bucle.enviar(qr.generar_pdf_etiquetas(activos, ruta, base)))
        except Exception as exc:  # noqa: BLE001 — se reporta al usuario
            self.app.avisar(error_al_guardar(exc, ruta), ROJO, duracion=10000)
            return
        finally:
            bucle.cerrar()
            self.progreso.visible = False
            self._safe_update()
        self.app.avisar(
            f"{n} etiqueta(s) generadas.", VERDE,
            accion="Abrir", on_accion=lambda _e: self.app.abrir_en_sistema(ruta),
            duracion=7000)

    # ------------------------------------------------ utilidades
    def _on_resize(self, _e=None) -> None:
        """Contenido fluido; no requiere recomputar."""

    def _safe_update(self) -> None:
        try:
            self.contenido.update()
        except (RuntimeError, AssertionError, AttributeError):
            pass
