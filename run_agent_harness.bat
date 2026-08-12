@echo off
setlocal EnableExtensions DisableDelayedExpansion

cd /d "%~dp0"
set "POWERSHELL_EXE="

where pwsh.exe >nul 2>nul
if not errorlevel 1 set "POWERSHELL_EXE=pwsh.exe"

if not defined POWERSHELL_EXE (
    if exist "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" (
        set "POWERSHELL_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
    )
)

if not defined POWERSHELL_EXE (
    echo [ERROR] PowerShell 7 or Windows PowerShell 5.1 was not found.
    exit /b 1
)

"%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\check_harness.ps1" %*
exit /b %ERRORLEVEL%
