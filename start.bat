@echo off
REM Flower Terminal - Windows launcher. Double-click to start; the browser opens http://127.0.0.1:8000
setlocal
cd /d "%~dp0"

REM Ignore any other Python environment that happens to be active on this PC.
set "VIRTUAL_ENV="
set "PYTHONHOME="
set "PYTHONPATH="
set "VENV_PY=%~dp0.venv\Scripts\python.exe"

if exist "%VENV_PY%" (
    "%VENV_PY%" -c "import fastapi, uvicorn, pandas, yfinance, httpx, pypdf" >nul 2>&1 && goto run
    echo The .venv folder is incomplete - installing the missing packages...
    goto install
)

echo Creating the app's own Python environment in .venv ...
where py >nul 2>&1 && py -3 -m venv .venv
if not exist "%VENV_PY%" python -m venv .venv
if not exist "%VENV_PY%" (
    echo.
    echo ERROR: could not create .venv.
    echo Install Python 3.11 or newer from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH" in the installer. Then run start.bat again.
    pause
    exit /b 1
)

:install
"%VENV_PY%" -c "import sys; sys.exit(sys.version_info < (3, 10))"
if errorlevel 1 (
    echo.
    echo ERROR: Python 3.10 or newer is required. Delete the .venv folder, install a newer Python and try again.
    pause
    exit /b 1
)
"%VENV_PY%" -m pip install --upgrade pip
"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo ERROR: installing the packages failed - see the messages above.
    pause
    exit /b 1
)

:run
REM Open the browser a few seconds later, once the server is up.
start "" cmd /c "timeout /t 4 /nobreak >nul & start http://127.0.0.1:8000"
"%VENV_PY%" -m backend
pause
