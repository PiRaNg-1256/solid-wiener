@echo off
rem One-time setup: double-click this file (or run it in a terminal).
cd /d "%~dp0"
set "PYCMD="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >/dev/null 2>nul
if not errorlevel 1 set "PYCMD=py -3"
if not defined PYCMD (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >/dev/null 2>nul
  if not errorlevel 1 set "PYCMD=python"
)
if not defined PYCMD (
  echo Python 3.10 or newer was not found. Install it first by running:
  echo     winget install Python.Python.3.12
  echo Then close this window, open a new one and run setup.cmd again.
  pause
  exit /b 1
)
%PYCMD% tools\setup_windows.py
pause
