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

## B.4 Ambiente de pruebas

El RPA opera contra **stage**: `https://stage.sipp.petroil.dev` (fijado en
`core/rpa_sipp.py` `SesionSipp.BASE_URL`).
