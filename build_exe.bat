@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  py -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt

pyinstaller --noconfirm --clean --windowed --name MetadataRepairTool app_unified.py
if errorlevel 1 goto :fail

REM Copy ExifTool runtime beside the built app when it exists in the project folder.
if exist "exiftool.exe" copy /Y "exiftool.exe" "dist\MetadataRepairTool\exiftool.exe" >nul
if exist "exiftool(-k).exe" if not exist "dist\MetadataRepairTool\exiftool.exe" copy /Y "exiftool(-k).exe" "dist\MetadataRepairTool\exiftool(-k).exe" >nul
if exist "exiftool_files" xcopy /E /I /Y "exiftool_files" "dist\MetadataRepairTool\exiftool_files" >nul

REM Keep trusted default references beside the built EXE.
REM HEIC image references and MOV video references are supported and preferred.
if exist "good images" xcopy /E /I /Y "good images" "dist\MetadataRepairTool\good images" >nul

echo.
echo Built: dist\MetadataRepairTool\MetadataRepairTool.exe
echo Includes: metadata repair + optional image SynthID cleanup + Topaz video enhancement.
echo.
if exist "dist\MetadataRepairTool\good images" (
  echo Default reference folder copied: dist\MetadataRepairTool\good images
) else (
  echo NOTE: No "good images" folder was found at build time.
  echo Create "good images" beside the EXE and put a trusted image/video reference in it.
)
echo.
echo NOTE: Topaz Video is NOT bundled. The app auto-detects an installed Topaz Video ffmpeg/models folder.
echo NOTE: remove-ai-watermarks is NOT bundled. Install it separately if you want image SynthID cleanup.
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
