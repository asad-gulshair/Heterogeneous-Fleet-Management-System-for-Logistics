@echo off
REM One-click start for TusCore (Windows). Creates the venv on first run, installs requirements, starts the server.
cd /d "%~dp0backend"
if not exist .venv (
  echo Creating virtual environment...
  python -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install -q -r requirements.txt
echo.
echo TusCore running at http://localhost:8000   (API docs: http://localhost:8000/docs)
echo.
python -m uvicorn app.main:app --reload --reload-dir app --reload-dir ../config
