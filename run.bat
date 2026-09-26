@echo off
rem Sim vs Reality - start the dashboard
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Creating virtual environment...
  python -m venv .venv
  .venv\Scripts\python.exe -m pip install -r requirements.txt
)
echo Starting Sim vs Reality on http://localhost:8000
.venv\Scripts\python.exe -m uvicorn backend.app:app --host 0.0.0.0 --port 8000
