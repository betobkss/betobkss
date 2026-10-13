"""
Dicionario de erros ORA-.

Traduz o codigo cru do alert log em **causa, acao concreta e nota MOS**. Sem
isto o relatorio diz "ORA-04031 (12x)" e o DBA continua sem saber o que fazer -
que era exatamente a lacuna em relacao ao que o TFA/AHF entrega.

Fonte: `mcp/erros_ora.json` (dado, nao codigo), para o radar da comunidade
(`MELHORIA.py --fontes`) poder acrescentar erro novo sem mexer em analisador.
"""
from __future__ import annotations

import json
import os
import re

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAMINHO = os.path.join(BASE_DIR, "mcp", "erros_ora.json")

RE_ORA = re.compile(r"ORA-\d{4,5}")

# Familias de codigo que aparecem no dia a dia do DBA. O PREFIXO faz parte da
# identidade do erro e nao pode ser descartado: PLS-00201 ("identifier must be
# declared") e ORA-00201 ("control file version incompatible") sao erros
# completamente diferentes, e ate 2026-09 normalizar() devolvia ORA-00201 para
# PLS-00201 - o dicionario respondia com uma falha critica de controlfile para
# quem tinha, na verdade, um problema de privilegio em PL/SQL. Responder errado
# e pior do que responder "nao sei".
FAMILIAS = ("ORA", "PLS", "PLW", "TNS", "RMAN", "SP2", "DPY", "DPI",
            "IMP", "EXP", "KUP", "LRM", "CRS", "CLSRSC", "PRCD", "PRKO", "PRCR")

RE_CODIGO = re.compile(r"\b(" + "|".join(FAMILIAS) + r")-(\d{3,6})\b", re.I)

_CACHE: dict | None = None
_CACHE_MSG: list | None = None


def carregar(caminho: str = "") -> dict:
    """Dicionario completo (cache em modulo). Falha silenciosa e proposital:
    um dicionario ausente nao pode derrubar a analise de um caso."""
    global _CACHE
    if _CACHE is None or caminho:
        try:
            with open(caminho or CAMINHO, encoding="utf-8") as f:
                dados = (json.load(f).get("erros") or {})
        except Exception:
            dados = {}
        if caminho:
            return dados
        _CACHE = dados
    return _CACHE


def carregar_mensagens(caminho: str = "") -> list:
    """Mensagens de driver/cliente mapeadas para o codigo Oracle equivalente.

    Por que existe: o DBA raramente cola o codigo. O SQL Developer mostra
    "No more data to read from socket" e o python-oracledb mostra "DPY-4011";
    nenhum dos dois imprime ORA-03113, que e o erro de verdade. Sem este mapa,
    a busca na KB caia em playbook errado por casamento de palavra solta
    ('socket' -> datapump/sql_lento), o que e pior do que nao achar nada.
    """
    global _CACHE_MSG
    if _CACHE_MSG is None or caminho:
        try:
            with open(caminho or CAMINHO, encoding="utf-8") as f:
                dados = (json.load(f).get("mensagens") or [])
        except Exception:
            dados = []
        if caminho:
            return dados
        _CACHE_MSG = dados
    return _CACHE_MSG


def normalizar(codigo: str) -> str:
    """'ora-600' e 'ORA-00600' sao o mesmo erro; 'PLS-00201' NAO e 'ORA-00201'.

    O alert log escreve ORA-00600, mas gente e documentacao escrevem ORA-600.
    Sem normalizar o numero, metade das buscas no dicionario falharia. Sem
    PRESERVAR o prefixo, o dicionario devolve a entrada de outra familia - ver
    a nota em FAMILIAS. Codigo sem prefixo continua sendo tratado como ORA-,
    que e o caso de quem digita so '600'.
    """
    texto = (codigo or "").strip().upper()
    m = RE_CODIGO.search(texto)
    if m:
        return f"{m.group(1).upper()}-{int(m.group(2)):05d}"
    n = re.search(r"(\d{1,6})", texto)
    if not n:
        return texto
    return f"ORA-{int(n.group(1)):05d}"


