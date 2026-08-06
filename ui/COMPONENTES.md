# Catálogo de componentes (`ui/componentes.py`)

Referencia de firmas para no tener que leer el módulo entero. **Antes de
construir un `ft.FilledButton`, `ft.TextField`, `ft.Dropdown` o `ft.AlertDialog`
a mano, busca aquí el equivalente.** Si ninguno encaja, **añade el parámetro que
falta al componente** en vez de armar el control suelto: es lo que permite
cambiar el estilo de toda la app desde un solo archivo.

Los colores salen de [tema.py](tema.py). Nunca escribas un hex: usa roles de
Material 3 (`ft.Colors.PRIMARY`, `ON_SURFACE_VARIANT`, `ERROR`,
`SURFACE_CONTAINER_HIGHEST`).

---

## Tokens

```python
RADIO = 8                 # radio de esquina estándar
PAD_H, PAD_V = 24, 10     # relleno de botón
GAP_SM, GAP_MD, GAP_LG = 8, 16, 24
GUTTER_SCROLL = 14        # hueco para que la barra de scroll no pise el contenido
SOMBRA_N1                 # elevación 1
ALTO_CAMPO_TABLA = 35     # alto de los campos dentro de una fila de tabla
```

Ritmo vertical de 4px: alturas y márgenes divisibles entre 4.

## Botones

```python
boton_primario(texto, icono=None, on_click=None, tooltip=None, disabled=False)
boton_primario_icono(icono, tooltip, on_click=None, *, disabled=False)
boton_secundario(texto, icono=None, on_click=None, tooltip=None, disabled=False)
boton_herramienta(texto, icono=None, on_click=None, tooltip=None, destructivo=False)
icono_accion(icono, tooltip, on_click, *, color=None)   # 24x24, para el suffix de un campo
```

`boton_primario_icono` es la acción principal reducida a su ícono, del lado de
`ALTO_CAMPO_TABLA` para casar con los campos compactos en una barra de filtros.
Su `tooltip` es obligatorio: sin texto visible, es lo único que dice qué hace.

**Un solo `boton_primario` por bloque de acciones**; el resto, secundarios.
`boton_herramienta` es la acción menor sobre una selección; `destructivo=True` la
pinta en `ERROR`.

`icono_accion` mide 24×24 exactos porque esa es la caja de contenido de un campo
Material. **Nunca metas un `ft.IconButton` en un `suffix`**: impone un mínimo
táctil de 48px y estira el campo.

### El puntero de mano

```python
puntero_mano(caja)        # muta la caja y la devuelve
CURSOR_BOTON              # mapa por estado, para el `style` de un botón
```

**`ft.Container` no expone `mouse_cursor`** —solo lo tienen los botones de
Material y `GestureDetector`—, así que una caja con `on_click` se queda con la
flecha y no se lee como pulsable.

`puntero_mano` mete un `GestureDetector` **dentro** de la caja, envolviendo su
contenido, y devuelve la misma caja. Va sin manejadores, y como Flet solo
registra los reconocedores de las devoluciones que no son `None`, el clic sigue
llegando al Container con su `ink` y su `on_hover` intactos:

```python
caja = ft.Container(..., on_click=..., ink=True)
fila.controls.append(puntero_mano(caja))    # sigue siendo `caja`
```

**Por fuera no funciona.** Un Container pulsable dibuja su PROPIA región de
ratón, y con regiones anidadas Flutter se queda con el cursor de la más interna:
envolviendo la caja, ella misma lo pisaba en cuanto el puntero entraba de verdad.
El precio de meterlo dentro es el RELLENO de la caja (2–8px), donde el cursor
sigue siendo la flecha; cubrirlo exigiría quitarle el `on_click` al Container y
con él la tinta.

Como el árbol no gana controles, `expand`, `width` y `tooltip` se quedan donde
estaban. Los componentes de este módulo ya lo traen; **si armas una caja pulsable
nueva, pásala por aquí**.

En los botones el cursor va por `style`, con `CURSOR_BOTON`: es un mapa por
estado para que el botón apagado no ofrezca una mano que no lleva a nada.

## Campos

