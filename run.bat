@echo off
rem PanRush 启动脚本：起服务 + 自动打开浏览器
cd /d %~dp0
start "" cmd /c "timeout /t 2 >nul & start http://127.0.0.1:18400"
where python >nul 2>nul
if %errorlevel%==0 (
  python -m uvicorn server.main:app --host 127.0.0.1 --port 18400
) else (
  echo 未找到 python，请先安装 Python 3.10+ 并 pip install -r requirements.txt
  pause
)
