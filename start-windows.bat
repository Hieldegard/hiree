@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>&1
if errorlevel 1 goto no_python
py -3.12 --version >nul 2>&1
if errorlevel 1 goto no_python
if not exist ".venv\Scripts\python.exe" (
    echo Creating a Python environment...
    py -3.12 -m venv .venv
    if not exist ".venv\Scripts\python.exe" goto no_python
)
echo Installing required packages...
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto failed
echo Checking the browser...
.venv\Scripts\python.exe -m playwright install chromium
if errorlevel 1 goto failed
echo.
echo Open http://127.0.0.1:8765 in your browser.
echo Keep this window open. Press Ctrl+C to stop Hiree.
.venv\Scripts\python.exe -m hiree.app
if errorlevel 1 goto failed
exit /b 0
:no_python
echo Python 3.12 was not found. Install Python 3.12 and the Python launcher, then try again.
pause
exit /b 1
:failed
echo.
echo Setup or launch failed. Please share a screenshot of this window.
pause
exit /b 1
