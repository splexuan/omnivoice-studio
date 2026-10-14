@echo off
rem 本文件必须是 GBK 编码 + CRLF 换行，且【不要 chcp 65001】——
rem 实测：UTF-8 + chcp 会让 cmd 把中文行从半截截断，报 not recognized。
cd /d "%~dp0"

rem ---- 国内环境：HuggingFace 走镜像（仅当需要联网下载时才生效）----
set "HF_ENDPOINT=https://hf-mirror.com"

echo ============================================
echo   OmniVoice Studio - 一键环境搭建
echo   自动识别显卡，装对应版本的 torch
echo ============================================
echo.

where py >nul 2>nul && goto :usepy
where python >nul 2>nul && goto :usepy2

echo [错误] 没找到 Python。
echo   请先安装 Python 3.12（https://www.python.org/downloads/ ），
echo   或执行 "pip install uv" 后重试。
pause
exit /b 1

:usepy
set "PYCMD=py -3"
goto :run

:usepy2
set "PYCMD=python"

:run
%PYCMD% bootstrap.py %*
echo.
echo 流程结束（若上方有报错，请按提示处理后重跑本脚本，已下好的文件会自动跳过）。
pause
