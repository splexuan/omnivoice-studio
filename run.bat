@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem ---- 国内环境：HuggingFace 走镜像（仅当需要联网下载时才生效）----
set "HF_ENDPOINT=https://hf-mirror.com"

echo ============================================
echo   OmniVoice Studio - 本地语音合成工具
echo ============================================
echo.

"%~dp0.venv\Scripts\python.exe" "%~dp0app\app.py"

echo.
echo 服务已退出。
pause
