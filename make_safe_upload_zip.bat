@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

REM ============================================================
REM Safe upload ZIP maker for content-trend-tracker
REM
REM Project:
REM   C:\AIProjects\content-trend-tracker
REM
REM Output:
REM   content-trend-tracker_safe_upload.zip
REM
REM The ZIP contains source code, tests, documents, example configs,
REM and project scripts needed for review, modification, and testing.
REM
REM It always excludes:
REM   - secrets and login/authentication data
REM   - local databases and collected datasets
REM   - virtual environments and dependencies
REM   - caches, logs, test/coverage reports, and build outputs
REM   - IDE/OS temporary files, backups, and existing archives
REM
REM The ZIP root is the project root itself. No extra top folder is added.
REM ============================================================

set "PROJECT_ROOT=C:\AIProjects\content-trend-tracker"

for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "STAMP=%%I"
set "ZIP_NAME=content-trend-tracker_safe_upload.zip"
set "ZIP_PATH=%PROJECT_ROOT%\!ZIP_NAME!"
set "STAGING=%TEMP%\content-trend-tracker_safe_zip_!STAMP!_!RANDOM!!RANDOM!"

if not exist "%PROJECT_ROOT%\" (
    echo [ERROR] Project folder not found:
    echo %PROJECT_ROOT%
    exit /b 1
)

REM Existing ZIP files are excluded from staging.
REM Older safe-upload ZIPs are deleted only after a new ZIP succeeds.

if exist "%STAGING%" rmdir /s /q "%STAGING%"
mkdir "%STAGING%"
if not exist "%STAGING%\" (
    echo [ERROR] Could not create temporary staging folder.
    exit /b 1
)

echo [INFO] Copying review-safe project files...
robocopy "%PROJECT_ROOT%" "%STAGING%" /E /XJ /R:1 /W:1 /COPY:DAT /DCOPY:DAT /NP ^
 /XD ".git" ".venv" "venv" "env" "ENV" "node_modules" ".pnpm-store" ".yarn" ^
     "__pycache__" ".pytest_cache" ".mypy_cache" ".ruff_cache" ".cache" ".tox" ".nox" ".hypothesis" ".ipynb_checkpoints" ^
     "htmlcov" "coverage" "test-results" "playwright-report" ^
     ".next" "out" "build" "dist" "site" "data" ^
     "logs" "log" "reports" "exports" "backups" "backup" "tmp" "temp" ".tmp" ".temp" "artifacts" "screenshots" "downloads" ^
     ".idea" ".vscode" ".vs" ".history" ^
     "browser_profile" "browser-profile" "chrome_profile" "chrome-profile" "user-data-dir" ".auth" ^
 /XF ".env" ".env.*" ".envrc" "secrets*.toml" ^
     "credentials.json" "token.json" "oauth*.json" "client_secret*.json" "service_account*.json" ^
     "storage_state*.json" "cookies*.json" "auth_state*.json" ^
     "*.duckdb" "*.duckdb.*" "*.db" "*.db-shm" "*.db-wal" "*.sqlite" "*.sqlite3" ^
     "*.parquet" "*.feather" "*.arrow" ^
     "*.log" "*.tmp" "*.temp" "*.bak" "*.old" "*.orig" "*.rej" "*.swp" "*.swo" "*~" "*.pid" ^
     "patch_*.py" "collect_trend_quality_samples.ps1" "correct_guardrail_regressions.py" ^
     "*.pyc" "*.pyo" "*.pyd" ".coverage" ".coverage.*" "coverage.xml" "junit.xml" "pytest-report*.xml" "test-results*.xml" ^
     "*.zip" "*.7z" "*.rar" "*.tar" "*.tar.gz" "*.tgz" "*.gz" "*.bz2" "*.xz" ^
     "*.key" "*.pem" "*.p12" "*.pfx" "*.cer" "*.crt" "id_rsa*" "id_ed25519*" "known_hosts" ^
     "Thumbs.db" "desktop.ini" ".DS_Store" >nul

