@echo off
rem 3840x2160 desktop on the built-in display (or pass a display, e.g. -Display 2). Confirm within 15 s or it reverts.
powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0..\Set-VirtualResolution.ps1" -Width 3840 -Height 2160 %*
