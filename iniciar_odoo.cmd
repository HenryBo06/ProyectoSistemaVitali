@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -File "%~dp0scripts\odoo_local.ps1" -Accion Iniciar
if errorlevel 1 pause
