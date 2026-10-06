@echo off
REM ============================================================================
REM Dispatch: Windows Background Startup Uninstaller
REM Removes DispatchAutonomousEngine from Windows Task Scheduler.
REM ============================================================================

echo [*] Removing Dispatch Background Service from Windows Task Scheduler...
schtasks /delete /tn "DispatchAutonomousEngine" /f

if %ERRORLEVEL% equ 0 (
    echo [SUCCESS] Dispatch background auto-start has been removed.
) else (
    echo [INFO] Task was not found or already removed.
)
pause
