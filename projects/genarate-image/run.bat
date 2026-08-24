@echo off
setlocal
cd /d "%~dp0"
set OLLAMA_STORY_MODEL=qwen3.5:27b
if not exist .venv\Scripts\python.exe (
  py -3.10 -m venv .venv
  .venv\Scripts\python.exe -m pip install -r requirements.txt
)
.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8010
