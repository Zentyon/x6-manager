@echo off
rem Captura lo que el software oficial escribe en la memoria del mouse.
rem Usa una COPIA instrumentada en %%TEMP%%\x6-capture (no toca la instalacion real).
setlocal
cd /d "%~dp0"

:wait
tasklist /fi "imagename eq X6.exe" 2>nul | find /i "X6.exe" >nul
if not errorlevel 1 (
    echo.
    echo   Cierra el software oficial: icono de la bandeja -^> clic derecho -^> Salir.
    echo   Esperando 3 segundos...
    timeout /t 3 >nul
    goto wait
)

del hid_capture.log 2>nul
echo.
echo   Abriendo la COPIA instrumentada...
echo   Haz UN cambio, pulsa Aplicar, espera 3 s, y repite con otro.
start "" /wait "%~dp0X6.exe"
echo.
echo   Copia cerrada. Log: %~dp0hid_capture.log
pause
