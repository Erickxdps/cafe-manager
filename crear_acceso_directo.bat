@echo off
rem Crea en el Escritorio el acceso directo con icono. Se ejecuta una sola vez.
setlocal
cd /d "%~dp0"
powershell -NoProfile -Command "$p=(Get-Location).Path; $s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) ('Caf'+[char]233+' Personal.lnk'))); $s.TargetPath=(Join-Path $p 'iniciar_servidor.bat'); $s.WorkingDirectory=$p; $s.IconLocation=(Join-Path $p 'cafe\static\img\cafe.ico')+',0'; $s.WindowStyle=7; $s.Description='Abrir Cafe Personal'; $s.Save()"
if errorlevel 1 goto fallo
echo Acceso directo creado en el Escritorio.
pause
exit /b 0

:fallo
powershell -NoProfile -Command "Add-Type -AssemblyName PresentationFramework; [void][System.Windows.MessageBox]::Show('No se pudo crear el acceso directo.', 'Cafe Personal', 'OK', 'Error')"
exit /b 1