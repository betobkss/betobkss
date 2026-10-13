# limpar_senha.ps1 - remove a senha Oracle persistida na maquina.
#
# Ate 02/08/2026 existia aqui um `guardar_senha.ps1` que fazia
#   [Environment]::SetEnvironmentVariable("ORA_PASSWORD", $senha, "User")
# ou seja: gravava a senha em TEXTO PURO em HKCU\Environment. Permanente,
# sobrevivia a reboot e era herdada por qualquer processo do usuario.
#
# O framework nao le mais ORA_PASSWORD - a senha e digitada a cada execucao.
# Mas apagar o script nao apaga o que ele ja gravou: quem o usou continua com a
# senha no registro ate rodar isto aqui.

$achou = $false

foreach ($escopo in @("User", "Machine")) {
    try {
        $v = [Environment]::GetEnvironmentVariable("ORA_PASSWORD", $escopo)
    }
    catch {
        # escopo Machine exige elevacao para leitura em algumas politicas
        Write-Host "  [--] escopo $escopo nao pode ser lido (precisa de admin)"
        continue
    }
    if ($v) {
        $achou = $true
        Write-Host "  [!!] ORA_PASSWORD persistida no escopo $escopo ($($v.Length) caracteres)"
        try {
            [Environment]::SetEnvironmentVariable("ORA_PASSWORD", $null, $escopo)
            Write-Host "  [OK] removida do escopo $escopo"
        }
        catch {
            Write-Host "  [ERRO] nao foi possivel remover do escopo ${escopo}: $_"
            Write-Host "         rode este script como administrador"
        }
    }
    else {
        Write-Host "  [OK] escopo ${escopo}: nada persistido"
    }
}

# A variavel ainda pode estar viva na janela ABERTA (herdada de antes da
# limpeza), e um processo filho aberto dali ainda a receberia.
#
# CUIDADO com o que se promete aqui. Um processo NAO consegue mexer no ambiente
# do processo pai: quando isto roda via `limpar_senha.bat` (que chama
# `powershell -File`), este script e um processo FILHO e o `Remove-Item` abaixo
# so limpa a copia dele proprio, que morre em seguida. A janela do usuario
# continua com a variavel. A versao anterior anunciava "removida tambem da
# sessao atual" nesse caso - mentira, e justamente sobre credencial exposta.
#
# So ha um jeito de limpar a janela de quem chamou: ser DOT-SOURCED
# (`. .\limpar_senha.ps1`), que executa dentro da propria sessao.
$dotsourced = $MyInvocation.InvocationName -eq "."
$sobrouNaJanela = $false
if ($env:ORA_PASSWORD) {
    Remove-Item Env:\ORA_PASSWORD -ErrorAction SilentlyContinue
    if ($dotsourced) {
        Write-Host "  [OK] removida tambem desta sessao do PowerShell"
    }
    else {
        $sobrouNaJanela = $true
        Write-Host "  [!!] a sua janela do PowerShell AINDA tem a variavel na"
        Write-Host "       memoria (herdada quando foi aberta). Nao da para"
        Write-Host "       limpa-la de um processo filho como este."
        Write-Host "       Feche e reabra o terminal, ou rode:  . .\limpar_senha.ps1"
    }
}

Write-Host ""
if ($achou) {
    Write-Host "Feche e reabra os terminais/IDEs abertos: eles herdaram a"
    Write-Host "variavel antiga quando foram iniciados."
    Write-Host "TROQUE a senha do usuario de banco: ela esteve em texto puro no"
    Write-Host "registro por tempo indeterminado."
}
elseif ($sobrouNaJanela) {
    Write-Host "O registro esta limpo. So falta esta janela, que carregou a"
    Write-Host "variavel na memoria ao ser aberta - feche e reabra."
}
else {
    Write-Host "Nada a fazer - a senha nao esta gravada nesta maquina."
}
