@echo off
rem Double-click to start the app. The first run sets everything up (about a minute).
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto run
echo Setting up the app for the first time (about a minute)...
py -3 -m venv .venv 2>nul || python -m venv .venv
if not exist ".venv\Scripts\python.exe" (
  echo The app needs Python. Opening the download page...
  echo Install Python ^(tick "Add python.exe to PATH"^), then double-click Start again.
  start https://www.python.org/downloads/
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
  rmdir /s /q .venv
  echo Setup didn't finish. Check your internet connection and try again.
  pause
  exit /b 1
)
:run
".venv\Scripts\python.exe" start.py --open
if errorlevel 1 pause
