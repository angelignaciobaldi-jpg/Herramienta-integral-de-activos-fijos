@echo off
rem ============================================================
rem  Construye el ejecutable (dist\ActivosFijos\ActivosFijos.exe),
rem  listo para meterlo en el instalador (instalador.iss con Inno Setup).
rem
rem  NO empaqueta Chromium: el navegador se descarga en la primera
rem  ejecucion del RPA (a %LOCALAPPDATA%\...). Asi el instalador es
rem  liviano. El driver de Playwright (node) si va incluido via
rem  --collect-all, para poder lanzar/descargar el navegador.
rem
rem  Ejecutar dentro del entorno virtual (.venv activado), desde
rem  la carpeta del proyecto.
rem ============================================================
setlocal
cd /d "%~dp0"

echo Empaquetando con flet pack ...
set DATAARGS=
if exist "Imagenes\" set DATAARGS=%DATAARGS% --add-data "Imagenes:Imagenes"

flet pack app.py -n "ActivosFijos" -D ^
  --icon "Imagenes\icon.ico" ^
  %DATAARGS% ^
  --hidden-import openpyxl ^
  --pyinstaller-build-args="--collect-all=playwright" ^
  -y
if errorlevel 1 (
  echo *** Fallo el empaquetado. ***
  pause & exit /b 1
)

echo.
echo ============================================================
echo   Listo: dist\ActivosFijos\ActivosFijos.exe
echo   Siguiente: compila instalador.iss con Inno Setup (iscc).
echo ============================================================
pause
