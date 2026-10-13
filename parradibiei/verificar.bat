@echo off
:: Verifica se esta maquina esta pronta para rodar o framework (nao acessa banco).
setlocal
set "ROOT=%~dp0"
:: Vale para os dois mundos: .venv na instalacao normal, python\ no pacote
:: portatil. Este e o primeiro .bat que alguem roda numa maquina nova, entao e
:: o pior lugar possivel para exigir um .venv que a versao portatil nao tem.
set "PY=%ROOT%.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=%ROOT%python\python.exe"
if not exist "%PY%" (
    echo [ERRO] Nenhum Python encontrado nesta pasta.
    echo        Instalacao normal: rode  .\install.bat
    echo        Versao portatil  : pacote incompleto, falta python\python.exe
    exit /b 1
)
"%PY%" -m troubleshoot.doctor
endlocal
