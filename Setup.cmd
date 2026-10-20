@echo off
rem Double-click to set up WWOZ Studio on this computer (see setup.ps1).
if not exist "%~dp0setup.ps1" goto unzip
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
echo.
pause
exit /b

:unzip
echo Setup can't find the rest of WWOZ Studio. That happens when Setup.cmd is opened
echo from inside the downloaded ZIP file.
echo.
echo Close this window, right-click the ZIP file, choose "Extract All", then open the
echo extracted folder and double-click Setup.cmd there.
echo.
pause
exit /b 1
