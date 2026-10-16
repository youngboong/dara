@echo off
setlocal
cd /d "%~dp0"

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_dara_web.ps1"
if errorlevel 1 (
    echo.
    echo DARA could not be started. See the error above.
    pause
)
