@echo off
:: Alias rapido para COLETAR.py (funciona de qualquer pasta se chamar pelo caminho completo)
setlocal
set "ROOT=%~dp0"
:: Instalacao normal usa o .venv; a versao portatil traz o Python em python\.
set "PY=%ROOT%.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=%ROOT%python\python.exe"
if not exist "%PY%" (
    echo [ERRO] Nenhum Python encontrado nesta pasta.
    echo        Instalacao normal: rode  .\install.bat
    echo        Versao portatil  : pacote incompleto, falta python\python.exe
    exit /b 1
)
"%PY%" -c "import sys" 2>nul
if errorlevel 1 (
    echo [ERRO] O .venv existe mas nao funciona nesta maquina.
    echo        Isso acontece quando a pasta .venv foi COPIADA de outro PC.
    echo        Apague a pasta .venv e rode:  .\install.bat
    exit /b 1
)
"%PY%" "%ROOT%COLETAR.py" %*
endlocal
