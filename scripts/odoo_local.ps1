param([ValidateSet('Iniciar', 'Detener', 'Respaldar')][string]$Accion = 'Iniciar')
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
$taskLab = Join-Path $taskRoot '.local-odoo'
$taskPg = 'C:\Program Files\PostgreSQL\18\bin'
$taskPython = Join-Path $taskLab 'venv\Scripts\python.exe'
$taskConfig = Join-Path $taskLab 'odoo.conf'
$taskSource = Join-Path $taskLab 'source\odoo-bin'
if (!(Test-Path -LiteralPath $taskConfig)) { throw 'El laboratorio no está preparado en este equipo.' }
Set-Location -LiteralPath $taskRoot
$taskListener = Get-NetTCPConnection -State Listen -LocalPort 8079 -ErrorAction SilentlyContinue
if ($Accion -eq 'Iniciar') {
    & "$taskPg\pg_ctl.exe" -D "$taskLab\postgres-data" status *> $null
    if ($LASTEXITCODE -ne 0) {
        & "$taskPg\pg_ctl.exe" -D "$taskLab\postgres-data" -l "$taskLab\postgres.log" -o '-p 55432 -h 127.0.0.1' -w start
        if ($LASTEXITCODE -ne 0) { throw 'No se pudo iniciar la base del laboratorio.' }
    }
    if ($taskListener) {
        foreach ($taskSocket in $taskListener) {
            $taskProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($taskSocket.OwningProcess)"
            if (!$taskProcess.CommandLine.Contains('odoo-bin') -or !$taskProcess.CommandLine.Contains($taskConfig)) {
                throw 'El puerto 8079 pertenece a otro proceso.'
            }
        }
        Write-Host 'Odoo ya está iniciado: http://127.0.0.1:8079'; exit 0
    }
    Write-Host 'Odoo Vitali: http://127.0.0.1:8079. Ctrl+C detiene la aplicación.'
    & $taskPython $taskSource -c $taskConfig -d vitali_lab
    exit $LASTEXITCODE
}
if ($Accion -eq 'Detener') {
    foreach ($taskSocket in $taskListener) {
        $taskProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($taskSocket.OwningProcess)"
        if ($taskProcess.CommandLine.Contains('odoo-bin') -and $taskProcess.CommandLine.Contains($taskConfig)) {
            Stop-Process -Id $taskProcess.ProcessId
        } else { throw 'El puerto 8079 pertenece a otro proceso; no se detuvo.' }
    }
    & "$taskPg\pg_ctl.exe" -D "$taskLab\postgres-data" -m fast -w stop
    exit $LASTEXITCODE
}
if ($taskListener) { throw 'Detén Odoo antes de respaldar para conservar base y adjuntos del mismo momento.' }
& "$taskPg\pg_ctl.exe" -D "$taskLab\postgres-data" status *> $null
if ($LASTEXITCODE -ne 0) {
    & "$taskPg\pg_ctl.exe" -D "$taskLab\postgres-data" -l "$taskLab\postgres.log" -o '-p 55432 -h 127.0.0.1' -w start
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo iniciar la base para respaldar.' }
}
$taskSecrets = Get-Content -LiteralPath "$taskLab\credentials.json" -Raw | ConvertFrom-Json
$taskBackup = Join-Path $taskLab ('backups\' + (Get-Date -Format 'yyyyMMdd-HHmmss-fffffff'))
New-Item -ItemType Directory -Path $taskBackup | Out-Null
$taskPreviousPassword = $env:PGPASSWORD
$env:PGPASSWORD = $taskSecrets.database
try {
    & "$taskPg\pg_dump.exe" -h 127.0.0.1 -p 55432 -U vitali_odoo -d vitali_lab -Fc -f "$taskBackup\vitali_lab.dump"
    if ($LASTEXITCODE -ne 0) { throw 'Falló el respaldo de la base.' }
} finally { $env:PGPASSWORD = $taskPreviousPassword }
if (Test-Path -LiteralPath "$taskLab\data\filestore\vitali_lab") {
    Copy-Item -LiteralPath "$taskLab\data\filestore\vitali_lab" -Destination "$taskBackup\filestore" -Recurse
}
Write-Host "Respaldo de base y adjuntos: $taskBackup"
