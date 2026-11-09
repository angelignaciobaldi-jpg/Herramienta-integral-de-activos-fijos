# Referencia — Módulo de Activos Fijos del SIPP (portal productivo)

> Análisis del DOM real del módulo de Activos Fijos del SIPP, extraído de la
> captura `Paginas html/Pagina catálogo activos fijos` (portal **productivo**).
> Sirve como fuente de verdad para: (1) el esquema de `core/db.py`, (2) los
> localizadores del RPA (`core/rpa_sipp.py`) y (3) el diseño de las pantallas.
>
> **Solo estructura** (nombres de campos, controles y operaciones) — sin datos
> reales. El HTML fuente contiene datos reales y **no se versiona** (`.gitignore`).
>
> Fuente: SIPP productivo · app AngularJS `appActivosNuevo` /
> controlador `ctrlappActivosNuevo` · grid virtualizado **ngGrid**.

---

## 1. Modelo de datos del activo (formulario de alta — `filtrosAgregar.*`)

| Campo SIPP (`ng-model`) | Etiqueta / significado |
|---|---|
| `id_TipoActivo` | Tipo de activo |
| `id_InsumoOrigen` / `nb_NombreInsumo` | Insumo de origen (catálogo de insumos) |
| `nu_Serie` | Número de serie |
| `nu_Etiqueta` | Etiqueta / número de inventario |
| `nb_Factura` | Número de factura |
| `nb_Proveedor` | Proveedor |
| `im_Costo` | Costo / importe |
| `de_DescripcionActivo` | Descripción del activo |
| `de_Ubicacion` | Ubicación |
| `de_Enlace` | Enlace |
| `id_EmpresaAgregar` / `id_SucursalAgregar` | Empresa / sucursal de compra |
| `id_CentroCosto` / `id_GrupoCentroCosto` / `id_Departamento` | Clasificación contable (compra) |
| `id_EmpresaResguardo` / `id_SucursalResguardo` | Empresa / sucursal de resguardo |
| `id_CentroCostoResguardo` / `id_GrupoCentroCostoResguardo` / `id_DepartamentoResguardo` | Clasificación contable (resguardo) |
| `nb_Empleado` | Empleado responsable (resguardo) |
| `id_Situacion` | Situación del activo |
| `sn_DatosCompra` | ¿Capturar datos de compra? |
| `sn_GenerarCentroCosto` | ¿Generar centro de costo? |
| `FH_ADQUISICION` | Fecha de adquisición |
| `FH_ASIGNACION` | Fecha de asignación |
| `FH_GARANTIA` | Fecha de garantía |
| `ar_ArchivoSoporte` / `ar_ArchivoSoporteFactura` / `ar_ArchivoSoporteCR` | Archivos soporte (máx. 3) |

> Las variantes con sufijo `_EDITAR` (`filtrosEditar.*`, `FH_*_EDITAR`) son el
> mismo modelo para el flujo de edición.

### Activo o dado de baja (`SN_ACTIVO`)

El listado del portal lo trae como **texto** (`'True'` / `'False'`); el de la API
**no lo trae en absoluto**. En Aske, 864 de 2,408 activos están dados de baja
(36%), así que no es un caso raro.

Importa porque el portal **no deja editar un activo inactivo**: la herramienta lo
mandaba al RPA y el cambio se perdía sin aviso. La herramienta lo guarda en la
columna `activo` de la caché (NULL = no se ha consultado el portal, y se trata
como activo), lo refresca en la pasada de «Traer detalles del SIPP» y, con la
etiqueta repetida, se queda con el VIVO: es el único que se puede modificar.

Ojo con el booleano: `bool('False')` es `True` en Python. Se normaliza en
`core.db._bandera`.

### Campos de detalle del insumo (Marca, Modelo, RAM…)

Son los «Detalles Insumo» del formulario: dinámicos por insumo, no columnas del
activo. **No salen en ningún listado**, ni en el del portal
(`getListadoActivosFijos`) ni en el de la API (`/api/activos-fijos/listado`, que
solo devuelve empresa, sucursal, empleado, fecha, etiqueta, serie, nombre y
precio, e ignora cualquier parámetro para ampliarlo). Se piden **uno por activo**:

