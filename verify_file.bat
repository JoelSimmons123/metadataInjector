@echo off
setlocal
cd /d "%~dp0"
set "EXIF=exiftool.exe"
if not exist "%EXIF%" set "EXIF=exiftool(-k).exe"
if not exist "%EXIF%" (
  echo ExifTool was not found beside this file.
  echo Put exiftool.exe and exiftool_files in this folder first.
  pause
  exit /b 1
)
if "%~1"=="" (
  echo Drag an image or video file onto verify_file.bat to inspect its metadata locally.
  echo.
  pause
  exit /b 0
)
echo =============================================================
echo File: %~1
echo =============================================================
"%EXIF%" -G1 -a -s "%~1"
echo.
pause
