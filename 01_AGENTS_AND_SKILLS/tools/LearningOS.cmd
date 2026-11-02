@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo LearningOS needs Python 3.11 or later. Install Python and try again.
  pause
  exit /b 6
)
if /i "%~1"=="menu" (
  python launcher.py --menu
) else (
  python launcher.py
)
if errorlevel 1 pause
endlocal
