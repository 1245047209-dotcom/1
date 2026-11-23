@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set "PY=%~dp0runtime\python.exe"
if not exist "%PY%" set "PY=py -3.12"
if not exist screenshots mkdir screenshots
echo [1/3] 运行单元测试...
%PY% -m unittest discover -s tests > "%~dp0test-result.txt" 2>&1
echo exit code %errorlevel%>> "%~dp0test-result.txt"
echo [2/3] 模拟界面自检...
%PY% demo_ui.py --smoke > "%~dp0smoke-result.txt" 2>&1
echo exit code %errorlevel%>> "%~dp0smoke-result.txt"
echo [3/3] 截图（主窗口和添加设备的浅色、深色，共 4 张），请不要移动鼠标或切换窗口...
%PY% demo_ui.py --theme light --capture screenshots\ios-light.png > "%~dp0capture-result.txt" 2>&1
%PY% demo_ui.py --theme dark --capture screenshots\ios-dark.png >> "%~dp0capture-result.txt" 2>&1
%PY% demo_ui.py --theme light --page search --capture screenshots\ios-search-light.png >> "%~dp0capture-result.txt" 2>&1
%PY% demo_ui.py --theme dark --page search-expanded --capture screenshots\ios-search-dark.png >> "%~dp0capture-result.txt" 2>&1
echo exit code %errorlevel%>> "%~dp0capture-result.txt"
echo.
echo 完成：test-result.txt、smoke-result.txt、capture-result.txt 和 screenshots 文件夹。
pause
