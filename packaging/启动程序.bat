@echo off
chcp 65001 >nul
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
echo    启动完成后，用浏览器打开： http://127.0.0.1:7865
echo    （一般会自动弹出）
echo.
echo    关闭本窗口即可退出程序。
echo ============================================================
echo.

"%PY%" "%~dp0app\app.py"

if errorlevel 1 (
  echo.
  echo [异常] 程序退出，错误码 %errorlevel%。
  echo 若为显存不足，可在界面里调低「推理步数」，或把 config 里 DEVICE 改成 cpu。
  echo.
  pause
)
endlocal
