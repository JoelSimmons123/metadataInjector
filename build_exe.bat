@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  py -m venv .venv || exit /b 1
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip || exit /b 1
pip install -r requirements.txt || exit /b 1

pyinstaller --noconfirm --clean --windowed --name MetadataRepairTool app_unified.py
if errorlevel 1 exit /b 1

if exist "exiftool.exe" copy /Y "exiftool.exe" "dist\MetadataRepairTool\exiftool.exe" >nul
if exist "exiftool(-k).exe" if not exist "dist\MetadataRepairTool\exiftool.exe" copy /Y "exiftool(-k).exe" "dist\MetadataRepairTool\exiftool(-k).exe" >nul
if exist "exiftool_files" xcopy /E /I /Y "exiftool_files" "dist\MetadataRepairTool\exiftool_files" >nul
if exist "good images" xcopy /E /I /Y "good images" "dist\MetadataRepairTool\good images" >nul
if exist "ffmpeg.exe" copy /Y "ffmpeg.exe" "dist\MetadataRepairTool\ffmpeg.exe" >nul
if exist "ffprobe.exe" copy /Y "ffprobe.exe" "dist\MetadataRepairTool\ffprobe.exe" >nul

echo Built: dist\MetadataRepairTool\MetadataRepairTool.exe
exit /b 0
