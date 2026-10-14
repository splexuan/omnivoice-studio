@echo off
rem 本文件必须是 GBK 编码 + CRLF 换行，且【不要 chcp 65001】——
rem 实测：UTF-8 + chcp 会让 cmd 把中文行从半截截断，报 not recognized。
cd /d "%~dp0"

rem ============================================================
rem  优先用项目内置的便携运行时 runtime\ ：整套 Python 环境就在
rem  项目目录里，不依赖本机安装的 Python，文件夹可随意移动/复制。
rem  没有 runtime\ 时回退到 .venv\（setup.bat 从零搭建的那套）。
rem ============================================================
set "PY=%~dp0runtime\python.exe"
if not exist "%PY%" (
    if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
)
set "ENTRY=%~dp0web\server.py"

rem ---- 隔离宿主环境 ----
rem  清掉外部 PYTHONPATH，防止别的 Python 的包串进来；
rem  固定 UTF-8，避免换机后中文输出乱码。
set "PYTHONPATH="
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

rem ---- 国内环境：HuggingFace 走镜像（仅当需要联网下载时才生效）----
set "HF_ENDPOINT=https://hf-mirror.com"

if not exist "%PY%" (
    echo [错误] 没找到可用的 Python 运行时，两个位置都没有：
    echo         内置运行时：%~dp0runtime\python.exe
    echo         备用环境　：%~dp0.venv\Scripts\python.exe
    echo.
    echo         如果拿到的是从仓库克隆的副本（不含 runtime\），
    echo         请先运行 setup.bat 搭建环境。
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
echo   运行时：%PY%
echo   界面只有这一套（早期并存的 Gradio 版已移除）。
echo.

rem  加 -W 是压掉 pydub 那条 "Couldn't find ffmpeg" 的 RuntimeWarning：
rem  OmniVoice 用 pydub 只做内存转换，不读文件，用不到 ffmpeg，那条警告没意义。
"%PY%" -s -W ignore::RuntimeWarning:pydub.utils "%ENTRY%"
set "CODE=%ERRORLEVEL%"

echo.
if not "%CODE%"=="0" echo [异常退出] 退出码 %CODE%，请查看上方错误信息。
echo 服务已退出。
pause
