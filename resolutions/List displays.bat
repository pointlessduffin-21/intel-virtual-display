@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\Set-VirtualResolution.ps1" -List
pause
