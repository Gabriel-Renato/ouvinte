@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo  Ouvinte — gerando o programa de Windows
echo  Isso baixa as bibliotecas e monta um ZIP para outro computador.
echo.

py -3 -m pip install --upgrade pip
if errorlevel 1 goto fail
py -3 -m pip install --upgrade -r requirements.txt pyinstaller
if errorlevel 1 goto fail

py -3 build_exe.py
if errorlevel 1 goto fail

pause
exit /b 0

:fail
echo.
echo  A geracao falhou. Veja as mensagens acima.
echo.
pause
exit /b 1
