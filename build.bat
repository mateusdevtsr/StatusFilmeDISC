@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Gerando StatusFilme.exe

set PY=py
where py >nul 2>nul || set PY=python

echo [1/3] Instalando dependencias...
%PY% -m pip install --upgrade -r requirements.txt || goto erro

echo.
echo [2/3] Gerando o executavel...
%PY% -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name StatusFilme ^
  --icon icone.ico ^
  --add-data "icone.ico;." ^
  --version-file versao.txt ^
  --noupx ^
  --collect-all guessit ^
  --collect-all babelfish ^
  --collect-all rebulk ^
  --exclude-module tkinter ^
  --exclude-module unittest ^
  --exclude-module pydoc ^
  --exclude-module test ^
  status_filme.py || goto erro

echo.
echo [3/3] Pronto! O programa esta em: dist\StatusFilme.exe
explorer dist
pause
exit /b 0

:erro
echo.
echo Algo deu errado. Veja a mensagem acima.
pause
exit /b 1