set "ROBOCOPY_RC=!ERRORLEVEL!"
if !ROBOCOPY_RC! GEQ 8 (
    echo [ERROR] Robocopy failed. Errorlevel: !ROBOCOPY_RC!
    rmdir /s /q "%STAGING%" >nul 2>nul
    exit /b 1
)

REM Root-level apply_*.ps1 files are one-time local patches.
REM Keep the official scripts\apply_update.ps1 that is part of the project.
if exist "%STAGING%\apply_*.ps1" del /f /q "%STAGING%\apply_*.ps1" >nul 2>nul

REM Secret-like names are excluded for safety. Restore only explicit examples.
for %%F in (.env.example .env.sample .env.template secrets.example.toml secrets.sample.toml) do (
    if exist "%PROJECT_ROOT%\%%F" copy /y "%PROJECT_ROOT%\%%F" "%STAGING%\%%F" >nul
)

REM Keep an empty data directory marker, but never upload collected local data.
if not exist "%STAGING%\data\" mkdir "%STAGING%\data"
if exist "%PROJECT_ROOT%\data\.gitkeep" copy /y "%PROJECT_ROOT%\data\.gitkeep" "%STAGING%\data\.gitkeep" >nul

REM Basic completeness check before compression.
if not exist "%STAGING%\app.py" (
    echo [ERROR] app.py was not copied. ZIP creation stopped.
    rmdir /s /q "%STAGING%" >nul 2>nul
    exit /b 1
)
if not exist "%STAGING%\src\" (
    echo [ERROR] src folder was not copied. ZIP creation stopped.
    rmdir /s /q "%STAGING%" >nul 2>nul
    exit /b 1
)
if not exist "%STAGING%\tests\" (
    echo [ERROR] tests folder was not copied. ZIP creation stopped.
    rmdir /s /q "%STAGING%" >nul 2>nul
    exit /b 1
)

if exist "%ZIP_PATH%" del /f /q "%ZIP_PATH%" >nul 2>nul

echo [INFO] Creating ZIP...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
 "$ErrorActionPreference='Stop'; Add-Type -AssemblyName System.IO.Compression.FileSystem; [System.IO.Compression.ZipFile]::CreateFromDirectory('%STAGING%', '%ZIP_PATH%', [System.IO.Compression.CompressionLevel]::Optimal, $false)"

if errorlevel 1 (
    echo [ERROR] ZIP creation failed.
    rmdir /s /q "%STAGING%" >nul 2>nul
    exit /b 1
)

rmdir /s /q "%STAGING%" >nul 2>nul

if not exist "%ZIP_PATH%" (
    echo [ERROR] ZIP file was not created.
    exit /b 1
)

for %%Z in ("%ZIP_PATH%") do set "ZIP_SIZE=%%~zZ"
if "!ZIP_SIZE!"=="0" (
    echo [ERROR] ZIP file is empty.
    del /f /q "%ZIP_PATH%" >nul 2>nul
    exit /b 1
)

REM Keep only the newly created safe-upload ZIP.
for %%Z in ("%PROJECT_ROOT%\content-trend-tracker_safe_upload*.zip") do (
    if exist "%%~fZ" if /I not "%%~fZ"=="%ZIP_PATH%" del /f /q "%%~fZ" >nul 2>nul
)

echo.
echo [OK] Safe upload ZIP created:
echo %ZIP_PATH%
echo.
echo [INCLUDED]
echo Source, tests, docs, requirements, example configs, and BAT/scripts
echo.
echo [EXCLUDED]
echo Secrets/auth data, local DB/data files, logs, reports/exports,
echo dependencies, virtual environments, caches, coverage/build outputs,
echo IDE/temp/backup files, browser profiles, and existing archives

echo.
echo Open the ZIP once before uploading and confirm that no private files are included.

endlocal

exit /b 0
