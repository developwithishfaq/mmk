# start_server.ps1
# Called by Windows Task Scheduler on login.
# Waits for network, checks port 8000, then starts uvicorn hidden.

$LogFile = "D:\Python Projects\2026 April\mmk-apis\api-mmk\logs\autostart.log"
$Python  = "C:\Users\Muhammad Ishfaq\AppData\Local\Programs\Python\Python313\python.exe"
$Dir     = "D:\Python Projects\2026 April\mmk-apis\api-mmk"

function Log($msg) {
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

Log "Autostart triggered — waiting 90s for network..."
Start-Sleep -Seconds 90

# Check if already running on port 8000
$listening = netstat -an | Select-String "0.0.0.0:8000\s"
if ($listening) {
    Log "Server already running on port 8000 — nothing to do."
    exit 0
}

Log "Port 8000 is free — starting server..."

try {
    Start-Process `
        -FilePath $Python `
        -ArgumentList @("-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000") `
        -WorkingDirectory $Dir `
        -WindowStyle Hidden
    Log "Server process launched OK."
} catch {
    Log "ERROR launching server: $_"
}
