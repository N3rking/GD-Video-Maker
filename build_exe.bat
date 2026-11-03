@echo off
rem Rebuilds "GD Video Maker.exe". Run install_prerequisites.bat first.
rem ffmpeg.exe must be on PATH; it gets bundled into the app.
cd /d "%~dp0"
set FFMPEG=
for /f "delims=" %%f in ('where ffmpeg 2^>nul') do if not defined FFMPEG set "FFMPEG=%%f"
if not defined FFMPEG (
  echo ffmpeg.exe wasn't found. Run install_prerequisites.bat, then open a new window.
  pause & exit /b 1
)
python -m PyInstaller --noconfirm --windowed --onedir --name "GD Video Maker" --icon "%~dp0icon.ico" ^
  --distpath dist --workpath build_tmp --specpath build_tmp ^
  --add-binary "%FFMPEG%;." --add-data "%~dp0verified_objects.json;." --add-data "%~dp0icon.ico;." --add-data "%~dp0CREDITS.txt;." ^
  --hidden-import fit_sharp --hidden-import fit_objects --hidden-import fit_shapes --hidden-import build_level --hidden-import build_library ^
  --exclude-module matplotlib --exclude-module PIL gd_video_maker.py
if errorlevel 1 (pause & exit /b 1)
echo Built into %~dp0dist\GD Video Maker
pause
