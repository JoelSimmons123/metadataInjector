@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  py -m venv .venv || exit /b 1
  call .venv\Scripts\activate.bat
  python -m pip install --upgrade pip || exit /b 1
) else (
  call .venv\Scripts\activate.bat
)

rem v2.10 caption dependencies are intentionally installed once into the existing venv.
rem The marker prevents a package check/download on every normal launch.
if not exist ".venv\.deps_v2_10_1" (
  echo Installing/updating v2.10.1 dependencies ^(includes GPU Whisper runtime; first run is large^) ...
  python -m pip install --upgrade pip setuptools wheel || exit /b 1
  pip install -r requirements.txt || exit /b 1
  > ".venv\.deps_v2_10_1" echo installed
)

if exist ".venv\Scripts\pythonw.exe" (
  start "" .venv\Scripts\pythonw.exe app_unified.py
) else (
  start "" .venv\Scripts\python.exe app_unified.py
)

exit /b 0
