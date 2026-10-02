@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0prepare-dependencies.ps1" -Silent
exit /b %errorlevel%
