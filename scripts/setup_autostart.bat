@echo off
title MMK Auto Trader - Setup Autostart
color 0B

echo.
echo  ============================================================
echo   MMK Auto Trader  -  Windows Autostart Setup
echo   (Run this ONCE to install; no need to run again)
echo  ============================================================
echo.
echo  This will add a Windows Task Scheduler entry so the server
echo  starts automatically every time you log into Windows.
echo.
echo  The server starts 90 seconds after login (giving Windows
echo  time to connect to the internet first).
echo.

:: ── Use PowerShell to create the task (handles spaces in paths) ───
:: [char]34 = double-quote character, avoids quoting nightmares.
powershell -ExecutionPolicy Bypass -Command "$vbs = 'D:\Python Projects\2026 April\mmk-apis\api-mmk\scripts\start_server.vbs'; $arg = [char]34 + $vbs + [char]34; $a = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument $arg; $t = New-ScheduledTaskTrigger -AtLogon; Register-ScheduledTask -TaskName 'MMK Auto Trader' -Action $a -Trigger $t -RunLevel Highest -Force -Description 'Starts MMK Auto Trader server 90s after Windows login'"

if %errorlevel% == 0 (
    echo.
    echo  ============================================================
    echo   SUCCESS!
    echo   The server will now start automatically on every login.
    echo.
    echo   To remove autostart later, run:
    echo     schtasks /delete /tn "MMK Auto Trader" /f
    echo   Or open Task Scheduler and delete "MMK Auto Trader"
    echo  ============================================================
) else (
    echo.
    echo  ============================================================
    echo   FAILED. Right-click this file, choose
    echo   "Run as administrator", then try again.
    echo  ============================================================
)

echo.
pause
