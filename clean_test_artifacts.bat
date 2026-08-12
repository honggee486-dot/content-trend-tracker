@echo off
setlocal

for /d %%D in (.pytest_cache .pytest_manual_check .codex_pytest_* .codex_pycache_*) do (
    if exist "%%D" rmdir /s /q "%%D"
)
if exist "__pycache__" rmdir /s /q "__pycache__"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$roots = @('.\src', '.\tests', '.\scripts') | Where-Object { Test-Path $_ }; " ^
  "Get-ChildItem -Path $roots -Directory -Filter '__pycache__' -Recurse -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force"

echo Test temporary folders have been removed.
