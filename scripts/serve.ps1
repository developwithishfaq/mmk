# Start the MMK API server.
# Loads credentials from .env.local (KEY=VALUE per line, # comments ignored)
# then launches uvicorn on 0.0.0.0:8000 with --reload.
#
# Usage:
#   .\scripts\serve.ps1            # foreground
#   Start-Process powershell -ArgumentList "-File .\scripts\serve.ps1"   # background

param(
    [string]$EnvFile = ".env.local",
    [string]$BindHost = "0.0.0.0",
    [int]$Port = 8000,
    [switch]$NoReload
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$envPath = Join-Path $root $EnvFile
if (-not (Test-Path $envPath)) {
    Write-Error "Missing $EnvFile at $envPath. Create it with MMK_USER_ID/MMK_PASSWORD/MMK_PIN."
    exit 1
}

Get-Content $envPath | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) { return }
    $eq = $line.IndexOf("=")
    if ($eq -lt 1) { return }
    $k = $line.Substring(0, $eq).Trim()
    $v = $line.Substring($eq + 1).Trim().Trim('"').Trim("'")
    Set-Item -Path "Env:$k" -Value $v
}

foreach ($k in @("MMK_USER_ID","MMK_PASSWORD","MMK_PIN")) {
    if (-not (Get-Item -Path "Env:$k" -ErrorAction SilentlyContinue)) {
        Write-Error "$k not set after loading $EnvFile"
        exit 1
    }
}

$reloadFlag = if ($NoReload) { "" } else { "--reload" }
Write-Output "Starting uvicorn on ${BindHost}:$Port (user=$env:MMK_USER_ID)"
$cmd = "uvicorn server:app --host $BindHost --port $Port $reloadFlag"
Invoke-Expression $cmd
