@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0runtime\python.exe" (
    "%~dp0runtime\python.exe" "%~dp0aula_f87_v2d.py"
) else (
    py -3.12 "%~dp0aula_f87_v2d.py"
)
pause
