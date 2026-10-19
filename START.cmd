@echo off
setlocal
cd /d "%~dp0"
title DARA START
powershell.exe -STA -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_dara_web.ps1"
if errorlevel 1 (
    echo.
    echo DARA could not be started. See the error above.
    pause
)
