@echo off
REM Flower Terminal - Windows launcher. Double-click to start; the browser opens http://127.0.0.1:8000
REM "start.bat check [options]" sets everything up and runs the health check instead (used by check.bat and CI).
setlocal
cd /d "%~dp0"
set "PAUSE=pause"
set "MODE=run"
if /i "%~1"=="check" (
    set "MODE=check"
    set "PAUSE=rem"
)

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
    %PAUSE%
    exit /b 1
)

:install
"%VENV_PY%" -c "import sys; sys.exit(sys.version_info < (3, 10))"
if errorlevel 1 (
    echo.
    echo ERROR: Python 3.10 or newer is required. Delete the .venv folder, install a newer Python and try again.
    %PAUSE%
    exit /b 1
)
"%VENV_PY%" -m pip install --upgrade pip
"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo ERROR: installing the packages failed - see the messages above.
    %PAUSE%
    exit /b 1
)

:run
if not "%MODE%"=="check" goto serve
REM (kept outside a parenthesized block so %errorlevel% is read after the check has run)
"%VENV_PY%" -m backend.check %2 %3 %4 %5 %6
exit /b %errorlevel%

:serve
REM Open the browser a few seconds later, once the server is up.
start "" cmd /c "timeout /t 4 /nobreak >nul & start http://127.0.0.1:8000"
"%VENV_PY%" -m backend
pause
