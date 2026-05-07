@echo off
chcp 65001 >nul
setlocal

echo ========================================
echo  AI Bridge - Windows Build Script
echo ========================================
echo.

REM 优先用 py launcher，其次 python
where py >nul 2>nul
if %errorlevel%==0 (
    set "PY=py -3"
) else (
    set "PY=python"
)

echo [1/3] 安装依赖...
%PY% -m pip install --upgrade pip
%PY% -m pip install -r requirements.txt
if errorlevel 1 (
    echo [错误] 依赖安装失败
    pause
    exit /b 1
)

echo [2/3] 清理旧构建...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [3/3] 打包 exe (PyInstaller)...
%PY% -m PyInstaller AIBridge.spec --clean --noconfirm
if errorlevel 1 (
    echo [错误] 打包失败
    pause
    exit /b 1
)

echo.
echo ========================================
echo  打包完成！
echo  exe 路径: dist\AIBridge.exe
echo ========================================
echo.

if exist dist\AIBridge.exe (
    explorer dist
)
pause
