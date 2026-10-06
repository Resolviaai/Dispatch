@echo off
REM ============================================================================
REM Dispatch: Windows Automatic Background Startup Installer
REM Registers Dispatch in Windows Task Scheduler to start silently on boot/logon.
REM Runs with low priority so it never lags games, browser, or foreground apps.
REM ============================================================================

set SCRIPT_DIR=%~dp0
set VBS_PATH=%SCRIPT_DIR%run_background.vbs

echo [*] Registering Dispatch Background Service with Windows Task Scheduler...
schtasks /create /tn "DispatchAutonomousEngine" /tr "wscript.exe \"%VBS_PATH%\"" /sc onlogon /rl limited /f

if %ERRORLEVEL% equ 0 (
    echo [SUCCESS] Dispatch will now automatically start silently whenever you log in!
    echo [INFO] Resource governor enforces below-normal CPU priority and battery preservation.
) else (
    echo [ERROR] Failed to register task with error code %ERRORLEVEL%.
)
pause