def explicar(codigo: str) -> dict:
    """Entrada do dicionario para o codigo, ou {} se desconhecido."""
    return carregar().get(normalizar(codigo), {})


def conhecido(codigo: str) -> bool:
    return bool(explicar(codigo))


def severidade(codigo: str, padrao: str = "warn") -> str:
    return explicar(codigo).get("severidade", padrao)


def escalavel(codigo: str) -> bool:
    """Erro que normalmente termina em SR na Oracle (bug, corrupcao, evicao)."""
    return bool(explicar(codigo).get("escalar"))


def secundario(codigo: str) -> bool:
    """Linha de MOLDURA da pilha - contexto de outro erro, nao erro proprio.

    `ORA-06512: at line 42` nao diz o que falhou: diz ONDE. `ORA-02063:
    preceding 2 lines from LINK` diz que o erro veio do outro lado do dblink.
    `ORA-01403` (NO_DATA_FOUND) e excecao normal de PL/SQL, usada como controle
    de fluxo em muito codigo.

    Por que isso ganhou campo proprio: um unico incidente produz VARIAS destas
    linhas (uma por nivel de chamada), e cada uma virava um achado separado. O
    par canonico e ORA-20000 (o que falhou) + ORA-06512 (onde) - contar os dois
    como problemas independentes dilui o erro de verdade no meio da moldura.
    """
    return bool(explicar(codigo).get("secundario"))


def acao(codigo: str) -> str:
    e = explicar(codigo)
    if e.get("acao"):
        return e["acao"] + (f" (ver {e['mos']})" if e.get("mos") else "")
    return ("Erro sem entrada no dicionario: leia o contexto no alert log e os "
            "traces do mesmo horario. Se for recorrente, acrescente o codigo em "
            "mcp/erros_ora.json para o proximo plantao nao comecar do zero.")


def titulo(codigo: str) -> str:
    return explicar(codigo).get("titulo", "Erro Oracle")


def extrair(texto: str, todas_familias: bool = False) -> list:
    """Codigos distintos no texto, normalizados e na ordem de aparicao.

    O padrao continua sendo SO ORA-: e o que os analisadores de alert log
    esperam, e alargar por padrao mudaria a contagem de achados de todo caso ja
    entregue. Quem quer o espectro completo (PLS-, TNS-, DPY-...) pede
    explicitamente - hoje a busca da KB.
    """
    vistos, out = set(), []
    padrao = RE_CODIGO if todas_familias else RE_ORA
    for m in padrao.finditer(texto or ""):
        n = normalizar(m.group(0))
        if n not in vistos:
            vistos.add(n)
            out.append(n)
    return out


def por_mensagem(texto: str) -> list:
    """Codigos sugeridos pela MENSAGEM do driver/cliente, sem codigo numerico.

    Casa por substring normalizada (minusculas, espacos colapsados) porque o
    texto chega colado de tela, com quebras e prefixos variaveis do driver.
    """
    alvo = re.sub(r"\s+", " ", (texto or "").strip().lower())
    if not alvo:
        return []
    vistos, out = set(), []
    for ent in carregar_mensagens():
        padrao = re.sub(r"\s+", " ", str(ent.get("padrao") or "").lower())
        if not padrao or padrao not in alvo:
            continue
        for c in ent.get("codigos") or []:
            n = normalizar(c)
            if n not in vistos:
                vistos.add(n)
                out.append(n)
    return out


def codigos_no_texto(texto: str) -> list:
    """Tudo que identifica o erro num texto colado: codigo explicito de
    qualquer familia MAIS o que a mensagem do driver denuncia."""
    out = extrair(texto, todas_familias=True)
    for c in por_mensagem(texto):
        if c not in out:
            out.append(c)
    return out
