@echo off
REM Ejecuta la suite de tests Python del toolkit en Windows. (test_lib.sh es
REM solo para Linux: prueba los ayudantes en bash; en Windows el nucleo
REM equivalente se cubre con test_mcadmin.py.)
setlocal
cd /d "%~dp0.."

set "PY=%~dp0..\.venv-admin\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

set FAIL=0
for %%t in (test_mcconfig.py test_mcadmin.py test_dashboard.py test_idle_monitor.py test_rcon.py) do (
    echo.
    echo ########## %%t ##########
    "%PY%" "%~dp0%%t" -v || set FAIL=1
)

echo.
if "%FAIL%"=="0" (
    echo Todos los tests pasaron.
) else (
    echo Algun test fallo.
)
exit /b %FAIL%
