@echo off
rem Sim vs Reality - start the dashboard (Windows)
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install Python 3.10 or newer from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" in the installer, then run this again.
  pause
  exit /b 1
)

if not exist .venv\Scripts\python.exe (
  echo First run: creating a private Python environment and installing dependencies...
  echo This takes a few minutes and only happens once.
  python -m venv .venv || goto :fail
  .venv\Scripts\python.exe -m pip install --upgrade pip >nul
  .venv\Scripts\python.exe -m pip install -r requirements.txt || goto :fail
)

echo.
echo If Windows asks whether to allow Python on networks, tick PRIVATE networks
echo and click Allow access - otherwise the console's telemetry is blocked.
echo.
.venv\Scripts\python.exe -m backend %*
exit /b %errorlevel%

:fail
echo.
echo Setup failed. Check your internet connection and try again.
pause
exit /b 1
