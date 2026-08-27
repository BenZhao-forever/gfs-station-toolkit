@echo off
REM Find an installed Chrome/Edge and open the kiosk fullscreen.
REM If none found, open the default browser (not fullscreen) as a fallback.
cd /d %~dp0
set "KURL=http://127.0.0.1:5000/kiosk"

if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" (
  start "" "%ProgramFiles%\Google\Chrome\Application\chrome.exe" --app=%KURL% --start-fullscreen --kiosk-printing --user-data-dir="%~dp0chrome-profile"
  goto :eof
)
if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" (
  start "" "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" --app=%KURL% --start-fullscreen --kiosk-printing --user-data-dir="%~dp0chrome-profile"
  goto :eof
)
if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" (
  start "" "%LocalAppData%\Google\Chrome\Application\chrome.exe" --app=%KURL% --start-fullscreen --kiosk-printing --user-data-dir="%~dp0chrome-profile"
  goto :eof
)
if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" (
  start "" "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" --app=%KURL% --start-fullscreen --kiosk-printing --user-data-dir="%~dp0edge-profile"
  goto :eof
)
if exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" (
  start "" "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" --app=%KURL% --start-fullscreen --kiosk-printing --user-data-dir="%~dp0edge-profile"
  goto :eof
)

echo [WARN] Chrome/Edge not found. Opening default browser (NOT fullscreen).
echo Please install Google Chrome for proper fullscreen kiosk mode.
start "" %KURL%