```
component:  ActivosFijosNuevo
execMethod: obtenerCamposDetalle
args:       {id_Empresa, id_ActivoFijo}
->  COLUMNS: ID_EMPRESA, ID_ACTIVOFIJO, ID_CAMPODETALLE, NB_CAMPODETALLE,
             DE_VALORCAMPODETALLE
```

Puntos medidos en vivo:

- La respuesta trae el RÓTULO (`NB_CAMPODETALLE`) junto al valor, así que **no
  hace falta** consultar aparte `ConfiguracionCamposDetalle.listar`.
- `id_ActivoFijo` es obligatorio y numérico: pedirlo vacío para traerlos todos de
  golpe devuelve error. El id solo viaja en el listado del PORTAL
  (`ID_ACTIVOFIJO`), no en el de la API.
- Coste real: ~170 ms por activo de uno en uno, ~30 ms con 8 en paralelo (~10 s
  por 300 activos). El listado de la empresa, que hace falta para traducir
  etiqueta → id, tarda ~15 s.

Lo usa `core.activos_sipp.descargar_detalles`, que guarda el resultado en
`extra['detalles']` de la caché y en la foto (`datos_sipp`) del levantamiento.

### Fotografía del activo (alta y edición)

| Formulario | Input | `ng-change` |
|---|---|---|
| Alta | `#ar_ArchivoSoporte` | `subirFotografia(this)` |
| Edición | `#ar_ArchivoSoporteEditar` | `subirFotografiaEditar(this)` |
| Consulta | `#ar_ArchivoSoporte` (deshabilitado) | `subirFotografiaEditar(this)` |

El id se repite entre la edición y la consulta, así que el localizador fiable es
el `ng-change` **más** `:not([disabled])`.

Reglas del portal (`js/controllers/ActivosFijosNuevo.js`), todas validadas en el
navegador y no en el servidor:

- Máximo **3 fotos** por activo y **2 MB** por archivo, solo JPG/JPEG/PNG/PDF. Lo
  que no cumple dispara un *alert* y el input se limpia: no sube nada y deja el
  aviso encima del formulario.
- Las fotos viven en `$scope.ar_ArchivosFotografias`. Borrar una
  (`borrarFotografiaEdit`) **no la quita del arreglo**: le deja `nombre` y `ruta`
  en blanco. Al guardar, los huecos se emparejan por POSICIÓN con
  `$scope.fotografias` para conservar su `id_Fotografia`, así que reutilizar un
  hueco ACTUALIZA esa foto en vez de crear otra.
- La subida es asíncrona (el archivo va a un almacenamiento externo): la foto
  solo existe cuando el servidor responde y aparece en el arreglo. Guardar antes
  la pierde sin avisar.
- Al guardar la edición, si el activo lleva alguna foto el portal **exige**
  empleado de resguardo; sin él aborta con un mensaje en línea y no se guarda
  nada del activo.

### Bloque de depreciación (`formB.*`)

| Campo | Significado |
|---|---|
| `nu_importe` | Importe / monto total |
| `nu_importeDepreciado` | Importe depreciado |
| `nu_faltanteDepreciar` / `nb_tiempofaltanteDepreciar` | Faltante por depreciar (monto / tiempo) |
| `nu_tiempoDepreciacion` | Tiempo de depreciación |
| `nb_depreciado` | Tiempo depreciado |
| `nb_motivo` / `nb_observaciones` | Motivo y observaciones (baja/ajuste) |

---

## 2. Operaciones del módulo (funciones `ng-click` del controlador)

| Flujo | Función(es) |
|---|---|
| Alta | `guardarActivoFijo()` · `confAgregarActivo()` · `agregarFilaRegistro()` |
| Edición | `guardarActivoFijoEditar()` |
| Baja / eliminación | `guardarBaja()` · `eliminarActivo()` |
| Carga masiva | `guardarActivosMasivo()` (modal `cargaMasiva`, `js_CargaMasiva.nu_activos` / `nu_seriesText`) |
| Asignación | `guardarAsignacion()` |
| Reasignación | `guardarReasignacion(sn_opcionReasignar)` · `confReasignacion()` (modelo `reasignarModalFiltros.*`) |
| Carta responsiva | `generarCartaResponsiva(1,true)` · `guardarSubirCartaResponsiva()` · `confCartaResponsiva()` · `confSubirCartaResponsiva()` |
| Etiquetas | `generarEtiqueta()` |
| Reportes | `generarReporte()` / `generarReporte(1)` · `confReportes()` (modelo `filtrosReporte.*`) |
| Autorización | `autorizar()` (bandeja `listadoBandejaAutorizacion`) |
| Comentarios | `guardarComentarios()` · `guardarComentariosCancelar()` |
| Contraseña de confirmación | `de_password` · `sn_guardarPass` ("guardar contraseña 30 min") |

