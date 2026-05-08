@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0"

echo ========================================
echo  AI Bridge - Windows Build Script
echo ========================================
echo.

where py >nul 2>nul
if %errorlevel%==0 (
    set "PY=py -3"
) else (
    set "PY=python"
)

echo [1/4] 安装依赖...
%PY% -m pip install --upgrade pip
%PY% -m pip install -e ".[build]"
if errorlevel 1 (
    echo [错误] 依赖安装失败
    pause
    exit /b 1
)

echo [2/4] 清理旧构建...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [3/4] 打包 exe (PyInstaller)...
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
