@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    set "PY=py -3"
) else (
    set "PY=python"
)

echo ========================================
echo  GLM Bridge - Dev Mode
echo ========================================
echo.
echo Python:
%PY% --version
echo.

echo [1/2] 检查依赖...
%PY% -c "import fastapi, uvicorn, httpx, webview" 2>nul
if errorlevel 1 (
    echo     缺少依赖，开始安装...
    %PY% -m pip install --upgrade pip
    %PY% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [错误] 依赖安装失败
        pause
        exit /b 1
    )
) else (
    echo     依赖已齐备
)

echo.
echo [2/2] 启动应用...
echo （任何报错都会显示在下面，不会自动关闭窗口）
echo ========================================
echo.

%PY% app.py

echo.
echo ========================================
echo 应用已退出（exit code: %errorlevel%）
echo ========================================
pause
