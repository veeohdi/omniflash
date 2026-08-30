@echo off
setlocal
cd /d "%~dp0"

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in your PATH.
    echo Please install Python from https://www.python.org/
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
