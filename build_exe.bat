@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo  Ouvinte — gerando o programa de Windows
echo  Isso baixa as bibliotecas e monta a pasta dist\Ouvinte
echo.

py -3 -m pip install --upgrade pip
if errorlevel 1 goto fail
py -3 -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto fail

py -3 -m PyInstaller --noconfirm --clean --windowed --onedir --name Ouvinte ^
  --collect-all customtkinter ^
  --collect-all faster_whisper ^
  --collect-all ctranslate2 ^
  --collect-all imageio_ffmpeg ^
  --collect-all tokenizers ^
  --collect-all huggingface_hub ^
  --hidden-import av ^
  --hidden-import av.audio ^
  --hidden-import windnd ^
  main.py
if errorlevel 1 goto fail

echo.
echo  Pronto. Abra: dist\Ouvinte\Ouvinte.exe
echo  Compacte a pasta dist\Ouvinte em um ZIP para enviar o programa.
echo  O modelo de voz baixa na primeira transcricao e fica em %%LOCALAPPDATA%%\Ouvinte
echo.
pause
exit /b 0

:fail
echo.
echo  A geracao falhou. Veja as mensagens acima.
echo.
pause
exit /b 1
