@echo off
setlocal
cd /d "%~dp0"

python --version 2>&1 | findstr /R "Python 3\." >nul
if %errorlevel% neq 0 (
    echo [ERROR] Python 3 is not installed or not in your PATH.
    echo Please install Python 3 from https://www.python.org/
    pause
    exit /b 1
)

echo =====================================================
echo Starting OmniFlash — Pixel 4 XL (coral) Flasher...
echo =====================================================
echo.

start "" cmd /c "python wait_for_server.py && start http://127.0.0.1:8086"
python server.py
pause
