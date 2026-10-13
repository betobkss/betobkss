@echo off
:: Remove a senha Oracle gravada no registro do Windows (HKCU\Environment).
:: O framework nao usa mais senha persistida - ela e digitada a cada execucao.
powershell -ExecutionPolicy Bypass -File "%~dp0limpar_senha.ps1" %*
pause
