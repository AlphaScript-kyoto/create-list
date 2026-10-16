@echo off
chcp 65001 > nul
cd /d "%~dp0"

py -3 -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :error

py -3 scripts\build_dist.py %*
if errorlevel 1 goto :error

echo.
echo Build finished: dist\ListCollector\ListCollector.exe
pause
exit /b 0

:error
echo.
echo Build failed.
pause
exit /b 1
