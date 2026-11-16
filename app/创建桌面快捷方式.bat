@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0runtime\python.exe" (
    "%~dp0runtime\python.exe" "%~dp0create_shortcuts.py"
    goto finish
)
where py.exe >nul 2>nul
if not errorlevel 1 (
    py.exe -3.12 "%~dp0create_shortcuts.py"
    goto finish
)
where python.exe >nul 2>nul
if not errorlevel 1 (
    python.exe "%~dp0create_shortcuts.py"
    goto finish
)
echo Python 3.12 x64 with Tcl/Tk is required.
:finish
pause
