@echo off
:: Healthcheck: healthcheck.bat <servidor> [flags extras, ex.: --rapido]
setlocal
set "ROOT=%~dp0"
if "%~1"=="" (
    echo Uso: healthcheck.bat ^<servidor^> [--rapido] [--workers N] [--timeout S]
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
set "SRV=%~1"
shift
set "EXTRA="
:loop
if not "%~1"=="" (
    set "EXTRA=%EXTRA% %1"
    shift
    goto loop
)
"%PY%" "%ROOT%COLETAR.py" "HC_%SRV%" --conn "%SRV%" --completo%EXTRA%
endlocal