```python
campo_texto(etiqueta=None, *, valor="", hint=None, width=None, on_submit=None,
            on_blur=None, prefix_icon=None, password=False, read_only=False,
            suffix=None, expand=False, flotante=False)   -> (bloque, campo)

campo_opciones(etiqueta, opciones, *, valor=None, width=None, hint=None,
               on_change=None, flotante=False)          -> (bloque, campo)

buscador(hint, on_submit=None, width=420, *, expand=False, autofocus=False)
CampoFecha(page, etiqueta, valor="", on_change=None, flotante=False)  # .control / .value
CampoEtiquetado(bloque, campo)                                       # .control / .value
```

**Contrato de retorno**: `campo_texto` y `campo_opciones` devuelven **una tupla
`(bloque, campo)`**. El *bloque* va al layout; el *campo* es el que expone
`.value`. Olvidarlo es el error más común:

```python
bloque, self.tf_serie = campo_texto("No. de serie")
fila.controls.append(bloque)         # el bloque
valor = self.tf_serie.value          # el campo
```

**Dos estilos de rótulo**, según el mockup de la pantalla:

| | dónde va la etiqueta | dónde se usa |
|---|---|---|
| por defecto | arriba del campo | paneles, formularios de página |
| `flotante=True` | rótulo nativo de Material, encajado en el borde | modales |

Con `flotante` no hay bloque aparte: se devuelve el campo dos veces, de modo que
desempaquetar la tupla sigue funcionando igual.

Para formularios **dinámicos** usa `CampoEtiquetado`, que empaqueta el par en un
objeto con `.control` y `.value` — mismo contrato que `CampoFecha`, así se guarda
una sola referencia por campo (ver [captura_activo.py](captura_activo.py)).

**Fechas siempre por calendario**, nunca un `TextField` tecleado: `CampoFecha`
expone `.value` como `'DD/MM/AAAA'` (`comun.FORMATO_FECHA`).

### Regla de altura (no la rompas)

`_estilo_campo` **no** toca `dense`, `height` ni `content_padding`. La altura
estándar de Material ya es idéntica entre `TextField` y `Dropdown`; en cuanto se
fija cualquiera de esos tres, el `Dropdown` deja de alinear con los `TextField` y
los bordes inferiores dejan de coincidir. **Lo único que puede variar entre
campos de un mismo grupo es el `width`.**

En `ft.Dropdown`, `bgcolor` es el fondo del **menú desplegable**, no del campo;
el relleno del campo es `fill_color`.

## Campos compactos (tablas y barras de filtros)

```python
campo_tabla_texto(*, valor="", on_blur=None, ancho=None)

SelectCompacto(page, opciones, *, valor="", ancho=None, titulo="Seleccionar",
               on_change=None, disabled=False)   # .control, .value, .disabled,
                                                 # .set_opciones(opciones, valor)


campo_tabla_opciones(opciones, *, valor=None, on_change=None, ancho=None,
                     page=None, titulo="Seleccionar")   # atajo: devuelve .control
```

**`SelectCompacto` es la única forma de tener un select bajo en este proyecto**
(35px en vez de los 56 de Material). No existe alternativa: `ft.Dropdown` ignora
`dense`, `height` y `content_padding`, así que reducirlo por esa vía es
imposible. Úsalo en tablas y en barras de filtros; trae buscador, que a partir de
unas pocas decenas de opciones deja de ser un lujo.

`campo_tabla_opciones` es el mismo control devolviendo solo `.control`, para
celdas donde el valor se lee por el `on_change` de la fila y no hace falta
conservar el objeto.

Aquí la caja (borde, radio, fondo, alto) **la dibujamos nosotros** y dentro va
solo el contenido, porque en una fila compacta ningún campo de Material se deja
medir: `ft.Dropdown` ignora `content_padding` y `dense`, y `height` /
`size_constraints` tampoco funcionan (el decorador recalcula su alto por su
cuenta). Con la caja propia, texto y select miden lo mismo **por construcción**.

`campo_tabla_opciones` abre un diálogo **con buscador** en vez de un menú, porque
los catálogos son largos (~58 empresas). Su `on_change` recibe un evento con
`e.control.value`, igual que un campo nativo.

El cursor de mano lo lleva dentro (ver `puntero_mano`), y como el select se apaga
y se enciende, guarda ese `GestureDetector` en `._cursor` para devolverle la
flecha al deshabilitarlo: un select bloqueado que ofrece la mano promete un menú
que no va a abrirse.

