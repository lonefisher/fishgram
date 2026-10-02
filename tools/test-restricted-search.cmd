@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0test-restricted-search.ps1" %*
exit /b %errorlevel%
