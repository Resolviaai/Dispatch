@echo off
echo [*] Removing Dispatch from Windows Task Scheduler...
schtasks /delete /tn "DispatchAutonomousEngine" /f
if %ERRORLEVEL% equ 0 (
    echo [SUCCESS] Dispatch automatic background startup removed.
) else (
    echo [INFO] Task was not registered or already removed.
)
pause
