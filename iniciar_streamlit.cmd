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
".venv-web\Scripts\python.exe" -m pip install -r requirements-streamlit.txt
if errorlevel 1 goto :error
".venv-web\Scripts\python.exe" manage.py migrate --noinput
if errorlevel 1 goto :error
if exist ".local\smartorder.sqlite3" (
    ".venv-web\Scripts\python.exe" manage.py import_legacy_local
    if errorlevel 1 goto :error
)
set SMARTORDER_ALLOW_LOCAL_SETUP=1
if not defined SMARTORDER_STREAMLIT_PORT set SMARTORDER_STREAMLIT_PORT=8501
set SMARTORDER_STREAMLIT_URL=http://127.0.0.1:%SMARTORDER_STREAMLIT_PORT%/
echo.
echo SmartOrder AI: %SMARTORDER_STREAMLIT_URL%
echo El servidor funciona mientras esta ventana este abierta. Ctrl+C para detenerlo.
".venv-web\Scripts\python.exe" -m streamlit run streamlit_app.py --server.address 127.0.0.1 --server.port %SMARTORDER_STREAMLIT_PORT% --server.headless true
exit /b %errorlevel%
:error
echo No se pudo iniciar Streamlit. Revise el mensaje anterior.
pause
exit /b 1
