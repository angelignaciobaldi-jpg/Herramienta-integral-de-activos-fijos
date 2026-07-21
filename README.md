# Herramienta Integral de Activos Fijos

Aplicación de escritorio (Flet) para el área de **Activos Fijos** del Grupo
Petroil. Base construida a partir de la *Herramienta Integral de Tesorería*,
reutilizando su misma arquitectura (shell modular, OCR, RPA del SIPP, BD local,
actualización automática e instalador por usuario).

## Módulos (pantallas)

- **Registro de activos** — captura/edición de activos fijos con tabla y
  persistencia en SQLite (`core/db.py`).
- **Extracción de documentos** — lee facturas/resguardos (PDF/imagen) con OCR
  (`core/ocr.py`) y muestra el texto extraído.
- **Automatización SIPP** — RPA (Playwright) que inicia sesión en el SIPP y
  selecciona empresa/sucursal (`core/rpa_sipp.py`), base para flujos concretos.
- **Exportación / Reportes** — exporta los activos a Excel/CSV (`core/exportador.py`).

Las credenciales del SIPP y la configuración de la API se capturan en el menú
**Configuración (⚙)** de la barra superior.

> **Estado:** andamiaje inicial. Las pantallas ya funcionan de extremo a extremo
> (navegación, BD, OCR, RPA base, exportación); la lógica fina de cada módulo
> (extractores de dominio, flujos SIPP específicos, plantillas de reporte) se
> completa sobre esta base.

---

## Para desarrollar

```
pip install -r requirements.txt
python -m playwright install chromium   # navegador para el RPA
python app.py
```

Se recomienda tener **Tesseract OCR** instalado en desarrollo
(`C:\Program Files\Tesseract-OCR`) con el idioma español (`spa`). En la app
instalada, el CI lo empaqueta y no hace falta instalarlo aparte.

### Estructura

```
app.py                      Shell: ventana, logo, tema, navegación
core/                       Backend (reutilizable + dominio)
  rutas.py                  BUNDLE / DATOS / INSTALL (dev vs empaquetado)
  version.py                Versión (fuente única; el CI la sincroniza con el tag)
  entorno.py                Lectura de secretos (.env / variables de entorno)
  dpapi.py                  Cifrado local atado al usuario de Windows (DPAPI)
  credenciales.py           Credenciales del SIPP (contraseña cifrada)
  ajustes_api.py / api.py   Configuración y cliente de los microservicios
  preferencias.py           Preferencias por máquina (tema, ventana, filtros)
  auto_updater.py           Actualización automática desde releases de GitHub
  win_titlebar.py           Color de la barra de título (Windows 11)
  win_taskbar.py            Identidad en la barra de tareas (AppUserModelID)
  db.py                     Persistencia de activos (SQLite)
  ocr.py                    Extracción de texto (PDF/imagen + Tesseract)
  rpa_sipp.py               Sesión base del SIPP (Playwright)
  empresas.py               Catálogo del Grupo Petroil (id + nombre)
  exportador.py             Exportación a Excel/CSV
ui/
  comun.py                  Constantes y utilidades compartidas
  configuracion.py          Modal de Configuración (credenciales SIPP + API)
  registro_activos.py       Pantalla "Registro de activos"
  extraccion_documentos.py  Pantalla "Extracción de documentos" (OCR)
  automatizacion_sipp.py    Pantalla "Automatización SIPP" (RPA)
  exportacion.py            Pantalla "Exportación / Reportes"
scripts/smoke_import.py     Verificación de imports (la corre el CI)
```

---

## Instalar en una máquina nueva (Instalador + actualización automática)

La app se distribuye como **instalador de Windows** (`Instalador_ActivosFijos.exe`,
generado con Inno Setup) y **se actualiza sola** desde las *releases* de GitHub
(`core/auto_updater.py`).

1. Ejecuta **`Instalador_ActivosFijos.exe`**. Se instala **por usuario** en
   `%LOCALAPPDATA%\Programs\...`, así que **NO pide permisos de administrador**.
2. Abre **«Herramientas Activos Fijos»** desde el menú Inicio o el acceso directo.

El AutoUpdater necesita el PAT del repo privado en la variable de entorno
`QUETZALTIC_GITHUB_PAT` (o un archivo `.env` junto al `.exe`; ver `.env.example`).

### Datos que viven en cada máquina (NO están en el repo)

Por seguridad, el repositorio **solo contiene código**. Estos archivos se generan
en la carpeta de datos del usuario (`%LOCALAPPDATA%\Quetzaltic Solutions\...`):
`activos_fijos.db`, `preferencias.json`, `credenciales_rpa.json`, `token_api.json`
y el navegador de Playwright.

---

## Publicar una nueva versión (automático vía GitHub Actions)

El pipeline `.github/workflows/compilar.yml` se dispara al **publicar un Release** y
hace TODO: sincroniza la versión con el tag, compila la app, empaqueta Tesseract,
arma el instalador con Inno Setup y sube `Instalador_ActivosFijos.exe` como asset.
Los demás equipos lo detectan y se actualizan solos.

1. Mergea tu código a `main`.
2. En GitHub: **Releases → Draft a new release** → crea el tag con la nueva versión
   (`0.2.0` o `v0.2.0`, **mayor** que el anterior) → **Publish**.
3. Listo. El CI escribe ese tag en `core/version.py` y en `AppVersion`
   (`instalador.iss`) **antes de compilar**, así que el instalador siempre reporta
   exactamente su tag (por eso no hay bucles de actualización).

### Compilar el instalable localmente (opcional, para probar)

1. `construir.bat` → genera `dist\ActivosFijos\ActivosFijos.exe` (onedir; incluye
   el driver de Playwright vía `--collect-all`; **no** empaqueta Chromium).
2. `iscc instalador.iss` → genera `Output\Instalador_ActivosFijos.exe`.

> **RPA (Playwright):** el `.exe` NO trae Chromium (para no inflar el instalador).
> La primera vez que se usa el RPA, `core/rpa_sipp.py` descarga Chromium a
> `%LOCALAPPDATA%\Quetzaltic Solutions\Herramientas de Activos Fijos\ms-playwright`
> (requiere internet). Las siguientes veces ya está.

---

Ver **`ARQUITECTURA.md`** para el estándar de arquitectura que comparten estas
herramientas y la guía para arrancar futuros proyectos con la misma base.
