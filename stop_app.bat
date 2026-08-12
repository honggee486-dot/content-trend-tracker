@echo off
setlocal EnableExtensions

cd /d "%~dp0"

set "POWERSHELL_EXE="
where pwsh.exe >nul 2>nul
if not errorlevel 1 set "POWERSHELL_EXE=pwsh.exe"
if not defined POWERSHELL_EXE (
    where powershell.exe >nul 2>nul
    if not errorlevel 1 set "POWERSHELL_EXE=powershell.exe"
)

if not defined POWERSHELL_EXE (
    echo [ERROR] PowerShell executable was not found.
    exit /b 1
)

"%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass ^
    -File "%CD%\scripts\stop_registered_app.ps1" ^
    -ProjectRoot "%CD%" ^
    -Port 8518

exit /b %ERRORLEVEL%
