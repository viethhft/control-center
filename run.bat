@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  py -3.10 -m venv .venv
  .venv\Scripts\python.exe -m pip install -r requirements.txt
)
echo.
echo Control Center dang lang nghe tren tat ca card mang.
echo Mo tren may nay: http://127.0.0.1:7999
echo May khac trong LAN: http://IP-CUA-MAY-CHU:7999
echo Luu y: 0.0.0.0 la dia chi bind, khong phai URL de mo trong trinh duyet.
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Start-Process 'http://127.0.0.1:7999'"
.venv\Scripts\python.exe -m uvicorn app:app --host 0.0.0.0 --port 7999
