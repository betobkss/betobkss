"""
Proximo comando por achado (drill-down).

O relatorio dizia o que estava errado e parava ai: para investigar, o DBA
voltava ao chat e escolhia o comando seguinte de cabeca - e as duas ferramentas
certas para esse passo (JANELA.py e DETALHAR_SQL.py) ficavam esquecidas no
projeto. Aqui cada achado passa a carregar o comando seguinte, pronto para
copiar, no padrao de drill-down dos produtos de mercado (no SolarWinds DPA:
anomalia -> hora -> SQL responsavel).

O drill-down principal (next_command) e sempre READ-ONLY. A unica excecao e
advisor_command, que oferece o SQL Tuning Advisor: escreve no dicionario e
consome Tuning Pack, por isso vem separado, so no achado que o justifica e
sempre rotulado como acao que escreve.
"""
from __future__ import annotations

import datetime as _dt
import os
import re

PY = r".\.venv\Scripts\python.exe"
PLACEHOLDER_CONN = "<conexao>"

# Limiar de impacto para valer a pena gastar Tuning Pack. Igual ao
# ADVISOR_IMPACTO_MIN_S de sqlhc.py e ao IMPACTO_CRIT_S de analyzers/regression:
# o relatorio nao pode oferecer o advisor para um SQL que o proprio advisor vai
# recusar por nao ser elegivel.
ADVISOR_IMPACTO_MIN_S = 300.0

# Pistas de que o achado e uma regressao que o advisor sabe tratar. O detalhe do
# achado de regressao traz 'plano 123->456' quando o plano trocou (ver
# analyzers/regression.py) e o impacto sai como '+320s'.
_PLANO_MUDOU = re.compile(r"\bplano\s+\d+\s*->\s*\d+")
_IMPACTO_S = re.compile(r"\+\s*(\d+(?:[.,]\d+)?)\s*s\b")

# SQL_ID do Oracle: 13 caracteres base-32. O filtro de letra+digito e o que
# impede 'administrador' (13 letras) ou um numero de 13 digitos de virarem um
# SQL_ID inexistente - o comando sugerido falharia no banco.
_TOKEN13 = re.compile(r"\b[a-z0-9]{13}\b")

# 'YYYY-MM-DD HH' (com ou sem :MI). E o formato que os checks de historico
# (ASH por hora) imprimem na coluna 'janela'. A hora e obrigatoria: uma data
# solta ('estatisticas de 2026-07-29') nao define janela de analise.
_HORA = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})[ T](\d{2})(?::\d{2})?\b")


def _texto(finding) -> str:
    return " ".join(str(x or "") for x in
                    (finding.title, finding.detail, finding.impact))


def find_sql_id(texto: str) -> str:
    for t in _TOKEN13.findall(texto):
        if any(c.isdigit() for c in t) and any(c.isalpha() for c in t):
            return t
    return ""


def find_janela(texto: str):
    """Retorna (inicio, fim) como 'YYYY-MM-DD HH:MI', a hora cheia e a seguinte."""
    m = _HORA.search(texto)
    if not m:
        return None
    ano, mes, dia, hora = (int(g) for g in m.groups())
    try:
        ini = _dt.datetime(ano, mes, dia, hora)
    except ValueError:
        return None
    fim = ini + _dt.timedelta(hours=1)   # trata a virada do dia (23h -> 00h)
    f = "%Y-%m-%d %H:%M"
    return ini.strftime(f), fim.strftime(f)


def resolver_conn(case: str, conn: str = "") -> str:
    """Nome do banco para o comando sugerido.

    Prioriza o meta.json da coleta. Sem ele (casos coletados antes de o
    meta.json existir), aproveita a convencao do healthcheck: 'HC_DBSRV600' foi
    coletado do DBSRV600. So entao desiste e usa o marcador.
    """
    if conn:
        return conn
    if case.startswith("HC_") and len(case) > 3:
        return case[3:]
    return PLACEHOLDER_CONN


def advisor_ja_rodou(case_dir: str) -> bool:
    """O advisor ja foi executado neste caso? Evita oferecer de novo o que ja
    esta no acoes.sql."""
    return bool(case_dir) and os.path.exists(os.path.join(case_dir, "advisor.txt"))


def advisor_command(finding, case: str, conn: str = "",
                    ja_rodou: bool = False) -> str:
    """Comando OPT-IN do SQL Tuning Advisor, so no achado que o justifica.

    Fica separado do next_command de proposito. O drill-down principal e
    read-only e vem primeiro (observar antes de agir); o advisor ESCREVE no
    dicionario e consome Tuning Pack, entao e sempre uma segunda opcao, com
    rotulo explicito. Criterio igual ao de sqlhc._advisor_elegiveis: troca de
    plano ou impacto grande - numa regressao de TEMPO ele so diria "colete
    estatisticas", que o acoes.sql ja traz de graca.
    """
    if ja_rodou:
        return ""
    texto = _texto(finding)
    if not find_sql_id(texto):
        return ""                     # sem SQL_ID nao ha o que tunar
    m = _IMPACTO_S.search(texto)
    impacto = float(m.group(1).replace(",", ".")) if m else None
    if not (_PLANO_MUDOU.search(texto)
            or (impacto is not None and impacto >= ADVISOR_IMPACTO_MIN_S)):
        return ""
    return (f'{PY} COLETAR.py {case} --advisor '
            f'--conn {resolver_conn(case, conn)}')


def next_command(finding, case: str, conn: str = "") -> str:
    """Comando de investigacao sugerido para um achado ('' se nao houver pista).

    Ordem importa: com um SQL_ID em maos, ir direto ao SQL rende mais que
    varrer a hora inteira.
    """
    texto = _texto(finding)
    alvo = resolver_conn(case, conn)

    sql_id = find_sql_id(texto)
    if sql_id:
        return f'{PY} DETALHAR_SQL.py {case} {sql_id} --conn {alvo}'

    janela = find_janela(texto)
    if janela:
        ini, fim = janela
        return (f'{PY} JANELA.py {case} --inicio "{ini}" --fim "{fim}" '
                f'--conn {alvo}')

    return ""
