@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -3 -m pip install -r requirements.txt
if errorlevel 1 goto fail
py -3 main.py
if errorlevel 1 goto fail
exit /b 0

:fail
echo.
echo Nao foi possivel abrir o Ouvinte.
echo Instale o Python 3.12 em python.org e marque "Add python.exe to PATH".
echo.
pause
exit /b 1
