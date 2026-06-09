@echo off
cd /d "%~dp0"
echo [V2X Lab] Opening frontend at frontend/index.html
start "" "http://localhost:8001/app/index.html"
echo.
echo If the backend is not running, start it first with start_backend.bat.
echo Note: direct file:// open won't work (CORS). Run through the backend server.
pause