---

## 3. Grids (ngGrid) — `listarDatosGrid('<nombre>')`

- `listadoActivosFijos` — **catálogo principal de activos**
- `listadoActivosFijosCartaResponsiva`
- `listadoActivosFijosReasignacion`
- `listadoBandejaAutorizacion`
- `listadoEmpleados`
- `listadoInsumos`
- `listadoProveedores`

> ⚠️ El ngGrid solo renderiza en el DOM las columnas del grid **visible** al
> guardar la página. En esta captura quedó el de *insumos* (Cve Insumo, Insumo,
> Unidad Medida, Familia, SubFamilia). Las columnas exactas de `listadoActivosFijos`
> están definidas en el JS del controlador (externo) — **pendiente**: capturar el
> HTML con esa grid ya cargada, o el `columnDefs` del controlador.

---

## 4. Modales — `abrirModal('<nombre>', <n>)`

`activosFijosLista`, `cargaMasiva`, `empleados`, `insumos`, `proveedores`,
`reasignaciones`.

## 5. Conjuntos de filtros (por pantalla/modal)

- `js_filtroListado.*` — filtros del listado principal (etiqueta, serie, empresa,
  centro de costo, situación, tipo, empleado resguardo, económico, estatus).
- `filtrosReporte.*` — filtros del reporte.
- `filtrosReasignacion.*` / `reasignarModalFiltros.*` — reasignación.
- `filtrosCartaResponsiva.*` / `filtrosSubirCartaResponsiva.*` / `filtrosBandeja.*`
  — carta responsiva y bandeja de autorización.
- `filtrosEditar.*` / `filtrosAgregar.*` — edición y alta.

---

## 6. Catálogos dependientes (a resolver: RPA/API vs. local)

El alta/edición dependen de catálogos que hoy viven en el SIPP:
**tipos de activo, insumos, empleados, centros de costo / grupos / departamentos,
situaciones, proveedores, empresas y sucursales**. Decisión de diseño pendiente:
traerlos en vivo (RPA/API) o mantener copias locales sincronizables.

---

## 7. Notas para el RPA (`core/rpa_sipp.py`)

- Los `ng-model` de arriba son los localizadores estables (misma técnica que los
  selects "chosen" de tesorería: operar el `<select>` nativo por su `ng-model`).
- El `listadoActivosFijos` se recorrería con el mismo patrón de **scroll
  virtualizado** (viewport `ngViewport`, clave única por fila) ya implementado
  para el reporte de cuentas en tesorería.
- Fechas: se abren con `fnc_openRedCalendar($event,'<campo>')` y hay campos con
  máscara `dt_FH_*` (mismo enfoque de tecleo de dígitos que en tesorería).

---

## 8. Capturas pendientes por analizar

- [ ] HTML del `listadoActivosFijos` **visible** (para las columnas exactas).
- [ ] (otros HTML que comparta el usuario).

---

# Anexo A — Bandeja de Compra de Activos Fijos

> Módulo distinto: app AngularJS `appBandejaCompraActivos` /
> controlador `ctrlBandejaCompraActivos`. Fuente: captura
> `Paginas html/Pagina bandeja de compras` (nombre interno en pantalla:
> **"Bandeja de Compra de Activos Fijos"**). Grid ngGrid.

Pantalla de **consulta/seguimiento de entradas de compra** de activos
(movimientos de almacén que luego se registran como activos fijos). Es
principalmente de lectura + filtros + exportar/autorizar; no captura el activo.

### A.1 Filtros (`filtros.*` + fechas)

