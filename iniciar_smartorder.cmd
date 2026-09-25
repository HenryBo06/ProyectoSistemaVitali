@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Falta el entorno local. Ejecute primero: py -m venv .venv
    echo Luego instale las dependencias: .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)
set "SMARTORDER_LOCAL_MODE=1"
".venv\Scripts\python.exe" -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501
if errorlevel 1 pause
