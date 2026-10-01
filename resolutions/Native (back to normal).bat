@echo off
rem Back to the display's native resolution (saved).
powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0..\Set-VirtualResolution.ps1" -Native -NoPrompt %*
