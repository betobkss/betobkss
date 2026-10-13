@echo off
:: Redija rapido: redija.bat <caso>
setlocal
set "ROOT=%~dp0"
if "%~1"=="" (
    echo Uso: redija.bat ^<caso^>
    exit /b 1
)
:: Instalacao normal usa o .venv; a versao portatil traz o Python em python\.
set "PY=%ROOT%.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=%ROOT%python\python.exe"
if not exist "%PY%" (
    echo [ERRO] Nenhum Python encontrado nesta pasta.
    echo        Instalacao normal: rode  .\install.bat
    echo        Versao portatil  : pacote incompleto, falta python\python.exe
    exit /b 1
)
"%PY%" "%ROOT%TROUBLESHOOT.py" "%~1" --completo
endlocal
