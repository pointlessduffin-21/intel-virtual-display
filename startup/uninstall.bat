@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\Install-Startup.ps1" -Remove
pause
