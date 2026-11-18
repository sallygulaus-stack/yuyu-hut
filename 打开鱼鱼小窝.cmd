@echo off
setlocal
pushd "%~dp0"
set "PY=.venv\Scripts\python.exe"
if exist "%PY%" goto ensure
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -m venv .venv
) else (
  where python >nul 2>nul
  if errorlevel 1 goto nopy
  python -m venv .venv
)
if not exist "%PY%" goto failed
:ensure
"%PY%" -c "from PIL import Image" >nul 2>nul
if not errorlevel 1 goto launch
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 goto failed
:launch
if "%~1"=="" goto desktop
"%PY%" -B tools\launch.py %*
if errorlevel 1 goto failed
popd
exit /b 0
:desktop
"%PY%" -c "import webview, pystray" >nul 2>nul
if not errorlevel 1 goto open_desktop
"%PY%" -m pip install --disable-pip-version-check -r panel\requirements-desktop.txt
:open_desktop
start "" ".venv\Scripts\pythonw.exe" -B tools\launch.py --desktop-first
if errorlevel 1 goto failed
popd
exit /b 0
:nopy
echo Please install Python 3.10 or newer, then open this file again.
:failed
echo Startup failed. The details above can help diagnose the issue.
pause
popd
exit /b 1
