# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Idioma

Código, comentarios, docstrings, nombres de identificadores y textos de UI van en
**español**. Es el estándar del proyecto, no una preferencia opcional.

## Comandos

```bash
pip install -r requirements.txt
python -m playwright install chromium    # navegador del RPA (una sola vez)
python app.py                            # correr la app en desarrollo

python scripts/smoke_import.py           # ÚNICA verificación automatizada
```

**No hay suite de pruebas ni linter configurado.** `scripts/smoke_import.py`
importa `app` + todos los módulos de `core/` y `ui/`, y es la compuerta que corre
el CI antes de compilar: si algo no importa, el build falla ahí. Córrelo tras
cualquier cambio estructural (renombrar, mover, agregar imports). Para verificar
un módulo suelto: `python -c "import ui.registro_activos"`.

Los `# noqa: BLE001` del código siguen convención de ruff, pero no hay
configuración de ruff en el repo.

### Empaquetado (opcional, para probar el instalable)

```bash
construir.bat            # -> dist\ActivosFijos\ActivosFijos.exe (flet pack, onedir)
iscc instalador.iss      # -> Output\Instalador_ActivosFijos.exe (Inno Setup)
```

La publicación real es automática: se dispara al **publicar un Release** en
GitHub (`.github/workflows/compilar.yml`). El CI reescribe `core/version.py` e
`instalador.iss` con el tag antes de compilar — **el tag ES la versión**; no la
edites a mano o provocas bucles de actualización.

## Documentación existente (y su deriva)

`ARQUITECTURA.md` es el documento del **estándar compartido** entre las
herramientas de escritorio Quetzaltic/Grupo Petroil (esta y la de Tesorería).
Léelo para principios, seguridad, rutas y el flujo de release.

⚠️ **`ARQUITECTURA.md` y `README.md` están desactualizados respecto a este repo.**
Ambos documentan `core/ocr.py`, `core/exportador.py`, `ui/extraccion_documentos.py`,
`ui/automatizacion_sipp.py` y `ui/exportacion.py`, que fueron **eliminados** en el
commit `faad336`. No existen OCR ni exportación aquí. Los módulos vivos son los
que están en disco; verifica antes de confiar en esas listas.

## Diseño visual