| `ng-model` | Etiqueta |
|---|---|
| `id_Empresa` / `id_Sucursal` / `id_Almacen` | Empresa / Sucursal / Almacén |
| `id_proveedor` / `nb_proveedor` | Proveedor |
| `id_ordenDeCompra` | Folio OC (orden de compra) |
| `id_Requisicion` | Requisición (modal de seguimiento) |
| `Insumos` / `nb_NombreInsumo` / `de_SerieInsumo` | Insumo / nombre / serie |
| `id_FamiliaInsumo` / `id_SubFamiliaInsumo` | Familia / SubFamilia de insumo |
| `id_TipoMovimiento` | Tipo de movimiento |
| `Empleados` | Empleados |
| `fh_inicio` / `fh_fin` (`dt_fh_*`) | Rango de fechas |
| `sn_ActivoFijo` | Solo activos fijos |
| `sn_EntradaPendienteFactura` | Entradas pendientes de factura |
| `sn_InsumoRelevante` | Insumo relevante |

También: `Folio Entrada`, `T.C.` (tipo de cambio) como campos visibles.

### A.2 Columnas del grid (movimientos)

`Cantidad` · `Insumo` · `Tipo de Movimiento` · `Fecha Movimiento` ·
`Usuario Movimiento` · `Centro Costo` · `Grupo Centro Costo` · `Comentarios`.

### A.3 Acciones (`ng-click`)

`obtenerListado()` / `buscarCambios()` (listar/refrescar) · `autorizar()` ·
`generarExcel()` · `imprimir()` · `descargar()` ·
`abrirFile(dir, filename, local)` (adjuntos: facturas/soporte) ·
modal de seguimiento de requisición · confirmación con contraseña
(`de_password`, `sn_guardarPass`).

### A.4 Relación con el catálogo

La bandeja es el **paso previo** al alta del activo: las entradas de compra
(OC, proveedor, factura, insumo, centro de costo, empresa/sucursal) alimentan lo
que en el catálogo se registra como activo fijo. Comparten campos clave
(insumo, proveedor, factura, centro de costo/grupo, empresa/sucursal). Flujo
probable: **compra → entrada en bandeja → alta de activo fijo**. Implicación de
diseño: el módulo de *Registro de activos* podría partir de una entrada de compra
(traída por RPA/API) y solo completar resguardo/depreciación, en vez de captura
100% manual. **A confirmar en el levantamiento.**

---

# Anexo B — Edición de activo y CAMPOS POR TIPO (camposDetalle)

> Captura: `Paginas html/Pagina de edicion de activo fijo.html` — un activo de
> tipo **Maquinaria y Equipo** (insumo "SONDA RIGIDA", situación "Solo Resguardo").
> Misma app `appActivosNuevo`.

## B.1 Catálogo OFICIAL de tipos de activo (id_TipoActivoFijo)

Provisto por el área (reemplaza los índices inferidos antes):

| id | Tipo | id | Tipo |
|---|---|---|---|
| 1 | Edificios | 7 | Equipo informático |
| 2 | Terrenos | 8 | Celulares |
| 3 | Maquinaria y Equipo | 9 | Embarcaciones |
| 4 | Vehículos Utilitarios | 10 | Aeronaves |
| 5 | Vehículos Pesados | 11 | Tanques |
| 6 | Mobiliario y equipo de Oficina | 12 | Activos Intangibles |

Reflejado en `core/tipos_activo.py` (`TIPOS_ACTIVO`).

## B.2 Mecanismo de campos por tipo — DINÁMICO (camposDetalle)

Los campos particulares por tipo **no son ng-models fijos**. Se renderizan bajo la
sección **"Detalles Insumo"** con:

```
ng-repeat="(key, item) in camposDetalle"
  rótulo:  {{ item.NB_CAMPODETALLE }}
  valor:   ng-model="camposDetalle[$index]['DE_VALORCAMPODETALLE']"
```

Es una lista dinámica que depende del **insumo** (que a su vez pertenece a un tipo/
familia). Para el ejemplo de Maquinaria y Equipo, las características fueron:
**Marca · Modelo · Cliente**.

**Implicación para el RPA (Fase 2):** para llenar estos campos NO se usa un
localizador fijo; se recorre el `ng-repeat` de `camposDetalle`, se lee cada rótulo
`item.NB_CAMPODETALLE` y se escribe el valor capturado en el input de
`DE_VALORCAMPODETALLE` de esa fila (emparejando por el nombre del rótulo). En
`core/tipos_activo.py` estos campos se marcan con `detalle=True` y su `etiqueta` es
el rótulo a emparejar.

## B.3 Otras notas del formulario de edición

