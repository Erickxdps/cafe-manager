@echo off
rem Espera a que el servidor responda y abre el navegador. Lo lanza iniciar_servidor.bat
setlocal EnableDelayedExpansion
set "PUERTO=%~1"
if "%PUERTO%"=="" set "PUERTO=31847"
set "BANDERA=%TEMP%\cafe_personal_fallo.flag"

where curl >nul 2>nul
if errorlevel 1 goto sin_curl

for /l %%i in (1,1,60) do (
  curl -fs -o nul --max-time 1 "http://127.0.0.1:!PUERTO!/salud" >nul 2>nul
  if not errorlevel 1 goto abrir
  if exist "%BANDERA%" exit /b 1
  ping -n 2 127.0.0.1 >nul
)
if exist "%BANDERA%" exit /b 1
set "MENSAJE=El servidor no respondio en 60 segundos. Revisa la ventana minimizada Cafe Personal en la barra de tareas."
powershell -NoProfile -Command "Add-Type -AssemblyName PresentationFramework; [void][System.Windows.MessageBox]::Show($env:MENSAJE, 'Cafe Personal', 'OK', 'Error')"
exit /b 1

:sin_curl
ping -n 9 127.0.0.1 >nul
if exist "%BANDERA%" exit /b 1

:abrir
start "" "http://localhost:%PUERTO%"
exit /b 0