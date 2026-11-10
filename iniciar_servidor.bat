@echo off
rem Cafe Personal: arranque minimizado. Para depurar: iniciar_servidor.bat min
setlocal
if /i "%~1"=="min" goto principal
start "Cafe Personal" /min cmd /c ""%~f0" min"
exit /b 0

:principal
cd /d "%~dp0"
title Cafe Personal - cierra esta ventana para apagar
if not defined CAFE_PORT set "CAFE_PORT=31847"
set "BANDERA=%TEMP%\cafe_personal_fallo.flag"
del "%BANDERA%" >nul 2>nul

rem Ya encendida: solo abre el navegador.
curl -fs -o nul --max-time 1 "http://127.0.0.1:%CAFE_PORT%/salud" >nul 2>nul
if not errorlevel 1 goto abrir_ya

if exist "venv\Scripts\python.exe" goto hay_venv
set "MENSAJE=No se encontro el entorno virtual en la carpeta venv. Crealo siguiendo la seccion Instalacion del README."
goto fallo

:hay_venv
rem Se busca la direccion remota 0.0.0.0:0 porque el texto LISTENING cambia con el idioma de Windows.
netstat -an -p tcp | findstr /r /c:":%CAFE_PORT%  *0\.0\.0\.0:0" >nul
if errorlevel 1 goto arrancar
set "MENSAJE=El puerto %CAFE_PORT% esta ocupado por otro programa. Cierra ese programa o define CAFE_PORT con otro numero."
goto fallo

:arrancar
start "Abriendo Cafe Personal" /min cmd /c ""%~dp0_abrir_navegador.bat" %CAFE_PORT%"
"venv\Scripts\python.exe" app.py
set "CODIGO=%ERRORLEVEL%"
if "%CODIGO%"=="0" exit /b 0
if "%CODIGO%"=="-1073741510" exit /b 0
set "MENSAJE=El servidor no pudo iniciar o se detuvo, codigo %CODIGO%. Puede ser un problema con la base de datos. Para ver el detalle ejecuta iniciar_servidor.bat min desde una consola en la carpeta del proyecto."
goto fallo

:abrir_ya
start "" "http://localhost:%CAFE_PORT%"
exit /b 0

:fallo
echo %MENSAJE%
echo.> "%BANDERA%"
powershell -NoProfile -Command "Add-Type -AssemblyName PresentationFramework; [void][System.Windows.MessageBox]::Show($env:MENSAJE, 'Cafe Personal', 'OK', 'Error')"
exit /b 1