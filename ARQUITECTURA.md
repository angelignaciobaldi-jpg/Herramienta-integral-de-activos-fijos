# Arquitectura estándar — Herramientas de escritorio Quetzaltic / Grupo Petroil

> Documento de referencia para **estandarizar** las herramientas internas de
> escritorio (Tesorería, Activos Fijos y las que sigan). Describe la arquitectura,
> las convenciones y el modelo de distribución que comparten, para que cada
> proyecto nuevo arranque igual y sea mantenible por cualquiera del equipo.
>
> Proyectos que ya siguen este estándar: **Herramienta Integral de Tesorería**
> (base original) y **Herramienta Integral de Activos Fijos** (este repo).

---

## 1. Principios de diseño

1. **Modularidad por pantalla.** Cada pantalla vive en su propio archivo bajo
   `ui/`, expone `.contenido` (su control raíz) y no conoce a las demás. Así
   varias personas trabajan en paralelo sin conflictos y una pantalla rota no
   tumba a las otras.
2. **Backend separado de la interfaz.** Toda la lógica (BD, OCR, RPA, exportación,
   red) vive en `core/` y no importa Flet. `ui/` orquesta; `core/` hace el trabajo.
   Esto permite probar el backend sin interfaz y reusarlo entre proyectos.
3. **Reutilizable vs. dominio.** `core/` tiene dos clases de módulos: los
   **reutilizables** (idénticos entre proyectos: rutas, entorno, dpapi,
   credenciales, preferencias, auto_updater, win_*) y los **de dominio**
   (específicos: `db`, extractores, exportadores, flujos RPA concretos).
4. **Arranque a prueba de fallos.** El shell muestra un *splash* inmediato,
   importa los módulos pesados de forma **perezosa** y envuelve el arranque en
   `try/except` para mostrar una pantalla de error clara (nunca una ventana en
   blanco ni un crash del sistema).
5. **Actualizable sin fricción.** La app se instala **por usuario** (sin UAC) y se
   **auto-actualiza** desde releases privadas de GitHub. El usuario nunca
   reinstala a mano.
6. **Los secretos nunca en claro ni en el repo.** Contraseñas y tokens se cifran
   con **DPAPI** (atado a la cuenta de Windows). El repo **solo contiene código**.
7. **Offline primero.** OCR local (Tesseract) y BD local (SQLite): los documentos
   y datos sensibles no salen del equipo salvo la operación explícita del RPA.

---

## 2. Stack tecnológico

| Capa | Tecnología |
|------|------------|
| UI | **Flet** (Material, escritorio) |
| Empaquetado | **flet pack** / PyInstaller (onedir) |
| Instalador | **Inno Setup** (por usuario, sin UAC) |
| Actualización | **GitHub Releases** privadas + `core/auto_updater.py` |
| CI/CD | **GitHub Actions** (dispara al publicar un Release) |
| OCR | **Tesseract** (local) vía `pytesseract` + **PyMuPDF** |
| RPA | **Playwright** (Chromium) |
| Datos | **SQLite** (stdlib) |
| Secretos | **DPAPI** (ctypes, Windows) |
| Reportes | **openpyxl** (Excel), CSV |

Plataforma objetivo: **Windows 10/11**. Python 3.12.

---

## 3. Estructura de carpetas (plantilla)

