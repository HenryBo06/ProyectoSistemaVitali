param([ValidateSet('Django','Streamlit','Sincronizar')][string]$Interfaz = 'Django')
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
$taskHome = Join-Path $taskRoot '.local-web-pilot'
$taskPython = Join-Path $taskRoot '.venv-web\Scripts\python.exe'
Set-Location -LiteralPath $taskRoot
$taskConfig = Get-Content -LiteralPath (Join-Path $taskHome 'postgres.json') -Raw | ConvertFrom-Json
$env:SMARTORDER_WEB_HOME = $taskHome
$env:SMARTORDER_DATABASE_URL = $taskConfig.url
$env:SMARTORDER_MEDIA_ROOT = Join-Path $taskHome 'uploads'
$env:SMARTORDER_ODOO_CONFIG = Join-Path $taskRoot '.local-odoo\smartorder.json'
if ($Interfaz -eq 'Django') { & $taskPython manage.py runserver 127.0.0.1:8003 --noreload }
elseif ($Interfaz -eq 'Streamlit') { & $taskPython -m streamlit run streamlit_app.py --server.address 127.0.0.1 --server.port 8503 --server.headless true --server.fileWatcherType none }
else { & $taskPython manage.py sync_odoo_sources --admin lab_admin_01 --loop }
