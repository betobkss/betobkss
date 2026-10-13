@echo off
:: Painel local (GUI): painel.bat [--port 9000] [--no-open]
:: Abre o navegador em http://127.0.0.1:8765/ e mantem o servidor nesta janela.
setlocal
set "ROOT=%~dp0"
:: Instalacao normal usa o .venv; a versao portatil (EMPACOTAR.py) traz o seu
:: proprio Python em python\ e NAO tem .venv de proposito. Sem este fallback o
:: pacote portatil mandava rodar install.bat - justo o que ele existe para evitar.
set "PY=%ROOT%.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=%ROOT%python\python.exe"
if not exist "%PY%" (
    echo [ERRO] Nenhum Python encontrado nesta pasta.
    echo        Instalacao normal: rode  .\install.bat
    echo        Versao portatil  : pacote incompleto, falta python\python.exe
    exit /b 1
)
"%PY%" "%ROOT%PAINEL.py" %*
endlocal
