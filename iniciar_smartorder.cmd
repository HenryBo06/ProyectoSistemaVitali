@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv-web\Scripts\python.exe" (
    where py >nul 2>nul
    if errorlevel 1 (
        python -m venv .venv-web
    ) else (
        py -3 -m venv .venv-web
    )
    if errorlevel 1 goto :error
)
".venv-web\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :error
".venv-web\Scripts\python.exe" manage.py migrate --noinput
if errorlevel 1 goto :error
if exist ".local\smartorder.sqlite3" (
    ".venv-web\Scripts\python.exe" manage.py import_legacy_local
    if errorlevel 1 goto :error
)
rem ponytail: two local ports; set SMARTORDER_PORT when both are unavailable.
if not defined SMARTORDER_PORT (
    ".venv-web\Scripts\python.exe" -c "import socket; s=socket.socket(); s.bind(('127.0.0.1',8000)); s.close()" >nul 2>nul
    if errorlevel 1 (
        set SMARTORDER_PORT=8050
    ) else (
        set SMARTORDER_PORT=8000
    )
)
echo.
echo SmartOrder AI local: http://127.0.0.1:%SMARTORDER_PORT%/
echo Para detener el servidor, presione Ctrl+C.
echo.
".venv-web\Scripts\python.exe" manage.py runserver "127.0.0.1:%SMARTORDER_PORT%" --noreload
exit /b %errorlevel%
:error
echo.
echo No se pudo iniciar SmartOrder AI. Revise el mensaje anterior.
pause
exit /b 1
