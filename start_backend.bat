@echo off
cd /d "%~dp0backend"
rem Force UTF-8: on ko-KR Windows the console defaults to cp949, and printing a
rem non-cp949 char (e.g. the em dash in [SIM] logs) raises UnicodeEncodeError,
rem which kills the whole simulation thread.
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
echo [V2X Lab] Starting FastAPI backend on http://localhost:8001
set "RELOAD_FLAG="
if "%V2X_RELOAD%"=="1" set "RELOAD_FLAG=--reload"
if exist ".venv\Scripts\python.exe" (
  .venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8001 %RELOAD_FLAG%
) else (
  python -m uvicorn main:app --host 127.0.0.1 --port 8001 %RELOAD_FLAG%
)
pause
