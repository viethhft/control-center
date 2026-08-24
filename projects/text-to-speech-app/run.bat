@echo off
title Text-to-Speech Web Studio
echo ===================================================
echo     KHOI DONG TEXT-TO-SPEECH WEB STUDIO (AI TTS)
echo ===================================================
echo.

cd /d "%~dp0"

echo [1/2] Kiem tra moi truong Python...
set "PROJECT_PYTHON=%~dp0.venv\Scripts\python.exe"
"%PROJECT_PYTHON%" --version >nul 2>&1
if errorlevel 1 (
    echo [LOI] Khong tim thay .venv! Chay: python -m venv .venv
    echo Sau do chay: .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

echo [2/2] Dang khoi chay may chu Web tai: http://127.0.0.1:8000 ...
echo Nh?n Ctrl+C de dung may chu.
echo.

"%PROJECT_PYTHON%" -m uvicorn app:app --host 127.0.0.1 --port 8000 --reload
pause
