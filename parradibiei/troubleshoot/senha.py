"""Como a senha do banco entra no framework - ponto unico.

REGRA (decidida em 2026-08-02): a senha e **sempre digitada**, a cada execucao,
como numa sessao de SuperPuTTY. Ela nao mora em arquivo, nem em registro, nem em
linha de comando, e nao sobrevive ao fim do processo.

O QUE FOI REMOVIDO E POR QUE
- `--password` nos CLIs: argv e publico. Qualquer processo do usuario le a linha
  de comando alheia (`Get-CimInstance Win32_Process`), e o shell ainda guarda uma
  copia no historico.
- `ORA_PASSWORD` lida do ambiente: parecia inofensivo, mas o `guardar_senha.ps1`
  gravava a variavel em `HKCU\\Environment` - texto puro, permanente, herdado por
  todo processo do usuario. Na pratica ninguem mais digitava senha nenhuma. Como
  variavel herdada e indistinguivel entre "o painel acabou de passar" e "esta
  gravada no registro desde marco", o caminho inteiro saiu.

O CANAL LEGITIMO ENTRE PROCESSOS E O STDIN, via `dialogo`: quem pergunta anuncia
a pergunta e le UMA linha. E o mesmo mecanismo que o painel usa para mostrar a
caixa mascarada, e o que a bateria usa para digitar uma vez e atender os 26
servidores. Stdin nao aparece em `Win32_Process`, nao vai para o historico do
shell e morre com o processo.
"""
from __future__ import annotations

import getpass
import sys

from troubleshoot import dialogo


# Uma unica excecao, para o chamador decidir se pergunta ou aborta.
class SemComoPerguntar(RuntimeError):
    """Nao ha terminal nem painel: pedir a senha aqui travaria o processo."""


def ha_como_perguntar() -> bool:
    """True se existe alguem do outro lado: painel/bateria (stdin) ou terminal."""
    if dialogo.habilitado():
        return True
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except Exception:
        return False


def pedir(user: str = "dbaread", label: str = "") -> str:
    """Pede a senha AGORA e devolve. Nunca consulta ambiente, arquivo ou argv.

    `secreto=True` no dialogo e o que faz o painel usar campo mascarado e manter
    a resposta fora do log da execucao - o log fica visivel na tela e no
    historico da sessao.
    """
    texto = label or f"Senha de {user}"
    if dialogo.habilitado():
        return dialogo.perguntar(texto, secreto=True)
    if ha_como_perguntar():
        return getpass.getpass(texto + ": ")
    raise SemComoPerguntar(
        f"sem senha para {user} e sem ninguem para digitar (stdin nao e "
        "terminal). Rode pelo painel ou por um terminal interativo.")


def explicar_falta(user: str = "dbaread") -> list[str]:
    """Mensagem de erro quando nao ha como perguntar.

    Fica aqui, e nao espalhada nos CLIs, para que a orientacao nao envelheca em
    um arquivo e continue certa nos outros - foi o que aconteceu com o
    `guardar_senha.bat`, citado em quatro lugares depois de deixar de existir.
    """
    return [f"[ERRO] Sem senha para {user} e sem terminal para perguntar.",
            "       A senha e sempre digitada na hora - nao ha arquivo,",
            "       variavel de ambiente nem parametro que a substitua.",
            "       Rode num terminal interativo ou pelo painel (PAINEL.bat).",
            "       Em automacao, o processo pai deve responder pelo stdin",
            "       (e o que a bateria TESTE_HOMOLOG.py faz)."]
