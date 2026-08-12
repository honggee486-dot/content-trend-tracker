@echo off
setlocal EnableExtensions

cd /d "%~dp0"

set "PYTHON_EXE="
if exist ".venv\Scripts\python.exe" (
    set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
) else if exist "venv\Scripts\python.exe" (
    set "PYTHON_EXE=%CD%\venv\Scripts\python.exe"
) else (
    where python.exe >nul 2>nul
    if not errorlevel 1 set "PYTHON_EXE=python.exe"
)

if not defined PYTHON_EXE (
    echo [ERROR] Python executable was not found.
    echo Create the project virtual environment and try again.
    exit /b 1
)

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
    -File "%CD%\scripts\app_supervisor.ps1" ^
    -Action Run ^
    -ProjectRoot "%CD%" ^
    -PythonExe "%PYTHON_EXE%" ^
    -Port 8518
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo [ERROR] Managed application launch failed.
    echo Run stop_app.bat once if an older Streamlit process is still alive.
)

exit /b %EXIT_CODE%
