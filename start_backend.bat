@echo off
cd /d "%~dp0backend"
echo [V2X Lab] Starting FastAPI backend on http://localhost:8001
set "RELOAD_FLAG="
if "%V2X_RELOAD%"=="1" set "RELOAD_FLAG=--reload"
if exist ".venv\Scripts\python.exe" (
  .venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8001 %RELOAD_FLAG%
) else (
  python -m uvicorn main:app --host 127.0.0.1 --port 8001 %RELOAD_FLAG%
)
pause
