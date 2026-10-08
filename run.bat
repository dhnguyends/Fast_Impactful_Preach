@echo off
cd /d "%~dp0"
if not exist .venv (
  py -3.11 -m venv .venv || python -m venv .venv
  .venv\Scripts\python -m pip install -r requirements.txt
)
start "" http://127.0.0.1:8000
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
