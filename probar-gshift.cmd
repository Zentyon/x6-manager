@echo off
rem Prueba el G-Shift con el perfil guardado, sin escribir en la memoria del mouse.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
echo Abriendo la ventana de prueba... (cierra la ventana para terminar)
py tools\test_gshift.py --profile gshift-test --seconds 600
echo.
echo Prueba terminada. Log: %TEMP%\x6-gshift-test.log
pause
