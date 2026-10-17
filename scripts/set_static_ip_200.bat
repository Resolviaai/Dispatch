@echo off
:: Ensure admin privileges
echo ========================================================
echo Setting Wi-Fi IPv4 to Permanent Static: 192.168.0.200
echo Gateway: 192.168.0.1  DNS: 192.168.0.1
echo ========================================================
netsh interface ipv4 set address name="Wi-Fi" static 192.168.0.200 255.255.255.0 192.168.0.1
netsh interface ipv4 set dns name="Wi-Fi" static 192.168.0.1 validate=no

echo.
echo IP Configuration Updated Successfully:
netsh interface ipv4 show addresses "Wi-Fi"
echo.
echo Your permanent Dispatch URL is now:
echo http://192.168.0.200:8000/download/dispatch.apk
echo ========================================================
pause
