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

:: ── Install the scheduled task ────────────────────────────────────
:: Uses wscript.exe to run the VBScript silently (no CMD window).
:: %~dp0 = full path to THIS bat file's directory (the scripts\ folder).
:: "" around the path handle spaces in folder names.

schtasks /create ^
  /tn "MMK Auto Trader" ^
  /tr "wscript.exe ""%~dp0start_server.vbs""" ^
  /sc onlogon ^
  /rl highest ^
  /f

if %errorlevel% == 0 (
    echo.
    echo  ============================================================
    echo   SUCCESS!
    echo   The server will now start automatically on every login.
    echo.
    echo   To remove autostart later:
    echo     schtasks /delete /tn "MMK Auto Trader" /f
    echo   Or: open Task Scheduler and delete "MMK Auto Trader"
    echo  ============================================================
) else (
    echo.
    echo  ============================================================
    echo   FAILED. Try right-clicking this file and choosing
    echo   "Run as administrator", then try again.
    echo  ============================================================
)

echo.
pause
