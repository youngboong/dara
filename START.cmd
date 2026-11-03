@echo off
setlocal
cd /d "%~dp0"
title DARA START
powershell.exe -STA -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_dara_web.ps1" %*
set "DARA_RC=%errorlevel%"
rem Exit code 75: DARA was just updated, so START runs again with the new files.
rem A new START.cmd arrives as START.cmd.new and replaces this file first.
if exist "%~dp0START.cmd.new" (move /y "%~dp0START.cmd.new" "%~dp0START.cmd" >nul & "%~dp0START.cmd" %* & exit /b)
if "%DARA_RC%"=="75" ("%~dp0START.cmd" %* & exit /b)
if not "%DARA_RC%"=="0" (
    echo.
    echo DARA could not be started. See the error above.
    pause
)
