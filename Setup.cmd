@echo off
rem Double-click to set up WWOZ Studio on this computer (see setup.ps1).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
echo.
pause
