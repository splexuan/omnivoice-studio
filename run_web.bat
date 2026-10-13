@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem ---- 国内环境：HuggingFace 走镜像（仅当需要联网下载时才生效）----
set "HF_ENDPOINT=https://hf-mirror.com"

set "PY=%~dp0.venv\Scripts\python.exe"
set "ENTRY=%~dp0web\server.py"

if not exist "%PY%" (
    echo [错误] 找不到虚拟环境：%PY%
    echo         请先在项目根目录创建 .venv 并安装依赖。
    echo.
    pause
    exit /b 1
)
if not exist "%ENTRY%" (
    echo [错误] 找不到入口文件：%ENTRY%
    echo.
    pause
    exit /b 1
)

echo ============================================
echo   OmniVoice Studio - WebUI（FastAPI + 自研前端）
echo   http://127.0.0.1:7866
echo ============================================
echo.
echo   与 Gradio 版（run.bat，端口 7865）可同时运行，便于对比。
echo.

"%PY%" "%ENTRY%"
set "CODE=%ERRORLEVEL%"

echo.
if not "%CODE%"=="0" echo [异常退出] 退出码 %CODE%，请查看上方错误信息。
echo 服务已退出。
pause
