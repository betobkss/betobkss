@echo off
:: Terminal do framework portatil: abre um prompt com o Python do pacote.
:: Ex.:  python COLETAR.py HC_DBSRV600 --conn DBSRV600 --completo
setlocal
set "ROOT=%~dp0"
doskey python="%ROOT%python\python.exe" $*
echo Python do pacote: %ROOT%python\python.exe
echo Ex.: python COLETAR.py HC_DBSRV600 --conn DBSRV600 --completo
echo.
echo Este terminal e o unico lugar do pacote com --advisor (escreve no banco
echo e consome Tuning Pack). O painel nao tem esse comando.
cmd /k
