' start_server.vbs
' Called by Windows Task Scheduler on login (via wscript.exe).
' Waits 90s for network, then launches uvicorn hidden.
' Activity written to logs\autostart.log for post-reboot review.
' Pure ASCII - no Unicode chars - wscript.exe reads ANSI.

Option Explicit

Dim WshShell, oFSO
Set WshShell = CreateObject("WScript.Shell")
Set oFSO    = CreateObject("Scripting.FileSystemObject")

Dim sPython, sDir, sLog
sPython = "C:\Users\Muhammad Ishfaq\AppData\Local\Programs\Python\Python313\python.exe"
sDir    = "D:\Python Projects\2026 April\mmk-apis\api-mmk"
sLog    = sDir & "\logs\autostart.log"

Sub LogLine(msg)
    Dim fh
    On Error Resume Next
    Set fh = oFSO.OpenTextFile(sLog, 8, True)
    If Err.Number = 0 Then
        fh.WriteLine Now() & "  " & msg
        fh.Close
    End If
    On Error GoTo 0
End Sub

' Wait for network to come up after login
LogLine "Autostart triggered - waiting 90s for network..."
WScript.Sleep 90000

' Launch uvicorn hidden (window style 0 = hidden, False = don't wait)
LogLine "Launching server..."
WshShell.CurrentDirectory = sDir
WshShell.Run """" & sPython & """ -m uvicorn server:app --host 0.0.0.0 --port 8000", 0, False
LogLine "Server process started. See logs\trader.log for details."
