@echo off
rem One-time setup for Shot Picker: Python venv + packages + libmpv (the video player DLL).
rem Needs: Windows 10/11, Python 3.12 (py launcher), ffmpeg/ffprobe on PATH, internet.
cd /d "%~dp0"
if not exist .venv (
  py -3.12 -m venv .venv || goto :fail
)
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt || goto :fail
if not exist lib\libmpv-2.dll (
  echo Downloading libmpv ^(shinchiro build linked from mpv.io, ~30 MB^)...
  if not exist lib mkdir lib
  curl -L --fail -o lib\mpv-dev.7z https://github.com/shinchiro/mpv-winbuild-cmake/releases/download/20261012/mpv-dev-x86_64-20261012-git-88eb9f2e73.7z || goto :fail
  %SystemRoot%\System32\tar.exe -xf lib\mpv-dev.7z -C lib libmpv-2.dll || goto :fail
  del lib\mpv-dev.7z
)
where ffmpeg >nul 2>&1 || echo WARNING: ffmpeg is not on PATH - export and stabilize need it.
echo.
echo Setup done. Start the app with ShotPicker.bat
echo (Optional: setup_voice.bat for Audio ^> Enhance voice, ~3 GB.)
exit /b 0
:fail
echo.
echo Setup failed - see the messages above.
exit /b 1
