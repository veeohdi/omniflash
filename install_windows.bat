@echo off
setlocal
echo =====================================================
echo Installing OmniFlash (Windows)
echo =====================================================
echo.

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed.
    echo Please install Python 3 from https://www.python.org/
    pause
    exit /b 1
)

echo Installing Flask dependency...
python -m pip install flask

echo.
echo =====================================================
echo Installation Complete!
echo Run OmniFlash by double-clicking run.bat
echo =====================================================
pause
