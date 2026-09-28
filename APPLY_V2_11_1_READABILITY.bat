@echo off
setlocal
cd /d "%~dp0"

if not exist "video_pipeline.py" (
  echo ERROR: Put this patch in the metadataInjector repo root.
  pause
  exit /b 1
)

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" APPLY_V2_11_1_READABILITY.py
) else (
    py APPLY_V2_11_1_READABILITY.py
)

if errorlevel 1 (
    echo.
    echo Patch was NOT applied.
    pause
    exit /b 1
)

echo.
echo Done. Launch normally with run.bat.
pause