```
<proyecto>/
├── app.py                      # Shell: ventana, tema, navegación, auto-updater
├── requirements.txt
├── construir.bat               # Build local del .exe (flet pack)
├── instalador.iss              # Inno Setup (por usuario)
├── README.md
├── ARQUITECTURA.md             # este documento
├── .env.example                # plantilla de variables de entorno (secretos)
├── .gitignore
├── .github/workflows/compilar.yml   # CI: build + instalador + release
├── scripts/smoke_import.py     # verificación de imports (la corre el CI)
├── Imagenes/                   # logos + icon.ico/icon.svg
├── core/                       # backend (sin Flet)
│   ├── rutas.py                # [R] BUNDLE / DATOS / INSTALL
│   ├── version.py              # [R] versión (fuente única; el CI la sincroniza)
│   ├── entorno.py              # [R] lectura de .env / variables de entorno
│   ├── dpapi.py                # [R] cifrado local atado al usuario de Windows
│   ├── credenciales.py         # [R] credenciales del SIPP (cifradas)
│   ├── ajustes_api.py          # [R] config API (URL en claro, token cifrado)
│   ├── api.py                  # [R] cliente HTTP mínimo de microservicios
│   ├── preferencias.py         # [R] preferencias por máquina (JSON)
│   ├── auto_updater.py         # [R] actualización desde releases de GitHub
│   ├── win_titlebar.py         # [R] color de la barra de título (Win 11)
│   ├── win_taskbar.py          # [R] identidad en la barra de tareas (AUMID)
│   ├── ocr.py                  # [R] extracción de texto (PDF/imagen + Tesseract)
│   ├── rpa_sipp.py             # [R/D] sesión base del SIPP + flujos de dominio
│   ├── empresas.py             # [R] catálogo Grupo Petroil (id + nombre)
│   ├── db.py                   # [D] persistencia del dominio (SQLite)
│   └── exportador.py           # [D] exportación (Excel/CSV/PDF)
└── ui/
    ├── comun.py                # constantes, colores, helpers, catálogo empresas
    ├── configuracion.py        # modal de Configuración (credenciales SIPP + API)
    └── <pantalla>.py           # una por módulo funcional
```

`[R]` = reutilizable tal cual entre proyectos · `[D]` = de dominio (se adapta).

---

## 4. El shell (`app.py`)

Responsabilidades y patrón fijo:

- **Splash inmediato** (`_pantalla_cargando`) antes de cualquier trabajo pesado,
  con `await asyncio.sleep(0.05)` para que el cliente alcance a pintarlo.
- **Imports perezosos**: solo `flet` y `core.rutas` al tope; el resto se importa
  dentro de funciones para que un módulo roto no impida arrancar el updater.
- **Encabezado**: logo (claro/oscuro), navegación propia (no `Tabs` de Material;
  se alterna `visible` de cada `.contenido`), botones de actualizar/config/tema.
- **Tema y ventana persistentes** en `preferencias.json` (claro/oscuro, tamaño,
  posición, maximizado).
- **Servicios compartidos** que el shell inyecta a cada pantalla vía `self`
  (la instancia `app`): `page`, `picker` (FilePicker traído al frente), `avisar()`
  (snackbar), `abrir_en_sistema()`.
- **Auto-updater en 2.º plano**: arranca de inmediato en la versión instalada y
  revisa nuevas versiones después, sin frenar; el usuario decide cuándo aplicar.
- **Contrato de una pantalla**:
  ```python
  class SeccionX:
      def __init__(self, app): ...      # recibe el shell (app)
      self.contenido: ft.Control        # lo que el shell muestra
      def _on_resize(self, e): ...       # opcional (el shell lo registra si existe)
      def cargar_desde_db(self): ...     # opcional (datos iniciales)
  ```

---

## 5. Rutas: desarrollo vs. empaquetado (`core/rutas.py`)

Tres ubicaciones, resueltas según `sys.frozen`:

- **BUNDLE** — assets de solo lectura (Imagenes, tessdata). Empaquetado:
  `sys._MEIPASS`.
- **INSTALL** — carpeta del `.exe` instalado (solo lectura en uso normal).
- **DATOS** — datos escribibles del usuario. Empaquetado:
  `%LOCALAPPDATA%\Quetzaltic Solutions\Herramientas de <App>`. **Nunca** escribas
  junto al `.exe` (suele estar en Archivos de Programa, solo lectura).

En desarrollo las tres apuntan a la carpeta del proyecto. **Todo lo que se
escribe en runtime** (BD, preferencias, credenciales, token, navegador, estado
del updater) va a `DATOS`.

---

## 6. Seguridad y secretos

