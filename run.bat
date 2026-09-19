@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Creating Python environment...
  py -m venv .venv
  if errorlevel 1 goto :fail_setup
  call .venv\Scripts\activate.bat
  python -m pip install --upgrade pip
  if errorlevel 1 goto :fail_setup
  pip install -r requirements.txt
  if errorlevel 1 goto :fail_setup
) else (
  call .venv\Scripts\activate.bat
)

echo Starting Metadata Repair Tool...
echo Crash/output log: %CD%\last_run.log
echo.

python -X faulthandler app_plus.py > last_run.log 2>&1
set "APPERR=%ERRORLEVEL%"

if not "%APPERR%"=="0" (
  echo.
  echo ============================================================
  echo APP CRASHED - exit code %APPERR%
  echo ============================================================
  echo.
  type last_run.log
  echo.
  echo Full log saved to:
  echo %CD%\last_run.log
  echo.
  pause
  exit /b %APPERR%
)

exit /b 0

:fail_setup
echo.
echo ============================================================
echo SETUP FAILED
 echo ============================================================
echo.
echo The Python environment/dependencies could not be installed.
echo.
pause
exit /b 1
