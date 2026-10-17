@echo off
echo ========================================================
echo Restoring Wi-Fi IPv4 to Automatic DHCP
echo ========================================================
netsh interface ipv4 set address name="Wi-Fi" source=dhcp
netsh interface ipv4 set dns name="Wi-Fi" source=dhcp

echo.
echo IP Configuration Restored to DHCP:
netsh interface ipv4 show addresses "Wi-Fi"
echo ========================================================
pause
