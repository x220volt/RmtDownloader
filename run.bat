@echo off
cd /d "%~dp0"
chcp 65001 > nul
title 원격 다운로더 (Remote Downloader) 서버

echo ========================================================
echo   🚀 원격 다운로더 (Remote Downloader)를 시작합니다...
echo ========================================================
echo.

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" app.py
) else (
    python app.py
)

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [오류 발생] 파이썬 실행 중 오류가 발생했습니다.
    pause
)
