@echo off
rem Double-click to work through clips.xlsx (close the sheet in Excel first).
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
"%HERE%.venv\Scripts\python.exe" -m clipper run
pause
