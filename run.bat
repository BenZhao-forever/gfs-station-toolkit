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
REM Optional extras. Skipped silently if they fail (e.g. onnxruntime on Windows 7).
REM   cryptography = at-rest password encryption
REM   PyMuPDF      = render official label PDF to image for license-free printing
REM   ddddocr      = local captcha OCR for fully-automatic print-token login
python -m pip install -q cryptography >nul 2>nul
python -m pip install -q PyMuPDF >nul 2>nul
python -m pip install -q ddddocr >nul 2>nul

REM Open fullscreen kiosk after 3s (auto-detects Chrome/Edge; safe if none).
start "" cmd /c "timeout /t 3 >nul & call "%~dp0open_kiosk.bat""

:loop
python app.py
REM Exit code 3 = updated, needs restart
if %errorlevel%==3 (
  echo Updated, restarting...
  goto loop
)
echo Server stopped.
pause
