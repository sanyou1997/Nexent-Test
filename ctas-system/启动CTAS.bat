@echo off
setlocal enabledelayedexpansion

rem ============================================================
rem  CTAS 审理辅助系统 - 一键启动
rem  1) 启动本地大模型服务 (Ollama, 端口 11434) - 可选增强
rem  2) 启动 CTAS 业务服务 (端口 8001) - 必需
rem  3) 服务就绪后自动打开浏览器
rem
rem  用法：双击本文件即可。关闭本窗口不会影响已启动的服务。
rem ============================================================

set "ROOT=%~dp0"
set "PORT=8001"
set "LLM_PORT=11434"
set "URL=http://127.0.0.1:%PORT%/"

echo.
echo  ============================================================
echo    CTAS 审理辅助系统  正在启动...
echo  ============================================================
echo.

rem ---------- 步骤 1：本地大模型服务（可选增强） ----------
echo  [1/3] 检查本地大模型服务 (Ollama) ...
set "LLM_READY=0"

powershell -NoProfile -Command "try{$r=Invoke-WebRequest -Uri 'http://127.0.0.1:%LLM_PORT%/api/tags' -TimeoutSec 3 -UseBasicParsing; if($r.StatusCode -eq 200){exit 0}else{exit 1}}catch{exit 1}" >nul 2>&1
if not errorlevel 1 (
    set "LLM_READY=1"
    echo        - 本地大模型服务已在运行，无需重复启动。
    goto :step2
)

rem 查找 ollama.exe
set "OLLAMA_EXE="
if exist "D:\WorkBuddy\2026-10-01-10-07-02\local-llm-scaffold\runtime\ollama\ollama.exe" set "OLLAMA_EXE=D:\WorkBuddy\2026-10-01-10-07-02\local-llm-scaffold\runtime\ollama\ollama.exe"
if not defined OLLAMA_EXE if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" set "OLLAMA_EXE=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
if not defined OLLAMA_EXE if exist "C:\Program Files\Ollama\ollama.exe" set "OLLAMA_EXE=C:\Program Files\Ollama\ollama.exe"

if not defined OLLAMA_EXE (
    echo        - 未找到 ollama.exe，跳过大模型启动。
    echo          大模型为可选项，不影响审理主流程。
    goto :step2
)

echo        - 正在启动本地大模型服务...
rem 便携版必须显式指定 OLLAMA_MODELS，否则报 "could not locate ollama app"
set "OLLAMA_MODELS=%USERPROFILE%\.ollama\models"
if not exist "%OLLAMA_MODELS%" set "OLLAMA_MODELS=%ROOT%backend\data\ollama\models"
set "OLLAMA_LOG=%TEMP%\ctas-ollama.log"
rem start 独立窗口启动，日志重定向到文件
start "CTAS-Ollama" /min cmd /c "set OLLAMA_MODELS=%OLLAMA_MODELS%&& "%OLLAMA_EXE%" serve > "%OLLAMA_LOG%" 2>&1"

rem 等待模型服务就绪（最多 60 秒）
for /l %%I in (1,1,30) do (
    ping -n 2 127.0.0.1 >nul 2>&1
    powershell -NoProfile -Command "try{$r=Invoke-WebRequest -Uri 'http://127.0.0.1:%LLM_PORT%/api/tags' -TimeoutSec 2 -UseBasicParsing; if($r.StatusCode -eq 200){exit 0}else{exit 1}}catch{exit 1}" >nul 2>&1
    if not errorlevel 1 (
        set "LLM_READY=1"
        goto :llm_ok
    )
)
if "%LLM_READY%"=="0" (
    echo        ! 本地大模型服务未就绪（详见 %OLLAMA_LOG%）。
    echo          大模型为可选项：不启动不影响审理主流程（量纪说理、审理报告、
    echo          处分决定书均为离线确定性引擎），仅影响智能问答与文书润色。
    echo.
)
goto :step2

:llm_ok
echo        - 本地大模型服务已就绪。

rem ---------- 步骤 2：业务服务 ----------
:step2
echo  [2/3] 检查业务服务 (端口 %PORT%) ...
powershell -NoProfile -Command "try{$r=Invoke-WebRequest -Uri 'http://127.0.0.1:%PORT%/api/health' -TimeoutSec 3 -UseBasicParsing; if($r.StatusCode -eq 200){exit 0}else{exit 1}}catch{exit 1}" >nul 2>&1
if not errorlevel 1 (
    echo        - 业务服务已在运行，无需重复启动。
    goto :step3
)

echo        - 正在启动业务服务...
if not exist "%ROOT%backend\venv\Scripts\python.exe" (
    echo.
    echo   [错误] 未找到虚拟环境： %ROOT%backend\venv\Scripts\python.exe
    echo          请先在该目录执行： python -m venv venv
    echo.
    pause
    exit /b 1
)

rem start 独立窗口启动（脚本内部已设置绝对库路径与工作目录）
start "CTAS-Server" /min "%ROOT%start_ctas_server.bat"

echo        - 等待服务就绪（最长 90 秒）...
for /l %%I in (1,1,45) do (
    ping -n 2 127.0.0.1 >nul 2>&1
    powershell -NoProfile -Command "try{$r=Invoke-WebRequest -Uri 'http://127.0.0.1:%PORT%/api/health' -TimeoutSec 2 -UseBasicParsing; if($r.StatusCode -eq 200){exit 0}else{exit 1}}catch{exit 1}" >nul 2>&1
    if not errorlevel 1 goto :svc_ok
)
echo.
echo   [错误] 业务服务启动超时，请查看日志： %ROOT%backend\data\server.log
echo          常见原因：数据库路径不正确，或 8001 端口被其它程序占用。
echo.
pause
exit /b 1

:svc_ok
echo        - 业务服务已就绪。

rem ---------- 步骤 3：打开浏览器 ----------
:step3
echo  [3/3] 打开浏览器 ...
start "" "%URL%"

echo.
echo  ============================================================
echo    启动完成！
echo.
echo    访问地址： %URL%
echo    默认账号： shenli / Sl@123456
echo.
echo    本地大模型： %LLM_READY%   (1=已就绪 / 0=未启用)
echo.
echo    关闭服务： 双击「停止CTAS.bat」
echo    服务日志： %ROOT%backend\data\server.log
echo  ============================================================
echo.
ping -n 6 127.0.0.1 >nul 2>&1
exit /b 0
