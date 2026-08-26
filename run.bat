@echo off
setlocal
cd /d %~dp0
title GOFO Station Toolkit

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found. Please install Python 3.10+ and check "Add python.exe to PATH".
  pause
  exit /b 1
)

echo Installing / updating dependencies...
python -m pip install -q -r requirements.txt
REM Optional: at-rest password encryption. Skipped silently if it fails (e.g. Windows 7).
python -m pip install -q cryptography >nul 2>nul

REM Open fullscreen kiosk after 3s. Try Chrome, fall back to Edge.
start "" cmd /c "timeout /t 3 >nul & (start chrome --app=http://127.0.0.1:5000/kiosk --start-fullscreen --user-data-dir=%~dp0chrome-profile || start msedge --app=http://127.0.0.1:5000/kiosk --start-fullscreen --user-data-dir=%~dp0edge-profile)"

:loop
python app.py
REM Exit code 3 = updated, needs restart
if %errorlevel%==3 (
  echo Updated, restarting...
  goto loop
)
echo Server stopped.
pause