- Modelo de edición: `filtrosEditar.*` (mismos campos que el alta; usa
  `nb_CentroCosto`/`nb_GrupoCentroCosto` en texto, más `id_TipoActivo`, `nu_Serie`,
  `nu_Etiqueta`, `im_Costo`, `nb_Factura`, `nb_Proveedor`, resguardo, situación).
- El rótulo de Serie cambia por tipo: **"Serie / Clave Catastral"** (Clave Catastral
  aplica a Edificios/Terrenos).
- Fechas del activo: `FH_ADQUISICION`, `FH_ASIGNACION`, `FH_GARANTIA` (abiertas con
  `fnc_openRedCalendar`).

## B.5 El empleado de resguardo NO se cambia desde la edición

Hallazgo verificado en el DOM real (`Paginas html/Pagina de edicion de activo
fijo.html`). Importa porque explica por qué «Comparar SIPP vs Excel» marca
*Empleado resguardo* como **solo local** y no lo empuja.

En el formulario de edición, el empleado viene bloqueado por partida doble:

```html
<input ng-model="filtrosEditar.nb_Empleado" readonly="">
<input ng-model="filtrosEditar.id_EmpleadoResguardo" ng-disabled="true">
```

Y **no hay modal que abrir**: el portal tiene seis botones «Buscar Empleado»
(`abrirModal('empleados', <n>)`) y ninguno pertenece a la edición.

| índice | contexto |
|--------|----------|
| 2 | `filtrosAgregar` (ALTA — el que usa hoy `SesionSipp.seleccionar_empleado`) |
| 3 | `filtrosReasignacion` |
| 4 | `filtrosCartaResponsiva` |
| 5 | `filtrosBandeja` |
| 6 | `filtrosSubirCartaResponsiva` |
| 7 | `filtrosReporte` |

El botón más cercano a `filtrosEditar.nb_Empleado` está a ~46,500 caracteres: es
el del alta, otro formulario.

### Contraste: el INSUMO sí se puede cambiar en la edición

Conviene no meter los dos en el mismo saco (la comparación marca ambos como
`empujable=False`, pero por razones distintas). El insumo también es `readonly` +
`ng-disabled="true"`, **pero tiene su botón «Buscar Insumo» pegado al campo**, en
el mismo `<div class="col-md-8">`, a ~196 caracteres:

```html
<input ng-model="filtrosEditar.nb_NombreInsumo" ng-disabled="true" readonly="">
<button ng-click="abrirModal('insumos')" class="btn-icon25p btn-buscar25p"></button>
```

Ese `abrirModal('insumos')` **no lleva índice** (a diferencia de `'empleados'`): es
el mismo modal para las tres pantallas que lo usan, y `SesionSipp.seleccionar_insumo`
ya resuelve cuál botón pulsar con `_primer_visible`. Es decir: automatizar el cambio
de insumo en la modificación es viable con lo que ya existe; automatizar el cambio de
empleado NO, porque ahí no hay modal que abrir.

### La vía que sí existe: REASIGNACIÓN

El SIPP trata el cambio de resguardo como un **movimiento**, no como una edición
—coherente con que de ahí salgan las cartas responsivas—. Es una acción por fila
del listado:

```
grid.appScope.abrirModal('reasignacion', 0, row.entity)   ->   guardarReasignacion(sn_opcionReasignar)
```

Campos del modal (`reasignarModalFiltros.*`); solo el empleado es obligatorio:

| campo | nota |
|-------|------|
| `id_EmpleadoNuevo` | **obligatorio**, `select` con `chosen` |
| `id_EmpresaNuevo`, `id_SucursalNuevo` | |
| `id_GrupoCentroCostoNuevo`, `id_CentroCostoNuevo` | |
| `id_DepartamentoNuevo`, `de_UbicacionNuevo` | |
| `sn_GenerarCentroCostoModal`, `sn_habilitarCamposReasignacion` | banderas del propio modal |

Automatizarlo es viable (el buscador de empleados ya lo maneja
`SesionSipp.seleccionar_empleado`), pero es un flujo distinto al de modificación y
con efectos propios en el portal. **Decisión de agosto 2026: se documenta, no se
implementa por ahora.**

## B.3 bis Generación del CENTRO DE COSTO (`sn_GenerarCentroCosto`)

Confirmado leyendo `js/controllers/ActivosFijosNuevo.js` en stage y contra datos
reales (noviembre 2026). El portal **no** pide el nombre del centro de costo: lo
arma el servidor con

