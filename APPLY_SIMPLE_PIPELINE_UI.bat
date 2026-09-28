@echo off
setlocal
cd /d "%~dp0"

if not exist "app_unified.py" (
  echo ERROR: Put this patch in the metadataInjector repo root.
  pause
  exit /b 1
)

copy /Y "video_pipeline.py" "video_pipeline.py" >nul 2>&1

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" APPLY_SIMPLE_PIPELINE_UI.py
) else (
    py APPLY_SIMPLE_PIPELINE_UI.py
)

if errorlevel 1 (
    echo.
    echo Patch was NOT applied.
    pause
    exit /b 1
)

echo.
echo Done. Start normally with run.bat.
pause
