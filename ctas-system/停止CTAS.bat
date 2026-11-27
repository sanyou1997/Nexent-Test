@echo off
setlocal

rem ============================================================
rem  CTAS 审理辅助系统 - 停止服务
rem  停止 8001 (业务服务) 与 11434 (本地大模型服务)
rem  ============================================================

echo.
echo  正在停止 CTAS 相关服务...
echo.

rem ---------- 停止业务服务 8001 ----------
set "SVC_KILLED=0"
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8001" ^| findstr "LISTENING"') do (
    if not "%%P"=="0" (
        echo  - 停止业务服务 (PID %%P, 端口 8001) ...
        taskkill /PID %%P /F >nul 2>&1
        set "SVC_KILLED=1"
    )
)
if "%SVC_KILLED%"=="0" echo  - 业务服务未在运行。

rem ---------- 停止本地大模型 11434 ----------
rem 仅停止本项目启动的 ollama（按命令行含 runtime\ollama 判定），避免误杀其他 Ollama 实例
set "LLM_KILLED=0"
for /f "tokens=2" %%P in ('wmic process where "name='ollama.exe'" get ProcessId^,CommandLine /format:list 2^>nul ^| findstr /r "ollama.exe"') do (
    echo  - 停止本地大模型 (PID %%P) ...
    taskkill /PID %%P /F >nul 2>&1
    set "LLM_KILLED=1"
)
if "%LLM_KILLED%"=="0" echo  - 本地大模型服务未在运行（或不由本项目启动，保留）。

echo.
echo  停止完成。
echo.
timeout /t 3 >nul
exit /b 0
