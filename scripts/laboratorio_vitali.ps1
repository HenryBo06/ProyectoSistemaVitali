param([ValidateSet('Django','Streamlit')][string]$Interfaz = 'Django')
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $taskRoot
$taskPython = Join-Path $taskRoot '.venv-web\Scripts\python.exe'
if (!(Test-Path -LiteralPath $taskPython)) { throw 'Prepara SmartOrder con su iniciador normal antes de abrir el laboratorio.' }
& $taskPython scripts/preparar_smartorder_lab.py
if ($LASTEXITCODE -ne 0) { throw 'No se pudo preparar el laboratorio separado.' }
$taskNames = @('SMARTORDER_WEB_HOME','SMARTORDER_DB','SMARTORDER_ODOO_CONFIG','SMARTORDER_TELEGRAM_BOT_TOKEN','SMARTORDER_TELEGRAM_CHAT_ID')
$taskPrevious = @{}
foreach ($taskName in $taskNames) { $taskPrevious[$taskName] = [Environment]::GetEnvironmentVariable($taskName,'Process') }
try {
    $env:SMARTORDER_WEB_HOME = Join-Path $taskRoot '.local-web-lab'
    $env:SMARTORDER_DB = Join-Path $taskRoot '.local-web-lab\smartorder.sqlite3'
    $env:SMARTORDER_ODOO_CONFIG = Join-Path $taskRoot '.local-odoo\smartorder.json'
    $env:SMARTORDER_TELEGRAM_BOT_TOKEN = $null
    $env:SMARTORDER_TELEGRAM_CHAT_ID = $null
    Write-Host 'Laboratorio simulado. Cuentas y contraseñas: .local-web-lab\credentials.json'
    if ($Interfaz -eq 'Django') {
        & $taskPython manage.py runserver 127.0.0.1:8001 --noreload
    } else {
        & $taskPython -m streamlit run streamlit_app.py --server.address 127.0.0.1 --server.port 8502 --server.headless true
    }
} finally {
    foreach ($taskName in $taskNames) { [Environment]::SetEnvironmentVariable($taskName, $taskPrevious[$taskName], 'Process') }
}
