@echo off
REM Flower health check: tests your installation and live data, writes check-report.txt.
REM Double-click it. If anything is FAIL, send check-report.txt for support.
cd /d "%~dp0"
call "%~dp0start.bat" check --live --report check-report.txt %*
set "RC=%errorlevel%"
echo.
if "%RC%"=="0" (
    echo All checks passed.
) else (
    echo Some checks failed. The details are in check-report.txt in this folder.
)
pause
exit /b %RC%