Para cambiar el alto: mueve `ALTO_CAMPO_TABLA` (los dos lo comparten) y verifica
que `_ALTO_FILA` en [tabla_responsiva.py](tabla_responsiva.py) siga siendo mayor.

## Contenedores y secciones

```python
tarjeta_seccion(contenido, *, padding=GAP_LG)
seccion_formulario(titulo, icono, campos, columnas=2)
Pestanas(definiciones, al_cambiar, activa=None)   # .control, .activa, .set_conteo(clave, n)
```

`seccion_formulario` reparte los campos en filas de `columnas`. **Con un solo
campo, pasa `columnas=1`**: si no, la fila se rellena con una celda vacía y el
campo ocupa la mitad.

`Pestanas` es el control segmentado (pista gris, activa en relieve):

```python
tabs = Pestanas([("todos", "Todos", ft.Icons.LIST_ALT), ...], al_cambiar=cb)
tabs.set_conteo("todos", 42)
```

## Modales

```python
Modal(page, titulo, *, subtitulo="", ancho=760, alto_cuerpo=None,
      acciones=None, al_cerrar=None)
```

Atributos y métodos: `.cuerpo` (el `Column` que llenas), `.abrir()`, `.cerrar()`,
`.refrescar()`, `.subtitulo` (propiedad), `.set_acciones(lista)`.

```python
self.modal = Modal(page, "Capturar activo", subtitulo=clave,
                   acciones=[boton_secundario("Cancelar", on_click=...),
                             boton_primario("Guardar", on_click=...)])
self.modal.cuerpo.controls = [seccion_formulario(...), ...]
self.modal.abrir()
```

**No armes un `ft.AlertDialog` a mano.** `AlertDialog` no expone animaciones, así
que `Modal` lo deja transparente y dibuja él la tarjeta visible (fondo, borde,
radio, sombra) en un `Container` animado: el fundido abarca todo lo visible.
Además trae cierre con **Esc** y con la **X**, y `_ajustar_alto()` recorta el
cuerpo a lo que quepa en la ventana — sin eso, en pantallas cortas los botones
del pie quedan fuera de vista.

`page.on_keyboard_event` es un **slot único**: `Modal` guarda el manejador
anterior al abrir y lo restaura al cerrar. Si registras teclado dentro de un
modal, respeta ese encadenamiento.

## Listados de búsqueda

```python
fila_resultado(clave, titulo, subtitulo="", *, on_click=None, resaltado=False)
lista_resultados(alto=320)
```

La fila **entera** es pulsable (mucho más área que un botón "Elegir" al final).
El patrón completo — `Modal` + `buscador` + filtrado en vivo + Enter elige el
primero — está en [selector_insumo.py](selector_insumo.py) y
[selector_empleado.py](selector_empleado.py); cópialo para cualquier selector
nuevo.

---

## Trampas de layout que ya nos costaron tiempo

**Un `Column` no estira a sus hijos** salvo que lleve
`horizontal_alignment=ft.CrossAxisAlignment.STRETCH`. Es la causa recurrente de
que un campo no llene su bloque o una tarjeta no ocupe el ancho. Si un `expand`
más abajo "no hace nada", busca el `Column` sin `STRETCH` que rompe la cadena.

**Un `DropdownMenu` de Material se dimensiona al ancho de su opción más larga**,
no al del contenedor. `campo_opciones` pasa `expanded_insets=Padding.all(0)`
cuando no se pidió un ancho fijo. Aun así queda un caso sin resolver (el select
de tipo de activo en el modal de captura).

**Flutter ignora `border_radius` cuando el borde no es uniforme.** Para una
banda de acento en un solo lado, métela dentro con
`clip_behavior=ft.ClipBehavior.ANTI_ALIAS`.

**Todo estilo del `TextTheme` debe llevar `color`.** Declarar un `TextTheme`
sustituye a la tipografía por defecto de Material, y un estilo sin color deja el
texto en **blanco e invisible** sobre fondo claro (encabezados de tabla, títulos
de diálogo). Debe ser un rol (`ON_SURFACE`), no un hex.

**Actualiza una sola vez.** Refrescar un control ya refresca a sus hijos; enviar
además el ancestro duplica el árbol serializado. Al repintar una tabla dentro de
un refresco mayor, usa `set_contenido(filas, refrescar=False)` y deja que el
ancestro haga el único envío.
