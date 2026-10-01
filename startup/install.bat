@echo off
rem Usage: install.bat [width] [height] [display]   (default 3840 2160 auto)
set W=%~1& set H=%~2& set D=%~3
if "%W%"=="" set W=3840
if "%H%"=="" set H=2160
if "%D%"=="" set D=auto
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\Install-Startup.ps1" -Width %W% -Height %H% -Display %D%
pause
