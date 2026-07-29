# Sistema de diseño — "Systematic Integrity"

> Guía de diseño visual de la **Herramienta Integral de Activos Fijos**. Traducida
> e integrada a partir del `DESIGN.md` generado con Stitch.
>
> Complementa a [ARQUITECTURA.md](ARQUITECTURA.md): aquel define **cómo se
> estructura** el código; este define **cómo se ve** la interfaz.

**Estado:** conflictos resueltos (ver [Decisiones tomadas](#decisiones-tomadas)).
La **paleta y la tipografía ya están aplicadas** en [ui/tema.py](ui/tema.py) y
enganchadas en [app.py](app.py).

---

## Tokens

Fuente única de valores. Las **claves no se traducen**: son identificadores que
mapean a los roles de Material 3 de Flet (ver [Cómo se aterriza en Flet](#cómo-se-aterriza-en-flet)).

### Claro

```yaml
name: Systematic Integrity
colors:
  surface: '#f9f9f9'
  surface-dim: '#dadada'
  surface-bright: '#f9f9f9'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#f3f3f3'
  surface-container: '#eeeeee'
  surface-container-high: '#e8e8e8'
  surface-container-highest: '#e2e2e2'
  on-surface: '#1a1c1c'
  on-surface-variant: '#454652'
  inverse-surface: '#2f3131'
  inverse-on-surface: '#f1f1f1'
  outline: '#767683'
  outline-variant: '#c6c5d4'
  surface-tint: '#4c56af'
  primary: '#000666'
  on-primary: '#ffffff'
  primary-container: '#1a237e'
  on-primary-container: '#8690ee'
  inverse-primary: '#bdc2ff'
  secondary: '#0061a4'
  on-secondary: '#ffffff'
  secondary-container: '#33a0fd'
  on-secondary-container: '#00355c'
  tertiary: '#000f5b'
  on-tertiary: '#ffffff'
  tertiary-container: '#072189'
  on-tertiary-container: '#7e90f8'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#e0e0ff'
  primary-fixed-dim: '#bdc2ff'
  on-primary-fixed: '#000767'
  on-primary-fixed-variant: '#343d96'
  secondary-fixed: '#d1e4ff'
  secondary-fixed-dim: '#9ecaff'
  on-secondary-fixed: '#001d36'
  on-secondary-fixed-variant: '#00497d'
  tertiary-fixed: '#dee0ff'
  tertiary-fixed-dim: '#bac3ff'
  on-tertiary-fixed: '#00105c'
  on-tertiary-fixed-variant: '#293ca0'
```

### Oscuro

Derivado de la **misma rampa tonal** que el claro, no inventado aparte. Material 3
construye ambos temas de la misma familia de tonos, y los tokens `*-fixed` del
claro ya contienen los que el oscuro necesita: el `primary` oscuro es el
`inverse-primary` del claro, su contenedor es `on-primary-fixed-variant`, y así.
Por eso los dos temas combinan en lugar de parecer dos diseños distintos.

```yaml
colors-dark:
  surface: '#121314'
  surface-dim: '#121314'
  surface-bright: '#38393a'
  surface-container-lowest: '#0d0e0f'
  surface-container-low: '#1a1c1c'
  surface-container: '#1e2021'
  surface-container-high: '#292a2b'
  surface-container-highest: '#343536'
  on-surface: '#e2e2e2'
  on-surface-variant: '#c6c5d4'     # = outline-variant del claro
  inverse-surface: '#e2e2e2'
  inverse-on-surface: '#2f3131'
  outline: '#90909d'
  outline-variant: '#454652'        # = on-surface-variant del claro
  surface-tint: '#bdc2ff'
  primary: '#bdc2ff'                # = inverse-primary del claro (tono 80)
  on-primary: '#1a2280'             # tono 20
  primary-container: '#343d96'      # = on-primary-fixed-variant (tono 30)
  on-primary-container: '#e0e0ff'   # = primary-fixed (tono 90)
  inverse-primary: '#4c56af'
  secondary: '#9ecaff'
  on-secondary: '#003258'
  secondary-container: '#00497d'
  on-secondary-container: '#d1e4ff'
  tertiary: '#bac3ff'
  on-tertiary: '#10267e'
  tertiary-container: '#293ca0'
  on-tertiary-container: '#dee0ff'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  # Los tokens *-fixed son IDÉNTICOS en claro y oscuro (por definición de M3).
```

**Contraste verificado (WCAG):** todos los pares de texto pasan AA (≥4.5:1) en
ambos temas. El claro va de 4.55 a 17.24; el oscuro, de 7.21 a 14.36. El único
valor por debajo es `outline`/`surface` en claro (4.25), que es correcto: `outline`
se usa para bordes y divisores, no para texto, y supera el 3:1 que exige WCAG
1.4.11 para componentes no textuales.

### Resto de tokens

```yaml
typography:
  headline-lg: { fontFamily: Inter, fontSize: 32px, fontWeight: '700', lineHeight: 40px, letterSpacing: -0.02em }
  headline-md: { fontFamily: Inter, fontSize: 24px, fontWeight: '600', lineHeight: 32px, letterSpacing: -0.01em }
  headline-sm: { fontFamily: Inter, fontSize: 20px, fontWeight: '600', lineHeight: 28px }
  body-lg:     { fontFamily: Inter, fontSize: 16px, fontWeight: '400', lineHeight: 24px }
  body-md:     { fontFamily: Inter, fontSize: 14px, fontWeight: '400', lineHeight: 20px }
  label-lg:    { fontFamily: Inter, fontSize: 14px, fontWeight: '600', lineHeight: 20px, letterSpacing: 0.01em }
  label-md:    { fontFamily: Inter, fontSize: 12px, fontWeight: '500', lineHeight: 16px, letterSpacing: 0.02em }
  code:        { fontFamily: monospace, fontSize: 13px, fontWeight: '400', lineHeight: 18px }
rounded:
  sm: 0.25rem      # 4px
  DEFAULT: 0.5rem  # 8px
  md: 0.75rem      # 12px
  lg: 1rem         # 16px
  xl: 1.5rem       # 24px
  full: 9999px
spacing:
  unit: 4px
  xs: 4px
  sm: 8px
  md: 16px
  lg: 24px
  xl: 32px
  gutter: 16px
  margin: 24px
  window-min-width: 960px   # sustituye a container-max-width
```

---

## Marca y estilo

El sistema está diseñado para **gestión empresarial de alta densidad y
orquestación de datos**. La personalidad de marca es autoritaria pero accesible:
prioriza la claridad y la eficiencia funcional por encima del adorno. Va dirigido
a operadores profesionales que necesitan una experiencia tipo *"heads-up
display"*, que minimice la carga cognitiva y maximice el flujo de datos.

El estilo es **Corporativo Moderno** con énfasis en **capas tonales**. Se apoya en
la estética estructurada y por widgets del framework Flet, enfatizando límites
claros, agrupación lógica y un enfoque sistemático de la densidad de la interfaz.
La respuesta emocional buscada es de **fiabilidad, precisión y control sereno**.

## Colores

La paleta se basa en un espectro de azules profesionales que establecen confianza
y jerarquía. Los nombres de abajo son **roles**, no valores sueltos: úsalos por
rol y el tema resuelve el color en claro u oscuro.

- **`primary` (`#000666`):** acciones principales y elementos de marca. Es un azul
  marino casi negro que aporta una sensación asentada y autoritaria.
- **`primary-container` (`#1a237e`):** el azul marino de superficie para
  navegación de alto nivel y fondos de énfasis. Es el color que la gente
  reconocerá como "el azul de la app".
- **`secondary` (`#0061a4`) y `secondary-container` (`#33a0fd`):** estados
  interactivos, indicadores de progreso y llamadas a la acción; atraen la
  atención sin resultar estridentes.
- **`surface` (`#f9f9f9`):** lienzo neutro que reduce el brillo de pantalla.
- **`surface-container-lowest` (`#ffffff`):** tarjetas, modales y contenedores de
  datos; eleva el contenido y señala interactividad.
- **`outline-variant` (`#c6c5d4`):** bordes de tarjetas y campos.

## Tipografía

Se elige **Inter** por su legibilidad excepcional en entornos con mucha
información. La jerarquía guía la vista a través de tableros complejos y
formularios administrativos.

- **Titulares:** interletraje más cerrado y pesos más gruesos para anclar
  secciones.
- **Cuerpo:** optimizado para lectura extensa y captura de datos, manteniendo un
  interlineado estándar de 1.5x para dar aire.
- **Etiquetas:** encabezados de tabla, títulos de formulario y metadatos
  pequeños; suelen usar peso medio o semi-negrita para distinguirse del cuerpo.

> ⚠️ **Inter no está en el repo** (no hay ningún `.ttf`/`.otf`). Hoy las métricas
> —tamaños, pesos, interlineado, interletraje— sí están aplicadas, pero sobre la
> tipografía del sistema (Segoe UI en Windows). Ver
> [Pendientes](#pendientes-de-implementar).

## Maquetación y espaciado

Retícula **modular de 12 columnas** de ancho **fluido**.

- **Lógica de retícula:** canaleta (*gutter*) fija de 16px que asegura un ritmo
  vertical consistente entre widgets de datos y grupos de campos.
- **Ritmo vertical:** construido sobre una línea base de 4px. Todos los
  componentes deben tener alturas y márgenes divisibles entre 4.
- **Sin ancho máximo.** La ventana arranca maximizada; un tope de 1440px dejaría
  media pantalla en márgenes vacíos en un monitor de 2560px. El tablero usa todo
  el ancho disponible. Si algún día hace falta acotar, que sea solo en formularios
  y modales, no en el tablero.
- **Ancho mínimo de ventana: 960px** (`page.window.min_width`, ya aplicado). Es
  media pantalla en 1920, el caso angosto real al acoplar la ventana. Por debajo
  de eso un tablero de alta densidad deja de ser legible y no tiene caso
  reacomodarlo.
- **Responsividad dentro de la tarjeta, no reacomodando la retícula.** No hay
  build móvil ni de tableta: es escritorio Windows. El piso de legibilidad
  (~260px) aplica al **span de una tarjeta**, no a una columna suelta de la
  retícula. Con 12 columnas base y un span mínimo de 4, la tarjeta más angosta
  mide 309px a 960px de ventana y 838px a 2560px: **nunca baja del piso**, así
  que las 12 columnas se sostienen en todo el rango soportado y no hace falta
  cambiar de mapa de posiciones.

  Lo que sí varía es la **densidad interna de cada tarjeta**, que se mide con
  `on_size_change` y suelta elementos accesorios por orden de importancia (ver
  [ui/tarjetas.py](ui/tarjetas.py)). Si algún día se declara una tarjeta con span
  de 1–2 columnas, ahí sí habrá que introducir escalones con mapa propio.
- **Scroll solo vertical.** El ancho de celda se deriva del ancho medido, así que
  la retícula encaja exacta a lo ancho por construcción. En un tablero, tener que
  desplazarse de lado para ver un indicador lo vuelve inútil.

## Elevación y profundidad

La profundidad se transmite con **capas tonales** y **sombras ambientales**, para
crear una pila lógica de información.

- **Nivel 0 (fondo):** la base `surface`.
- **Nivel 1 (tarjetas/superficies):** `surface-container-lowest` con borde suave
  de 1px (`outline-variant`) y sombra sutil (`0px 2px 4px rgba(0,0,0,0.05)`).
- **Nivel 2 (hover/activo):** sombras elevadas (`0px 4px 8px rgba(0,0,0,0.08)`)
  para indicar interactividad.
- **Nivel 3 (modales/popovers):** sombras de mayor contraste
  (`0px 12px 24px rgba(0,0,0,0.12)`) con atenuación del fondo para enfocar la
  atención.

## Formas

El lenguaje de formas es **redondeado**, buscando el equilibrio entre cercanía
moderna y estructura profesional.

- **Elementos estándar:** botones, campos de entrada y chips usan radio de
  `0.5rem` (8px).
- **Elementos grandes:** tarjetas de datos principales y contenedores de modal
  usan `0.75rem` (12px).
- **Elementos pequeños:** tooltips y etiquetas internas usan `0.25rem` (4px).

## Componentes

- **Botones:** los primarios usan `primary-container` (azul marino) con texto
  `on-primary-container`. Los secundarios usan contorno o solo texto en
  `secondary`.
- **Tarjetas:** el contenedor principal de datos. Deben incluir un área de
  encabezado para el título y, opcionalmente, un pie para acciones. Superficie
  `surface-container-lowest` con esquinas de 12px.
- **Campos de entrada:** estilo *outlined* con borde de 1px (`outline-variant`).
  Al enfocarse, el borde pasa a `secondary` con 2px de grosor.
- **Tablas de datos:** disposición de alta densidad con filas de 44px de alto
  (divisible entre 4, cumple el ritmo vertical). Usar colores alternos (*zebra
  striping*) **solo** en tablas de más de 10 columnas.
- **Chips/insignias:** fondo `secondary` al 10% de opacidad para indicadores de
  estado (p. ej. "Pendiente", "Dado de alta"), manteniendo el texto en alto
  contraste.
- **Navegación horizontal en el encabezado** (sustituye al *navigation rail* del
  diseño original; ver [decisión 4](#decisiones-tomadas)). Fila de ítems
  ícono + texto entre el logo y los botones de acción. El ítem activo se marca
  con borde inferior de 3px en `primary` y texto en negrita; el inactivo usa
  `on-surface-variant`. El hover resalta igual que el activo, con transición de
  160ms.

---

## Cómo se aterriza en Flet

Implementado en [ui/tema.py](ui/tema.py), que es la **fuente única** de color y
tipografía. Las pantallas **no deben escribir hex sueltos**: usan los roles
(`ft.Colors.PRIMARY`, `ON_SURFACE_VARIANT`, `ERROR`, `SURFACE_CONTAINER_HIGHEST`)
y el tema resuelve el valor según el modo. Gracias a eso, aplicar la paleta no
requirió tocar ninguna pantalla.

```python
# app.py, en main()
from ui import tema
page.theme = tema.construir_tema(False, _barra)      # claro
page.dark_theme = tema.construir_tema(True, _barra)  # oscuro
```

La tipografía va en `ft.Theme(text_theme=ft.TextTheme(...))`. Correspondencias:

| Token del diseño | Rol de `ft.TextTheme` |
|---|---|
| `headline-lg` / `-md` / `-sm` | `headline_large` / `headline_medium` / `headline_small` |
| `body-lg` / `body-md` | `body_large` / `body_medium` |
| `label-lg` / `label-md` | `label_large` / `label_medium` |
| `code` | *(sin rol Material; expuesto como `tema.CODIGO`)* |

Dos conversiones de unidades que hay que respetar al editar `tema.py`: `height` en
Flet es **multiplicador** (`lineHeight / fontSize`), y `letter_spacing` va en
**píxeles**, no en `em` (los `-0.02em` a 32px son `-0.64`).

### Tokens sin destino en Flet

Material 3 deprecó tres roles y los fusionó con `surface`, así que
`ft.ColorScheme` no los tiene: **`background`**, **`on-background`** y
**`surface-variant`**. En este sistema valen lo mismo que `surface` /
`on-surface`, así que no se pierde nada.

Nota de nombre: el token `inverse-on-surface` se llama **`on_inverse_surface`** en
Flet (orden invertido).

`shadow` y `scrim` no los define el diseño; se dejan en los valores por defecto de
Material (44 de 46 roles definidos en cada esquema).

---

## Decisiones tomadas

El documento original se contradecía consigo mismo y con decisiones ya tomadas en
el proyecto. Así quedó resuelto:

1. **Los tokens mandan sobre la prosa.** Donde el texto del diseño citaba colores
   que no coincidían con los tokens, ganan los tokens: son lo que consume la
   máquina. Los cuatro casos: el "Primary `#1A237E`" de la prosa era en realidad
   `primary-container`; el acento `#2196F3` no existía en ningún token y se
   sustituyó por `secondary`/`secondary-container`; el fondo `#F5F5F5` es
   `#f9f9f9`; la superficie `#FFFFFF` es `surface-container-lowest`. El borde
   `#E0E0E0` pasó a ser `outline-variant`. La sección [Colores](#colores) ya está
   reescrita contra los tokens.
2. **Radio de tarjetas: 12px.** "Formas" decía 16px y "Componentes" 8–12px para el
   mismo elemento; se unifica en `0.75rem`.
3. **Paleta oscura derivada de la misma rampa tonal**, no inventada aparte, para
   que no desentone con el tema buscado. Verificada por contraste WCAG.
4. **Se conserva la navegación horizontal del encabezado**, descartando el *riel
   de navegación* lateral que pedía el diseño original. Motivo: un riel se lleva
   entre 72 y 88px de ancho de forma permanente, y el ancho es justo el recurso
   escaso cuando la ventana baja a los 960px mínimos. En un tablero de alta
   densidad ese espacio rinde más como contenido que como navegación. La
   navegación actual ya usa los roles `primary` y `on-surface-variant`, así que
   adoptó la paleta nueva sin cambios.
5. **Sin `container-max-width`.** Ancho fluido, mínimo de ventana 960px y
   escalones de columna por piso de legibilidad, como se detalla en
   [Maquetación](#maquetación-y-espaciado).
6. **`code` en monoespaciada, no en Inter.** Inter no es monoespaciada y el único
   lugar donde se usa (detalle técnico de la pantalla de error) necesita
   alineación por columnas.
7. **Filas de tabla: 44px**, no 40px. Es lo que ya usa
   [ui/tabla_responsiva.py](ui/tabla_responsiva.py), cumple el ritmo de 4px y
   evita un cambio de densidad sin motivo.

## Pendientes de implementar

- **Fuente Inter.** Agregar el `.ttf` al repo, registrarlo con
  `page.fonts = {"Inter": "..."}`, poner `FUENTE = "Inter"` en
  [ui/tema.py](ui/tema.py) e incluirlo en el empaquetado (`--add-data` en
  `construir.bat` y en `.github/workflows/compilar.yml`, como ya se hace con
  `Imagenes`).
- **Elevación y sombras.** Los tres niveles están especificados pero no aplicados;
  hoy las tarjetas usan el `ft.Card` por defecto.
