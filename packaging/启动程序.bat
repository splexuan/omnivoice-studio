@echo off
rem 本文件必须是 GBK 编码 + CRLF 换行，且【不要 chcp 65001】——
rem 实测：UTF-8 + chcp 会让 cmd 把中文行从半截截断，报 not recognized。
setlocal
cd /d "%~dp0"

set "PY=%~dp0runtime\python.exe"

if not exist "%PY%" (
  echo.
  echo [错误] 未找到内置 Python 运行时：
  echo        %PY%
  echo.
  echo 请确认把整个文件夹完整解压出来（不要只解压其中一部分）。
  echo.
  pause
  exit /b 1
)

echo ============================================================
echo    OmniVoice Studio  -  本地语音合成（离线即用）
echo ============================================================
echo    内置运行时 : %PY%
echo    模型权重   : %~dp0checkpoints
echo.
echo    首次启动需加载模型，约 10 - 40 秒，请耐心等待…
echo    启动完成后，用浏览器打开： http://127.0.0.1:7866
echo    （一般会自动弹出）
echo.
echo    关闭本窗口即可退出程序。
echo ============================================================
echo.

rem  加 -W 是压掉 pydub 那条 "Couldn't find ffmpeg" 的 RuntimeWarning：
rem  OmniVoice 用 pydub 只做内存转换，不读文件，用不到 ffmpeg，那条警告没意义。
"%PY%" -s -W ignore::RuntimeWarning:pydub.utils "%~dp0web\server.py"

if errorlevel 1 (
  echo.
  echo [异常] 程序退出，错误码 %errorlevel%。
  echo 若为显存不足，可在界面里调低「推理步数」，或把 config 里 DEVICE 改成 cpu。
  echo.
  pause
)
endlocal
