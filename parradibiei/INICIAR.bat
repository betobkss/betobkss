@echo off
:: Painel de Troubleshooting Oracle - versao portatil (sem instalacao)
:: Abre http://127.0.0.1:8765/ no navegador. Ctrl+C encerra.
setlocal
set "ROOT=%~dp0"
set "PY=%ROOT%python\python.exe"
if not exist "%PY%" (
    echo [ERRO] pacote incompleto: falta python\python.exe
    exit /b 1
)
"%PY%" "%ROOT%PAINEL.py" %*
endlocal
