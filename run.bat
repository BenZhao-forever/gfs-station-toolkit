@echo off
chcp 65001 >nul
cd /d %~dp0
echo === GOFO 站点工具包 启动中 ===
where python >nul 2>nul || (echo [错误] 未找到 Python，请先安装 Python 3.10+ 并勾选 Add to PATH & pause & exit /b 1)
python -m pip install -q -r requirements.txt

REM 3 秒后打开全屏大屏（等服务起来）
start "" cmd /c "timeout /t 3 >nul & start chrome --app=http://127.0.0.1:5000/kiosk --start-fullscreen --user-data-dir=%~dp0chrome-profile"

:loop
python app.py
REM 退出码 3 = 自动更新后需重启
if %errorlevel%==3 (
  echo [更新] 已更新，正在重启...
  goto loop
)
echo 服务已停止。
pause