[DISENO.md](DISENO.md) es el sistema de diseño del proyecto ("Systematic
Integrity"): paleta, tipografía, retícula, elevación, formas y componentes. Tenlo
en cuenta al construir o modificar interfaz.

**La paleta y la tipografía ya están aplicadas**: viven en [ui/tema.py](ui/tema.py)
(esquemas claro y oscuro + `TextTheme`) y se enganchan en `main()` de `app.py`.

### Componentes: léelos ANTES de tocar interfaz

[ui/componentes.py](ui/componentes.py) es la forma estándar de crear **botones,
pestañas, campos, modales y tarjetas de sección**. Su catálogo de firmas,
contratos y trampas está en **[ui/COMPONENTES.md](ui/COMPONENTES.md)**:
consúltalo antes de escribir cualquier control. Las tarjetas del tablero viven
aparte, en [ui/tarjetas.py](ui/tarjetas.py).

**Ninguna pantalla construye ya un `ft.FilledButton`, `ft.TextField`,
`ft.Dropdown` ni `ft.AlertDialog` suelto**, y debe seguir así: es lo único que
permite cambiar el estilo en todas a la vez. Si necesitas una variante, **añade
el parámetro al componente**; no armes el control a mano ni lo estilices en la
pantalla.

Los tres contratos que más se olvidan:

- `campo_texto` / `campo_opciones` devuelven **`(bloque, campo)`**: el bloque va
  al layout, el campo es el que expone `.value`.
- El rótulo va **arriba** del campo en paneles y formularios de página, y
  `flotante=True` (rótulo nativo de Material, encajado en el borde) en
  **modales**.
- **Fechas siempre por calendario**: `CampoFecha`, nunca un `TextField`
  tecleado. `comun.py` solo conserva `parse_fecha`/`fmt_fecha`/`FORMATO_FECHA`.

Un `Column` **no estira a sus hijos** salvo que lleve
`horizontal_alignment=CrossAxisAlignment.STRETCH`. Es la causa recurrente de que
un campo no llene su bloque o una tarjeta no ocupe el ancho; `_bloque_etiquetado`
ya lo aplica.

Reglas al tocar interfaz:

- **Usa roles de Material 3, nunca hex sueltos** (`ft.Colors.PRIMARY`,
  `ON_SURFACE_VARIANT`, `ERROR`, `SURFACE_CONTAINER_HIGHEST`). Es lo que permitió
  aplicar todo el sistema sin modificar una sola pantalla; escribir un color
  literal rompe esa propiedad y descuadra el modo oscuro.
- **Los colores se cambian en `ui/tema.py`**, que es la fuente única. Ambos
  esquemas salen de la misma rampa tonal, así que un valor tocado a mano en uno
  desalinea al otro.
- **Todo estilo del `TextTheme` debe llevar `color`.** Declarar un `TextTheme`
  sustituye a la tipografía por defecto de Material; un estilo sin color deja el
  texto en blanco e invisible sobre fondos claros (afecta a todo lo que no fije
  color propio: encabezados de tabla, títulos de diálogo). Debe ser un rol
  (`ON_SURFACE`), no un hex, para no romper el modo oscuro.
- **Ritmo vertical de 4px**: alturas y márgenes divisibles entre 4.
- Ancho fluido, sin tope máximo; `page.window.min_width = 960`. La responsividad
  es por **escalones de columna** derivados de un piso de legibilidad, no por
  breakpoints de dispositivo (no hay build móvil). Scroll solo vertical.

`DISENO.md` cierra con **"Pendientes de implementar"**: la fuente Inter (no está
en el repo; hoy se usan las métricas sobre Segoe UI) y las sombras de elevación.

Ojo con un punto donde el documento **se desvía a propósito** del diseño original:
este pedía un *navigation rail* lateral y se decidió **conservar la navegación
horizontal del encabezado** ([app.py](app.py), `_construir_nav`), porque un riel
consume ancho fijo y el ancho es el recurso escaso a 960px. No lo "corrijas"
hacia el riel.

## Arquitectura

### Shell y contrato de pantalla

[app.py](app.py) es el shell: ventana, tema, encabezado, navegación y
auto-updater. Cada pantalla es un módulo independiente en `ui/` que no conoce a
las demás. El contrato:

```python
class SeccionX:
    def __init__(self, app): ...   # recibe el shell; de ahí saca page/picker/avisar
    self.contenido: ft.Control     # raíz que el shell monta
    def _on_resize(self, e): ...   # opcional; el shell lo registra si existe
    def cargar_desde_db(self): ... # opcional; datos iniciales
```

Puntos no obvios del shell:

- **Navegación por `visible`**, no por `Tabs`: todas las pantallas se montan a la
  vez en un `Column` y solo se alterna cuál es visible. Registrar una pantalla
  nueva son dos lugares: `_construir` (instancia + lista `_secciones`) y
  `_construir_nav` (etiqueta + ícono, **mismo orden**).
- **`page.on_resize` es un slot único**, así que el shell lo multiplexa con
  `registrar_on_resize` ([app.py:53](app.py#L53)). No lo sobrescribas directamente.
- **Imports perezosos**: solo `flet` y `core.rutas` al tope de `app.py`. Todo lo
  demás se importa dentro de funciones para que un módulo roto no impida arrancar
  el auto-updater (que podría traer la corrección). Mantén esa disciplina.
- Servicios que el shell inyecta vía `self` (la instancia `app`): `page`,
  `picker`, `avisar()`, `abrir_en_sistema()`.

### Rutas: desarrollo vs. empaquetado

[core/rutas.py](core/rutas.py) resuelve `BUNDLE` / `INSTALL` / `DATOS` según
`sys.frozen`. **Todo lo que se escribe en runtime va a `DATOS`**, nunca junto al
`.exe`.

Consecuencia en desarrollo: las tres apuntan a la carpeta del proyecto, así que
`python app.py` crea `activos_fijos.db`, `preferencias.json` y credenciales **en
la raíz del repo**. Están en `.gitignore`, pero contienen datos locales reales —
no los borres a la ligera ni los versiones.

### Tablero (dashboard)

[ui/dashboard.py](ui/dashboard.py) es un **ejemplo de disposición**: los valores
salen de `_EJEMPLO`, no de la base. El punto de cableado a datos reales es
`cargar_desde_db`.

Se apoya en dos piezas reutilizables:

- [ui/rejilla.py](ui/rejilla.py) — retícula "bento" de 12 columnas. Cada `Bloque`
  declara `(col, fila, ancho, alto)` en celdas y se posiciona en absoluto dentro
  de un `Stack`. Se usa porque **ningún control nativo hace esto**: `GridView`
  solo da celdas uniformes y `ResponsiveRow` no tiene span vertical. El `Stack`
  deja encimar sin quejarse, así que la no-superposición se valida al insertar
  (`ColisionRejilla`).
- [ui/tarjetas.py](ui/tarjetas.py) — tarjetas que **no fijan tamaño**: llenan la
  caja que les den y eligen densidad según el alto/ancho medido, soltando lo
  accesorio antes que desbordar. Al añadir una variante, hereda de `Tarjeta` e
  implementa `_adaptar`; muta los controles montados, no los recrees.

### Modelo de datos

[core/db.py](core/db.py) es SQLite sin servidor. Contiene **dos entidades, y solo
una está viva**:

- **`Levantamiento`** — la entidad real del dominio. Es el inventario levantado en
  campo, con `clave_unica` que unifica sus dos orígenes (Excel y ZIP), `estatus`
  contra el catálogo del SIPP (`EST_PENDIENTE` / `EST_DADO_ALTA` /
  `EST_NO_DADO_ALTA`) y funciones de **paginación y filtrado en SQL**
  (`listar_levantamiento_pagina`, `contar_levantamiento`) — la pantalla se
  congelaba trayendo todo a memoria, no revierta eso a un `listar()` completo.
- **`Activo`** (+ `guardar`, `listar`, `actualizar`, `eliminar`,
  `InventarioDuplicado`) — **andamiaje muerto**: cero llamadas fuera de `db.py`.
  No lo tomes como el modelo del dominio ni construyas sobre él.

`inicializar()` crea tablas **y aplica migraciones incrementales** (`ALTER TABLE`
sin romper bases existentes). La lista de columnas es fuente única para
INSERT/UPDATE y migraciones: al agregar un campo, actualízala y agrega su
migración ahí mismo.

`Insumo` y `Empleado` son cachés locales de catálogos descargados del SIPP.

### RPA del SIPP

[core/rpa_sipp.py](core/rpa_sipp.py) opera el portal SIPP (AngularJS) con
Playwright. `SesionSipp` encapsula lo común (login, empresa/sucursal, menús,
diagnósticos en `_diagnostico_rpa/`); los flujos concretos de activos fijos se
agregan encima, reutilizándola en vez de duplicar automatización.

Dos piezas que hay que respetar al integrarlo con la UI:

- **`BucleRpa`** ([core/rpa_sipp.py:843](core/rpa_sipp.py#L843)) corre asyncio en
  un hilo dedicado. Playwright ata sus objetos al loop donde se crearon, así que
  **todas** las corrutinas del RPA van al mismo bucle vía `enviar()`; desde un
  manejador async de Flet: `await asyncio.wrap_future(bucle.enviar(coro))`.
- **`ControlRpa`** da pausa/reanudar/detener cooperativos. El flujo llama
  `await punto_control()` en puntos seguros; detener lanza `RpaDetenido`, que
  **no es error** — se trata como parada limpia, sin diálogo de error.

Localizadores orientados al usuario (`get_by_role`/`get_by_placeholder`) con
respaldo por `ng-model`. Chromium **no se empaqueta**: se descarga a `DATOS` la
primera vez (`asegurar_navegador`).

### Formulario dinámico por tipo de activo

[core/tipos_activo.py](core/tipos_activo.py) es config declarativa: qué campos
pide el alta del SIPP y cuáles son obligatorios, por tipo de activo. La pantalla
de captura arma el formulario leyendo de ahí y el RPA usa el `ng_model` de cada
campo como localizador. **Para agregar o cambiar campos, edita ese archivo — no
la UI.** El DOM de referencia está en [docs/SIPP_Modulo_Activos_Fijos.md](docs/SIPP_Modulo_Activos_Fijos.md).

## API de Flet: usa la de 0.85, no la de los tutoriales

El proyecto corre **Flet 0.85** (`flet>=0.85`), cuya API rompe con casi todo el
material antiguo. En este repo se usa:

`ft.run(main)` · `ft.Colors` / `ft.Icons` (capitalizados) · `page.show_dialog()` y
`page.pop_dialog()` · `page.services.append(picker)` · `ft.Padding.symmetric(...)` ·
`ft.Alignment(0, 0)` · `ft.Border` / `ft.BorderSide` · `ft.BoxFit.CONTAIN`

No escribas `ft.app(...)`, `ft.colors`, `ft.icons`, `page.dialog = ...` ni
`page.snack_bar = ...`: son de versiones previas y fallan. Ante la duda,
introspecciona (`python -c "import flet as ft; print(ft.Container.__dataclass_fields__)"`)
en lugar de asumir.

Para medir un contenedor existe `Container.on_size_change` (evento
`LayoutSizeChangeEvent` con `width`/`height`) + `size_change_interval` como
debounce. `Stack` y `Container` **no** tienen `scroll`; solo `Column`, `Row`,
`ListView` y `GridView`.

## Convenciones de código

- `from __future__ import annotations` al tope de cada módulo.
- **`core/` no importa Flet.** La lógica (BD, red, RPA, Excel) vive ahí y es
  probable sin interfaz; `ui/` orquesta.
- **Trabajo pesado fuera del hilo de UI**: `asyncio.to_thread(...)` para red,
  descargas y archivos.
- **Errores best-effort en lo no crítico** (color de barra de título, taskbar,
  persistencia de ventana): `try/except` que nunca tumba el arranque. Lo crítico
  se reporta al usuario con `avisar()`.
- **Fechas siempre por calendario**: nunca un `TextField` tecleado. Usa
  `ui.componentes.CampoFecha`, que expone `.value` como `'DD/MM/AAAA'`
  (`comun.FORMATO_FECHA`).
- Comentarios que explican el **porqué**, sobre todo en los *workarounds* de
  Windows/DWM, Playwright y el updater.
- Helpers compartidos de UI en [ui/comun.py](ui/comun.py); el catálogo de
  empresas es fuente única en `core/empresas.py` y se re-exporta desde ahí.
- Para tablas anchas, [ui/tabla_responsiva.py](ui/tabla_responsiva.py) dimensiona
  columnas por **porcentaje** con piso en px, midiéndose con `on_size_change`, y
  **muta** los controles montados en vez de recrearlos al redimensionar.

## Secretos

Nunca en el repo ni en claro. Contraseñas del SIPP y token de API se cifran con
**DPAPI** (`core/dpapi.py`, atado a la cuenta de Windows) en
`credenciales_rpa.json` / `token_api.json` dentro de `DATOS`. El PAT de GitHub del
updater va en la variable de entorno `QUETZALTIC_GITHUB_PAT` o en un `.env` junto
al `.exe` (ver `.env.example`).
