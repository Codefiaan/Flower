@echo off
REM Flower Terminal - Windows launcher. Double-click to start, then open http://127.0.0.1:8000
cd /d "%~dp0"
if not exist .venv (
    echo Creating virtual environment...
    py -3 -m venv .venv || python -m venv .venv
    call .venv\Scripts\activate.bat
    python -m pip install --upgrade pip
    pip install -r requirements.txt
) else (
    call .venv\Scripts\activate.bat
)
start "" http://127.0.0.1:8000
python -m backend
pause
