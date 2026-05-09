' start_server.vbs
' ---------------------------------------------------------------
' Silently starts the MMK Auto Trader server if it is not already
' running. Designed to be called by Windows Task Scheduler on login.
'
' - Waits 90 seconds after login so the network is ready.
' - Checks port 8000 before starting (idempotent).
' - No window is shown at any point.
' ---------------------------------------------------------------

Option Explicit

Dim WshShell, oExec, sNetstat, sDir, sCmd

Set WshShell = CreateObject("WScript.Shell")

' ── Wait for network to initialise after login ──────────────────
WScript.Sleep 90000   ' 90 seconds

' ── Check if server is already running on port 8000 ─────────────
Set oExec  = WshShell.Exec("netstat -an")
sNetstat   = oExec.StdOut.ReadAll()

If InStr(sNetstat, ":8000 ") > 0 Then
    ' Already running — nothing to do.
    WScript.Quit 0
End If

' ── Launch uvicorn completely hidden ────────────────────────────
sDir = "D:\Python Projects\2026 April\mmk-apis\api-mmk"
sCmd = "cmd /c cd /d """ & sDir & """ && python -m uvicorn server:app --host 0.0.0.0 --port 8000"

' WindowStyle 0 = hidden, bWaitOnReturn False = fire-and-forget
WshShell.Run sCmd, 0, False
