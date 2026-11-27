@echo off
rem 后台启动 CTAS 业务服务（独立窗口，输出重定向到日志文件）
rem 用法：start_ctas_server.bat  （由 启动CTAS.bat 调用，也可单独双击调试）

setlocal
set "BACKEND=%~dp0backend"
set "PORT=8001"
rem 数据库路径必须绝对：相对路径会落到 backend\ctas.db（空库），而非 data\ctas.db
set "CTAS_DB_PATH=%BACKEND%\data\ctas.db"
set "CTAS_WEB_DIR=web-ctas"
set "CTAS_APP_NAME=审理辅助系统"

cd /d "%BACKEND%"
venv\Scripts\python.exe -m uvicorn app.main_ctas:app --port %PORT% --host 127.0.0.1 > "%BACKEND%\data\server.log" 2>&1
