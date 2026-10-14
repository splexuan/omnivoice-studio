@echo off
setlocal
rem ============================================================
rem  OmniVoice Studio 整合包 -^> zip
rem
rem  本文件放在项目根目录（和 run_web.bat 并列）。
rem  双击即可生成 OmniVoice-Studio.zip，发给别人用。
rem  也可以把任意文件夹直接拖到本文件上，压那一个。
rem
rem  它会自动做两件事，你不用记顺序：
rem    ① 还没有整合包（..\dist\OmniVoice-Studio 不存在）就先调
rem       packaging\assemble.py 组装一份（要复制约 11 GB，2 分钟左右）；
rem       已经组装过就直接用，不重复组装。
rem    ② 用项目自带的 tools\7zip\7za.exe 压成 zip
rem       （不依赖系统装没装 7-Zip）。
rem
rem  !! 本文件保存为 GBK 编码 + CRLF 换行，且特意【不加 chcp】!!
rem
rem  实测（本机稳定复现 3 轮）：同一个文件若存成 UTF-8 并写
rem  "chcp 65001"，cmd 读取它时会发生位置错乱，把中间的中文提示行
rem  从半截截断，报出 "'。10' is not recognized..." 之类的怪错。
rem  GBK 编码不改变代码页，中文正常、零报错。
rem
rem  也就是说：改这个文件时，编辑器要选 GBK/ANSI 保存，
rem  不要"顺手"改成 UTF-8，也不要加 chcp 65001。
rem ============================================================
cd /d "%~dp0"

echo ============================================================
echo   OmniVoice Studio 整合包  -^>  zip
echo ============================================================
echo.

set "ZIP7=%~dp0tools\7zip\7za.exe"
set "PKG=%~dp0..\dist\OmniVoice-Studio"

if not exist "%ZIP7%" (
  echo [错误] 找不到自带的 7-Zip 压缩器：
  echo        %ZIP7%
  echo.
  echo 请确认 tools\7zip\7za.exe 还在项目里。
  echo.
  pause
  exit /b 1
)

rem ---- 要压缩哪个文件夹 ----
rem  ① 拖到本文件上的文件夹优先（这种情况只压缩、不组装）
rem  ② 否则用 assemble.py 组装出来的包；没组装过就先组装一份
set "SRC=%~1"
set "NEED_BUILD=0"
if not defined SRC (
  set "SRC=%PKG%"
  set "NEED_BUILD=1"
)
rem  已经组装过了（包是完整的）就不用重做
if "%NEED_BUILD%"=="1" if exist "%PKG%\web\server.py" if exist "%PKG%\runtime\python.exe" set "NEED_BUILD=0"

if "%NEED_BUILD%"=="1" (
  echo   本地还没有整合包，先自动组装一份。
  echo   要复制约 11 GB，大概 2 分钟，请稍等……
  echo.
  "%~dp0runtime\python.exe" "%~dp0packaging\assemble.py" --clean
  if errorlevel 1 goto :buildfail
  echo.
)

if "%SRC:~-1%"=="\" set "SRC=%SRC:~0,-1%"

if not exist "%SRC%\" (
  echo [错误] 找不到要压缩的文件夹：
  echo        %SRC%
  echo.
  echo 也可以直接把 OmniVoice-Studio 文件夹拖到本文件上面。
  echo.
  pause
  exit /b 1
)

rem 规范化成绝对路径，免得输出里出现一堆 "..\"
for %%I in ("%SRC%") do set "FQ=%%~fI"
for %%I in ("%FQ%") do (
  set "NAME=%%~nxI"
  set "PARENT=%%~dpI"
)
set "OUT=%PARENT%%NAME%.zip"

echo   要压缩的文件夹 : %FQ%
echo                    ^(assemble.py 组装出的「解压即用」包：运行时、
echo                     模型权重、音色都已在包内，对方零下载^)
echo   输出文件       : %OUT%
echo   压缩器         : 自带 7-Zip 26.04
echo.

if exist "%OUT%" (
  echo   [注意] 输出文件已经存在，继续会覆盖它。
  choice /c yn /n /m "  确定继续吗？[Y/N] "
  if errorlevel 2 (
    echo.
    echo   已取消，什么都没做。
    echo.
    pause
    exit /b 0
  )
  echo.
)

echo   正在压缩，请勿关闭本窗口……
echo   窗口里会显示百分比进度。10 G 的包大约要 5 - 15 分钟。
echo.

rem  -tzip   压成 zip（Windows 双击就能解，对方不用装任何东西）
rem  -mx=5   压缩级别：正常（dll 本来就压过，再往上收益很小、耗时剧增）
rem  -mmt=on 多线程
rem  -mcu=on 文件名用 UTF-8 并打标志位 —— 不加这条，音色名里的中文
rem           （比如「于谦.wav」）在英文系统 / macOS 上解压会乱码
"%ZIP7%" a -tzip -mx=5 -mmt=on -mcu=on "%OUT%" "%FQ%"

if errorlevel 1 goto :failed
if not exist "%OUT%" goto :failed

echo.
echo ============================================================
echo   压缩完成
for /f "usebackq delims=" %%S in (`powershell -NoProfile -Command "'{0:N2}' -f ((Get-Item -LiteralPath '%OUT%').Length/1GB)"`) do set "SZGB=%%S"
if defined SZGB echo   体积 : %SZGB% GB
echo   文件 : %OUT%
echo ============================================================
echo.
echo   这个 zip 可以直接发给别人：对方解压出来双击「启动程序.bat」就能用。
echo.
pause
exit /b 0

:buildfail
echo.
echo ============================================================
echo   [失败] 组装整合包没成功，压缩没法继续。
echo   上面有 assemble.py 的报错信息，常见原因：
echo     - runtime\ 或 checkpoints\ 缺失（组装时这两个缺一不可）
echo     - 磁盘空间不够（组装大约要 11 GB）
echo ============================================================
echo.
pause
exit /b 1

:failed
echo.
echo ============================================================
echo   [失败] 压缩没有正常完成。常见原因：
echo     - 磁盘空间不够（zip 大约需要 4 - 7 GB 空闲空间）
echo     - 目标 zip 正被别的程序占用（先把它关掉/移走）
echo     - 源文件夹里有文件正被占用（先关掉正在用它的程序）
echo     - 源文件夹被中断过、有半截文件
echo ============================================================
echo.
pause
exit /b 1
