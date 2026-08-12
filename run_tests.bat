@echo off
setlocal
set "TEST_TEMP=%TEMP%\content-trend-tracker-pytest-%RANDOM%-%RANDOM%"

if exist "%TEST_TEMP%" rmdir /s /q "%TEST_TEMP%"
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp="%TEST_TEMP%"
set "EXIT_CODE=%ERRORLEVEL%"
if exist "%TEST_TEMP%" rmdir /s /q "%TEST_TEMP%"

exit /b %EXIT_CODE%
