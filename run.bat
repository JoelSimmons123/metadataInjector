@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  py -m venv .venv || exit /b 1
  call .venv\Scripts\activate.bat
  python -m pip install --upgrade pip || exit /b 1
  pip install -r requirements.txt || exit /b 1
) else (
  call .venv\Scripts\activate.bat
)

if exist ".venv\Scripts\pythonw.exe" (
  start "" .venv\Scripts\pythonw.exe app_unified.py
) else (
  start "" .venv\Scripts\python.exe app_unified.py
)

exit /b 0
