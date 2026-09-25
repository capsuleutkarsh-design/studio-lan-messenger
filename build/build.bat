@echo off
rem ------------------------------------------------------------------
rem  Builds LAN Messenger:
rem    build\dist\LANMessenger\            client program
rem    build\dist\LANMessengerServer\      server program
rem    build\output\LANMessenger-Client-Setup-x.y.z.exe
rem    build\output\LANMessenger-Server-Setup-x.y.z.exe
rem  Needs: Python 3.11+ (first run only, to create .venv) and Inno Setup 6.
rem ------------------------------------------------------------------
setlocal
pushd "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
    echo Creating Python environment .venv ...
    python -m venv .venv || goto :failed
)
".venv\Scripts\python.exe" "build\build.py" %*
if errorlevel 1 goto :failed
popd
echo.
pause
exit /b 0

:failed
popd
echo.
echo BUILD FAILED - see the messages above.
pause
exit /b 1
