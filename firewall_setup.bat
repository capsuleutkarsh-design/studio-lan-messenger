@echo off
rem Run this ONCE on the server PC as Administrator (right-click > Run as administrator).
rem It lets the artist PCs reach the messenger server through Windows Firewall.
rem Change the ports here if you changed them in the server settings.

set TCP_PORT=5150
set UDP_PORT=5151

netsh advfirewall firewall delete rule name="LAN Messenger Server (TCP)" >nul 2>&1
netsh advfirewall firewall delete rule name="LAN Messenger Discovery (UDP)" >nul 2>&1
netsh advfirewall firewall add rule name="LAN Messenger Server (TCP)" dir=in action=allow protocol=TCP localport=%TCP_PORT% profile=domain,private
netsh advfirewall firewall add rule name="LAN Messenger Discovery (UDP)" dir=in action=allow protocol=UDP localport=%UDP_PORT% profile=domain,private
echo.
echo Firewall rules added for TCP %TCP_PORT% and UDP %UDP_PORT%.
pause
