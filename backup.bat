@echo off
REM Backup del mundo (Windows). Delega en el nucleo multiplataforma mcadmin.py
REM (pausa el autoguardado via RCON, comprime el mundo y rota los antiguos).
cd /d "%~dp0"

set "VENV_PY=%~dp0.venv-admin\Scripts\python.exe"
if exist "%VENV_PY%" (
    "%VENV_PY%" "%~dp0mcadmin.py" --backup
) else (
    py -3 "%~dp0mcadmin.py" --backup 2>nul || python "%~dp0mcadmin.py" --backup
)
