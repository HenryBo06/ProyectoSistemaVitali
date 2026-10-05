@echo off
cd /d "%~dp0"
echo Laboratorio Vitali con datos simulados
echo 1. Django - puerto 8001
echo 2. Streamlit - puerto 8502
echo 3. Odoo Community - puerto 8079
choice /c 123 /n /m "Elige una interfaz: "
if errorlevel 3 goto odoo
if errorlevel 2 goto streamlit
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\laboratorio_vitali.ps1 -Interfaz Django
goto end
:streamlit
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\laboratorio_vitali.ps1 -Interfaz Streamlit
goto end
:odoo
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\odoo_local.ps1 -Accion Iniciar
:end
pause
