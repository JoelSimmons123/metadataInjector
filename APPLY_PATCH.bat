@echo off
setlocal
cd /d "%~dp0"

echo This patch is meant to be copied INTO the root of metadataInjector.
echo.
if not exist "app.py" (
  echo ERROR: app.py is not in this folder.
  echo Extract/copy this patch into your metadataInjector folder first, then run again.
  pause
  exit /b 1
)

if not exist "captioning.py" (
  echo ERROR: captioning.py is missing from this patch.
  pause
  exit /b 1
)

echo Patch files are already in place.
echo Starting the app; run.bat will install the v2.10.1 dependencies once.
call run.bat
exit /b %errorlevel%
