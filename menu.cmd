@echo off
rem Double-click for a simple menu: work through clips.xlsx, make clips from a link, check the setup, update.
setlocal
set "HERE=%~dp0"
if not exist "%HERE%.venv\Scripts\python.exe" (
  echo Setup has not been run yet. Double-click setup.cmd first.
  pause
  exit /b 1
)
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set HF_HUB_DISABLE_SYMLINKS_WARNING=1
"%HERE%.venv\Scripts\python.exe" -m clipper menu
pause
