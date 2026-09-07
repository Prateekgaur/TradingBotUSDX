@echo off
REM Trading Lab - starts the local web app at http://127.0.0.1:8000
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo First run: creating a virtual environment and installing dependencies...
  python -m venv .venv || goto :fail
  .venv\Scripts\python.exe -m pip install --upgrade pip >nul
  .venv\Scripts\python.exe -m pip install -r requirements.txt || goto :fail
)

echo.
echo   Trading Lab  ->  http://127.0.0.1:8000
echo   Paper simulation only. No broker is connected.
echo   Press Ctrl+C to stop.
echo.
start "" http://127.0.0.1:8000
.venv\Scripts\python.exe -m uvicorn app.api:app --host 127.0.0.1 --port 8000
goto :eof

:fail
echo.
echo Setup failed. Is Python 3.11+ installed and on PATH?
pause
