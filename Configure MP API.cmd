@echo off
setlocal
cd /d "%~dp0"
powershell.exe -STA -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_dara_web.ps1" -ConfigureApiKey
if errorlevel 1 (
    echo.
    echo API key setup failed. See the error above.
)
pause
