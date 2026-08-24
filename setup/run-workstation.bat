@echo off
setlocal
cd /d "%~dp0..\projects\genarate-image"
if not exist .env copy /Y .env.example .env >nul
if not exist .venv\Scripts\python.exe py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8010
endlocal