- **Contraseñas y tokens**: `core/dpapi.py` (CryptProtectData). Cifrado atado a la
  cuenta de Windows; ni la app maneja llaves ni el secreto viaja en claro.
  - Credenciales del SIPP → `credenciales_rpa.json` (`core/credenciales.py`).
  - Token de API → `token_api.json` (`core/ajustes_api.py`).
- **PAT de GitHub** (para el updater): variable de entorno
  `QUETZALTIC_GITHUB_PAT` (sistema o `.env` junto al `.exe`). Token de **mínimo
  alcance** (solo lectura del repo). Se comparte entre herramientas Quetzaltic.
- **URL de API**: no es secreta → preferencia en claro / variable de entorno.
- **`.gitignore`**: excluye `*.db`, credenciales, tokens, `.env`, diagnósticos del
  RPA, tessdata, `ms-playwright/`, salidas de build y datos de prueba reales.

---

## 7. OCR (`core/ocr.py`)

Estrategia híbrida, 100% reutilizable:

- PDF con capa de texto legible → se lee directo (rápido y exacto).
- PDF escaneado / página sin texto / codificación rota → rasteriza (PyMuPDF) +
  Tesseract.
- Imagen → Tesseract directo.

Detalles clave: Tesseract se localiza primero **empaquetado** (`{app}\Tesseract-OCR`)
y luego en el sistema; el idioma se resuelve a `spa+eng` si existe el modelo;
las llamadas se lanzan como subproceso propio **cancelable** y **sin ventana de
consola**. El motor es local: los documentos no salen del equipo.

---

## 8. RPA del SIPP (`core/rpa_sipp.py`)

`SesionSipp` encapsula lo **común**: ciclo de vida del navegador (Playwright),
`login`, `seleccionar_empresa_sucursal` (selects "chosen" de AngularJS operados por
su `ng-model`), `elegir_en_menu`, navegación SPA y captura de diagnósticos
(`_diagnostico_rpa/`) cuando un localizador falla.

Convenciones:

- **Localizadores orientados al usuario** (`get_by_role`/`get_by_placeholder`/
  texto) con respaldos por CSS/`ng-model`.
- **Chromium no se empaqueta**; se descarga la 1.ª vez a `DATOS\ms-playwright`
  (`asegurar_navegador`). El driver (node) sí va empaquetado (`--collect-all`).
- Cada módulo que opere el SIPP **reutiliza** `SesionSipp` y agrega ARRIBA sus
  flujos concretos, en vez de duplicar la automatización.
- Las **páginas HTML de referencia** del portal (DOM real) se guardan localmente
  para afinar selectores, pero **no se versionan** (datos reales).

---

## 9. Datos (`core/db.py`)

SQLite sin servidor en `DATOS`. Patrón:

- Una `@dataclass` por entidad; una **clave única de negocio** (CLABE en
  Tesorería, núm. de inventario en Activos Fijos) con excepción de duplicado.
- `inicializar()` crea la tabla **y aplica migraciones incrementales** (agrega
  columnas nuevas con `ALTER TABLE` sin romper bases existentes).
- Lista de columnas como **fuente única** para INSERT/UPDATE y migraciones.

---

## 10. Actualización automática (`core/auto_updater.py` + CI)

Flujo end-to-end:

1. **Desarrollo** → merge a `main`.
2. **Publicar Release** en GitHub con un tag **mayor** (`0.2.0` / `v0.2.0`).
3. El **CI** (`compilar.yml`) sincroniza `version.py` y `AppVersion` con el tag,
   corre el **smoke test de imports**, compila (`flet pack`), empaqueta Tesseract,
   arma el instalador (Inno Setup) y sube `Instalador_<App>.exe` como asset.
4. Cada app instalada consulta `releases/latest`, compara versión, descarga el
   asset privado (con el PAT) y lo aplica **en silencio**, reiniciándose sola.

