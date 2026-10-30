@echo off
title Android Emulator Launcher
echo ===================================================
echo           DISPATCH ANDROID EMULATOR
echo ===================================================
echo.

:: 1. Terminate any orphan emulator processes
taskkill /F /IM qemu-system-x86_64.exe >nul 2>&1
taskkill /F /IM emulator.exe >nul 2>&1

:: 2. Clear stale lock files
if exist "%USERPROFILE%\.android\avd\Medium_Phone.avd\multiinstance.lock" (
    del /f /q "%USERPROFILE%\.android\avd\Medium_Phone.avd\multiinstance.lock" >nul 2>&1
)
if exist "%USERPROFILE%\.android\avd\Medium_Phone.avd\hardware-qemu.ini.lock" (
    rmdir /s /q "%USERPROFILE%\.android\avd\Medium_Phone.avd\hardware-qemu.ini.lock" >nul 2>&1
)

echo Starting Android Phone Emulator (Medium Phone API 36.0)...
echo The emulator window will appear on your desktop.
echo.

"%LOCALAPPDATA%\Android\Sdk\emulator\emulator.exe" -avd Medium_Phone_API_36.0

if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] Emulator exited with code %ERRORLEVEL%.
    pause
)
