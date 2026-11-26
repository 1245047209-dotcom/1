@echo off
setlocal
cd /d "%~dp0"
set "NEXUS_SOURCE_SCRIPT=%~1"
if not defined NEXUS_SOURCE_SCRIPT set "NEXUS_SOURCE_SCRIPT=my_devices.pyw"
set "NEXUS_SOURCE_OPTIONS="
if /i "%~2"=="--keep-warm" set "NEXUS_SOURCE_OPTIONS=--keep-warm"
if exist "%~dp0runtime\pythonw.exe" (
    start "" "%~dp0runtime\pythonw.exe" "%~dp0%NEXUS_SOURCE_SCRIPT%" %NEXUS_SOURCE_OPTIONS%
    exit /b 0
)
where pyw.exe >nul 2>nul
if not errorlevel 1 (
    start "" pyw.exe -3.12 "%~dp0%NEXUS_SOURCE_SCRIPT%" %NEXUS_SOURCE_OPTIONS%
    exit /b 0
)
where pythonw.exe >nul 2>nul
if not errorlevel 1 (
    start "" pythonw.exe "%~dp0%NEXUS_SOURCE_SCRIPT%" %NEXUS_SOURCE_OPTIONS%
    exit /b 0
)
where py.exe >nul 2>nul
if not errorlevel 1 (
    py.exe -3.12 "%~dp0%NEXUS_SOURCE_SCRIPT%" %NEXUS_SOURCE_OPTIONS%
    exit /b %errorlevel%
)
where python.exe >nul 2>nul
if not errorlevel 1 (
    python.exe "%~dp0%NEXUS_SOURCE_SCRIPT%" %NEXUS_SOURCE_OPTIONS%
    exit /b %errorlevel%
)
echo Python 3.12 x64 with Tcl/Tk is required. Please install Python or add a runtime folder.
pause
exit /b 1
