@echo off
setlocal enabledelayedexpansion

title Dispatch Desktop Server

echo ===================================================
echo           DISPATCH AUTONOMOUS SERVER
echo ===================================================
echo.

:: 1. Navigate to repository root
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%.."
echo Working Directory: %CD%

:: 2. Check Python installation
where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Python is not found in PATH!
    echo Please install Python 3.10+ and make sure it is added to your PATH.
    echo.
    pause
    exit /b 1
)

for /f "tokens=*" %%i in ('python --version 2^>^&1') do set PYTHON_VERSION=%%i
echo Detected Python: !PYTHON_VERSION!

:: 3. Check FFmpeg installation
where ffmpeg >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [WARNING] FFmpeg is not found in PATH!
    echo Some video processing features may fail without FFmpeg.
    echo.
)

:: 4. Ensure storage directory exists
if not exist "storage" (
    mkdir "storage"
)

:: 5. Launch Dispatch Server
echo.
echo Starting Dispatch Desktop Server on http://127.0.0.1:8000 ...
echo Press Ctrl+C to shut down.
echo.

python -m uvicorn dispatch.main:app --host 127.0.0.1 --port 8000

if %ERRORLEVEL% neq 0 (
    echo.
    echo [DISPATCH] Server exited with code %ERRORLEVEL%.
    pause
)

endlocal
