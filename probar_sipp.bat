@echo off
rem Arranca la app contra un SIPP que NO es el productivo, con datos aparte.
rem
rem Dos variables hacen todo el trabajo (ver core/rpa_sipp.py y core/rutas.py):
rem   SIPP_BASE_URL        portal al que entra el RPA (login, catalogo, altas).
rem   ACTIVOS_FIJOS_DATOS  carpeta de base, preferencias, credenciales y cache.
rem
rem La carpeta aparte NO es un lujo: la cache de activos no guarda de que entorno
rem vino cada fila, asi que bajar el catalogo de pruebas sobre los datos de
rem siempre lo mezclaria con el productivo y la busqueda del levantamiento
rem dejaria de significar nada.
rem
rem Uso:  probar_sipp.bat [entorno]      entorno = stage (por omision) | test | dev
setlocal
rem Se trabaja desde la carpeta del .bat: asi funciona igual con doble clic
rem que invocado desde otra ruta (app.py se busca aqui, no en el cwd).
cd /d "%~dp0"
set ENTORNO=%1
if "%ENTORNO%"=="" set ENTORNO=stage

set SIPP_BASE_URL=https://%ENTORNO%.sipp.petroil.dev
set ACTIVOS_FIJOS_DATOS=%~dp0datos_%ENTORNO%

echo Portal : %SIPP_BASE_URL%
echo Datos  : %ACTIVOS_FIJOS_DATOS%
echo.
echo La primera vez hay que capturar en la app las credenciales del SIPP de este
echo entorno (boton de configuracion): la carpeta de datos empieza vacia.
echo.

rem El venv vive fuera del repo (el limite de 260 caracteres de Windows no deja
rem tenerlo dentro); si no esta donde se espera, se usa el python del PATH.
set PY=C:\venvs\activos-fijos\Scripts\python.exe
if not exist "%PY%" set PY=python
"%PY%" app.py
endlocal
