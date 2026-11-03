@echo off
rem Installs what's needed to run GD Video Maker from source or rebuild the .exe:
rem Python 3, FFmpeg, NumPy and PyInstaller. The finished .exe needs none of this.
rem Uses winget (built into Windows 10/11) for Python and FFmpeg, and pip for the Python packages.
setlocal
cd /d "%~dp0"

where winget >nul 2>nul
if errorlevel 1 (
  echo winget isn't available. Install "App Installer" from the Microsoft Store, then run this again.
  pause & exit /b 1
)

call :find_python
if not defined PY (
  echo Installing Python 3.13...
  winget install -e --id Python.Python.3.13 --scope user --accept-package-agreements --accept-source-agreements
  call :find_python
)
if not defined PY (
  echo Python was installed but this window can't see it yet. Close this window and run the file again.
  pause & exit /b 1
)
echo Using Python: %PY%

where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo Installing FFmpeg...
  winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
) else (
  echo FFmpeg is already installed.
)

echo Installing NumPy and PyInstaller...
"%PY%" -m pip install --upgrade pip
"%PY%" -m pip install numpy pyinstaller
if errorlevel 1 (
  echo pip failed. Check your internet connection and try again.
  pause & exit /b 1
)

echo.
echo Done. Open a NEW terminal so FFmpeg is on PATH, then either:
echo   python gd_video_maker.py      to run the app from source
echo   build_exe.bat                 to rebuild GD Video Maker.exe
pause
exit /b 0

:find_python
set PY=
for /f "delims=" %%p in ('where python 2^>nul') do (
  rem Skip the Microsoft Store placeholder, which only opens the Store.
  echo %%p | find /i "WindowsApps" >nul || if not defined PY set "PY=%%p"
)
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
exit /b 0
