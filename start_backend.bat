@echo off
cd /d "%~dp0backend"
echo [V2X Lab] Starting FastAPI backend on http://localhost:8001
python -m uvicorn main:app --host 127.0.0.1 --port 8001 --reload
pause
