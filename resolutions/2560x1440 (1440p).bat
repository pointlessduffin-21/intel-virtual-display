@echo off
rem 2560x1440 desktop on the built-in display (or pass a display, e.g. -Display 2). Confirm within 15 s or it reverts.
powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0..\Set-VirtualResolution.ps1" -Width 2560 -Height 1440 %*
