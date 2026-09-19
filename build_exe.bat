@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  py -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt

pyinstaller --noconfirm --clean --windowed --name MetadataRepairTool app_plus.py
if errorlevel 1 goto :fail

REM Copy ExifTool runtime beside the built app when it exists in the project folder.
if exist "exiftool.exe" copy /Y "exiftool.exe" "dist\MetadataRepairTool\exiftool.exe" >nul
if exist "exiftool(-k).exe" if not exist "dist\MetadataRepairTool\exiftool.exe" copy /Y "exiftool(-k).exe" "dist\MetadataRepairTool\exiftool(-k).exe" >nul
if exist "exiftool_files" xcopy /E /I /Y "exiftool_files" "dist\MetadataRepairTool\exiftool_files" >nul

echo.
echo Built: dist\MetadataRepairTool\MetadataRepairTool.exe
echo NOTE: Topaz Video is NOT bundled. The app auto-detects an installed Topaz Video ffmpeg/models folder.
if exist "dist\MetadataRepairTool\exiftool.exe" (
  echo ExifTool copied into the build.
) else if exist "dist\MetadataRepairTool\exiftool(-k).exe" (
  echo ExifTool copied into the build.
) else (
  echo NOTE: ExifTool was not found in the project folder.
  echo Put exiftool.exe and its exiftool_files folder beside the built EXE,
  echo or copy them into this project folder and run build_exe.bat again.
)
pause
exit /b 0

:fail
echo.
echo Build failed.
pause
exit /b 1
