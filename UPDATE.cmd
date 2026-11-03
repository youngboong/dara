@echo off
setlocal
cd /d "%~dp0"
title DARA UPDATE
rem Downloads only the files that changed on GitHub. No Git is needed.
rem An older DARA folder without the updater fetches it first.
if not exist "%~dp0scripts\update_dara.ps1" (
    echo Downloading the DARA updater...
    powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; [Net.ServicePointManager]::SecurityProtocol=[Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12; New-Item -ItemType Directory -Force -Path 'scripts' | Out-Null; Invoke-WebRequest -UseBasicParsing -Uri 'https://raw.githubusercontent.com/youngboong/dara/main/scripts/update_dara.ps1' -OutFile 'scripts\update_dara.ps1'"
)
if not exist "%~dp0scripts\update_dara.ps1" (
    echo Could not download the updater. Check the internet connection and try again.
    pause
    exit /b 1
)
rem One line on purpose: this file may be replaced while it runs, so nothing after it is read.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\update_dara.ps1" -Launcher UPDATE.cmd %* & (if exist "%~dp0UPDATE.cmd.new" move /y "%~dp0UPDATE.cmd.new" "%~dp0UPDATE.cmd" >nul) & pause & exit /b
