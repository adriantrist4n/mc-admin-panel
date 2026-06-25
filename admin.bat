@echo off
REM Panel de administracion (Windows). Equivalente de admin.sh: prepara el venv
REM con rich + psutil la primera vez y abre el dashboard en vivo (dashboard.py).
cd /d "%~dp0"

set "VENV_PY=%~dp0.venv-admin\Scripts\python.exe"

REM ¿El venv ya tiene las dependencias? Entonces arranca directamente.
if exist "%VENV_PY%" (
    "%VENV_PY%" -c "import rich, psutil" >nul 2>&1
    if not errorlevel 1 goto run
)

REM Elegir un Python 3 base para crear el venv (primero el launcher 'py').
set "BASEPY="
py -3 --version >nul 2>&1 && set "BASEPY=py -3"
if not defined BASEPY python --version >nul 2>&1 && set "BASEPY=python"
if not defined BASEPY (
    echo No se encontro Python 3. Instalalo desde https://www.python.org/downloads/
    echo y asegurate de marcar "Add Python to PATH". Luego vuelve a ejecutar admin.bat.
    pause
    exit /b 1
)

echo Instalando dependencias del panel (rich, psutil)... Solo ocurre la primera vez.
%BASEPY% -m venv "%~dp0.venv-admin"
"%VENV_PY%" -m pip install --quiet --upgrade pip >nul 2>&1
"%VENV_PY%" -m pip install --quiet rich psutil

"%VENV_PY%" -c "import rich, psutil" >nul 2>&1
if errorlevel 1 (
    echo No se pudieron instalar las dependencias del panel.
    echo Revisa tu conexion a internet y que pip funcione, y vuelve a intentarlo.
    pause
    exit /b 1
)

:run
"%VENV_PY%" "%~dp0dashboard.py"