Salvaguardas: **el tag ES la versión** (el CI la reescribe, evita bucles de
actualización); guard anti-bucle por `ultimo_tag_aplicado`; instalación **por
usuario** (`PrivilegesRequired=lowest`) → sin UAC; `AppId` **fijo** por app para
que la actualización sobrescriba en sitio.

**Constantes que cambian por proyecto** (mantenerlas consistentes entre sí):

| Dónde | Constante | Ejemplo (Activos Fijos) |
|-------|-----------|--------------------------|
| `auto_updater.py` | `OWNER` / `REPO` | `angelignaciobaldi-jpg` / `Herramienta-integral-de-activos-fijos` |
| `auto_updater.py` | `NOMBRE_ASSET` | `Instalador_ActivosFijos.exe` |
| `instalador.iss` | `OutputBaseFilename` | `Instalador_ActivosFijos` (== NOMBRE_ASSET) |
| `instalador.iss` | `AppId` | GUID **único y fijo** por app |
| `instalador.iss` / `win_taskbar.py` | `AppUserModelID` / `AUMID` | `QuetzalticSolutions.HerramientasActivosFijos` |
| `construir.bat` / `compilar.yml` | nombre `-n` del build | `ActivosFijos` (== `.exe` y ruta `dist\`) |
| `rutas.py` | `_SUBCARPETA_DATOS` | `Quetzaltic Solutions\Herramientas de Activos Fijos` |
| `db.py` | nombre del `.db` | `activos_fijos.db` |

---

## 11. Cómo arrancar un proyecto nuevo con este estándar

1. **Copiar la base** (este repo) y renombrar. Vaciar/adaptar los módulos `[D]`.
2. **Crear el repo** privado en GitHub y apuntar `OWNER`/`REPO` en `auto_updater.py`.
3. **Generar un `AppId` GUID nuevo** (`python -c "import uuid;print(uuid.uuid4())"`)
   y actualizar la tabla de constantes de la sección 10 en todos los archivos.
4. **Definir el esquema** en `db.py` (entidad + clave única) con la sección de
   migraciones lista.
5. **Crear una pantalla por módulo** en `ui/`, siguiendo el contrato (sección 4).
   Empezar con `comun.placeholder(...)` y rellenar la lógica después.
6. **Registrar las pantallas** en `app.py` (`_construir` + `_construir_nav`).
7. **Configurar el PAT** `QUETZALTIC_GITHUB_PAT` en la máquina / `.env`.
8. **Verificar**: `python scripts/smoke_import.py` y `python app.py`.
9. **Publicar**: primer Release con tag `0.1.0` → el CI genera el instalador.

### Checklist de "listo para publicar"

- [ ] `python scripts/smoke_import.py` pasa (lo exige el CI).
- [ ] `AppId`, `NOMBRE_ASSET`/`OutputBaseFilename`, `AUMID` y nombre del build
      coinciden entre `auto_updater.py`, `instalador.iss`, `win_taskbar.py`,
      `construir.bat` y `compilar.yml`.
- [ ] `.gitignore` cubre `*.db`, credenciales, tokens, `.env`, diagnósticos y
      datos de prueba reales.
- [ ] Secretos por DPAPI / variable de entorno; **ninguno** en el código.
- [ ] Logos e `icon.ico` presentes en `Imagenes/`.
- [ ] Tag del Release **mayor** que el anterior y Release **nuevo** (no editar uno
      viejo: no dispara el build).

---

## 12. Convenciones de código

- **Idioma**: código, comentarios y UI en **español**. Nombres descriptivos.
- **Comentarios que explican el *porqué*** (no el *qué*), especialmente en los
  *workarounds* de Windows/DWM, Playwright y el updater.
- **`from __future__ import annotations`** al tope de cada módulo.
- **Errores best-effort** en lo no crítico (color de barra, taskbar, persistencia
  de ventana): `try/except` que nunca tumba el arranque; lo crítico se reporta al
  usuario con `avisar()` o la pantalla de error.
- **Trabajo pesado fuera del hilo de UI**: `asyncio.to_thread(...)` (OCR, red,
  descargas) para no congelar la interfaz.
