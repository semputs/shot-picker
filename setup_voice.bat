@echo off
rem One-time setup for Audio > Enhance voice (ClearerVoice). Makes .venv-voice next to this file.
rem Needs Python 3.12 (py launcher) and internet (PyTorch ~2.5 GB, models ~0.5 GB from HuggingFace).
cd /d "%~dp0"
if not exist .venv-voice (
  py -3.12 -m venv .venv-voice || goto :fail
)
set PY=.venv-voice\Scripts\python.exe
%PY% -m pip install --upgrade pip
rem NVIDIA card: CUDA build of PyTorch (falls back to the processor when no card is found)
nvidia-smi >nul 2>&1
if %errorlevel%==0 (
  %PY% -m pip install torch torchaudio torchvision --index-url https://download.pytorch.org/whl/cu124 || goto :fail
) else (
  %PY% -m pip install torch torchaudio torchvision || goto :fail
)
%PY% -m pip install clearvoice soundfile || goto :fail
echo.
echo Downloading the two voice models (first time only)...
if not exist voice-models mkdir voice-models
cd voice-models
..\%PY% -c "from clearvoice import ClearVoice; ClearVoice(task='speech_enhancement', model_names=['MossFormer2_SE_48K']); ClearVoice(task='speech_super_resolution', model_names=['MossFormer2_SR_48K']); import torch; print('GPU:', torch.cuda.is_available())" || goto :fail
cd ..
echo.
echo Voice setup done. In Shot Picker use Audio ^> Enhance voice.
pause
exit /b 0
:fail
echo.
echo Setup failed. Send Claude the text above.
pause
exit /b 1
