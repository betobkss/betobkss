"""Protocolo de pergunta e resposta entre um CLI e o painel.

PROBLEMA. Os CLIs deste framework sao interativos de proposito: `COLETAR.py`
pergunta o servidor, o caso e, se preciso, a senha. Quando o painel os executa,
essa conversa nao tem para onde ir - a pergunta fica presa no `stdout` e o
processo espera uma resposta que ninguem digita. Foi o bug de 02/08: o job
ficava "rodando" para sempre e a tela mostrava apenas "Aguardando um comando...".

POR QUE UM MARCADOR, E NAO DETECCAO. A tentacao e o painel "perceber" que o
filho parou para perguntar, lendo o `stdout` caractere a caractere e adivinhando
pela ausencia de quebra de linha. Isso e heuristica: quando erra, o job volta a
pendurar - o mesmo bug, agora com mais codigo escondendo a causa. Aqui quem
pergunta **anuncia** que esta perguntando, numa linha completa e com `flush`. O
painel nao adivinha nada; ele reconhece uma linha ou nao reconhece.

CONTRATO. Uma linha no stdout:

    @@PARRA-PERGUNTA {"label": "Servidor", "default": "", "secreto": false}@@

e a resposta volta como UMA linha no stdin do processo. `secreto` marca senha: o
painel usa campo mascarado e **nao** guarda o valor no log da execucao.

O modo so liga com `PARRA_DIALOGO=1` no ambiente, que e o painel dizendo "tem
alguem do outro lado para responder". Sem isso o comportamento nao muda: no
terminal usa `input()`, e em job automatizado (bateria, agendamento) segue
falhando rapido em vez de esperar por um humano que nao existe.
"""
from __future__ import annotations

import json
import os
import sys

VAR = "PARRA_DIALOGO"
MARCA = "@@PARRA-PERGUNTA "
FIM = "@@"


def habilitado() -> bool:
    """True quando ha um painel do outro lado do stdin, pronto para responder."""
    return os.environ.get(VAR) == "1"


def montar(label: str, default: str = "", secreto: bool = False) -> str:
    """Linha de pergunta no formato do protocolo (sem a quebra de linha)."""
    corpo = json.dumps({"label": label, "default": default,
                        "secreto": bool(secreto)}, ensure_ascii=False)
    return f"{MARCA}{corpo}{FIM}"


def ler(linha: str) -> dict | None:
    """Interpreta uma linha do stdout do filho. None = linha comum de log.

    Tolerante de proposito: uma linha malformada e log, nao excecao. O painel le
    saida de producao, onde qualquer coisa pode aparecer no meio do texto.
    """
    txt = (linha or "").strip()
    if not txt.startswith(MARCA) or not txt.endswith(FIM):
        return None
    try:
        d = json.loads(txt[len(MARCA):-len(FIM)])
    except Exception:
        return None
    if not isinstance(d, dict) or not str(d.get("label", "")).strip():
        return None
    return {"label": str(d["label"]), "default": str(d.get("default", "")),
            "secreto": bool(d.get("secreto"))}


def perguntar(label: str, default: str = "", secreto: bool = False) -> str:
    """Faz a pergunta ao painel e espera a resposta (uma linha no stdin).

    Devolve o `default` se a resposta vier vazia - mesma regra do prompt no
    terminal. Se o stdin fechar (painel morreu, job cancelado), devolve o
    default em vez de estourar: o CLI segue pelo seu proprio caminho de erro,
    que sabe explicar o que faltou.
    """
    print(montar(label, default, secreto), flush=True)
    try:
        resposta = sys.stdin.readline()
    except Exception:
        return default
    if not resposta:  # EOF: o outro lado foi embora
        return default
    return resposta.strip() or default