```
<grupo de centro de costo> - <insumo> - <No. Economico>
```

Ejemplo real en stage: el TRACTOCAMION con grupo `Asamaz` y campo detalle
`No. Economico = a3wd3` tiene el centro `Asamaz - TRACTOCAMION - a3wd3`.

**De dónde sale cada parte**

| parte | origen |
|-------|--------|
| grupo | `filtrosAgregar.id_GrupoCentroCostoResguardo` (combo del alta) |
| insumo | `filtrosAgregar.nb_NombreInsumo` |
| económico | **campo detalle `ID_CAMPODETALLE = 10`**, rotulado `No. Economico` (sin acento) |

El económico NO es un campo del formulario: `obtenerNumeroEconomico()` lo busca
entre los `camposDetalle` del insumo (por id 10 o por un rótulo que contenga
«economico», ignorando acentos) y lo manda como `num_Economico`. Es decir, va por
la misma vía que Marca/Modelo/Placa: `core/tipos_activo.py` con `detalle=True`.

**La casilla**

`filtrosAgregar.sn_GenerarCentroCosto` (`id="checkboxGenerar"`,
`ng-change="habilitarComboCentroCostoResguardo()"`). Llega **deshabilitada** y no
hace nada mientras `id_GrupoCentroCostoResguardo` esté vacío: el handler sale
antes. Al marcarla, **vacía** `id_CentroCostoResguardo` —el centro se va a crear,
así que no se elige uno—.

**Subtipo: lo deriva el portal del tipo de activo, no lo elige nadie**

```
id_TipoActivo 4, 9, 10  -> id_SubTipoActivoFijo = 2 (Utilitario)
id_TipoActivo 5         -> id_SubTipoActivoFijo = 3 (Transporte)
cualquier otro          -> id_SubTipoActivoFijo = 1 (Activo Fijo)
```

**Matriz de validación al guardar** (solo si la casilla está marcada). Cruza la
NATURALEZA DEL INSUMO —`SN_UTILITARIO` / `SN_EQUIPOTRANSPORTE`, que vienen en el
listado de insumos— contra ese subtipo:

| insumo | subtipo | resultado |
|--------|---------|-----------|
| transporte | 1 Activo Fijo | **alerta y NO guarda** |
| utilitario | 1 Activo Fijo | **alerta y NO guarda** |
| utilitario | 2 Utilitario | crea el CC con económico |
| transporte | 3 Transporte | crea el CC con económico |
| transporte | 2 Utilitario | **alerta y NO guarda** |
| utilitario | 3 Transporte | **alerta y NO guarda** |
| ninguno | 3 Transporte | crea el CC **y marca el insumo como transporte** |
| ninguno | 2 Utilitario | crea el CC **y marca el insumo como utilitario** |

Las dos últimas filas tienen efecto en el CATÁLOGO DE INSUMOS
(`sn_ActualizarTransporte` / `sn_ActualizarUtilitario`), no solo en el activo.

Con subtipo 1 la casilla no aborta nada, pero `sn_CrearCCNumEconomico` se queda
en 0: ahí no hay número económico que concatenar.

El mismo mecanismo existe en el modal de EDICIÓN
(`editarModalFiltros.sn_GenerarCentroCostoModal`, `generarCC()`) y en el de
REASIGNACIÓN (`reasignarModalFiltros.sn_GenerarCentroCostoModal`,
`generarCCReasignar()`), con su propia copia de la matriz.

Los campos detalle del insumo se piden con
`ConfiguracionCamposDetalle.listar {id_subFamiliaInsumo, id_subFamiliaDinamica}`
—ambos ids vienen en el listado de insumos—; `ActivosFijosNuevo.obtenerCamposDetalle
{id_Empresa, id_ActivoFijo}` devuelve los VALORES ya guardados de un activo.

## B.4 Ambiente de pruebas

El entorno del RPA se elige con la variable de entorno **`SIPP_BASE_URL`**; por
defecto es **productivo** (`https://sipp.petroil.com.mx`). Para probar contra
stage sin mezclar datos: `probar_sipp.bat stage`, que además manda base,
preferencias y credenciales a una carpeta aparte (`ACTIVOS_FIJOS_DATOS`).
Entornos: `test`, `stage` y `dev` bajo `*.sipp.petroil.dev` (`dev` no resuelve).
