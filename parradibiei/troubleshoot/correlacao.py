"""
CORRELACAO entre mudancas no ambiente e achados.

O framework ja coletava as mudancas (restart, DDL, parametros, patches, coleta
de estatisticas) e ja produzia achados - mas ninguem ligava uma coisa na outra.
Quem investiga faz essa ligacao de cabeca: "isto comecou depois daquilo".

Regra de honestidade, que vale mais que a heuristica: o resultado e sempre
**hipotese a confirmar**, nunca causa afirmada. Correlacao nao e causalidade, e
um relatorio que troca uma pela outra manda o DBA consertar a coisa errada com
ar de certeza. Por isso cada item sai com o motivo do vinculo e um grau de
confianca explicito, para o DBA testar (ou descartar com `CASO.py --hipotese`).
"""
from __future__ import annotations

import re
from datetime import datetime

from .model import Severity

# Titulo da secao que o ChangesAnalyzer produz.
SECAO_MUDANCAS = "mudancas no ambiente"

# Tipo de mudanca -> assinaturas de achado que ela costuma explicar.
# Cada par tem o "porque" que sera mostrado: sem explicar o vinculo, a
# correlacao vira palpite com cara de conclusao.
REGRAS = {
    "STATS": (
        ("plano", "regress", "sql", "db time", "elapsed", "cardinal",
         "full table scan", "otimizador"),
        "coleta de estatisticas muda o plano escolhido pelo otimizador"),
    "PARAM": (
        ("plano", "regress", "sql", "memoria", "sga", "pga", "shared pool",
         "ora-04031", "cursor", "paralel", "otimizador", "db time"),
        "parametro de instancia altera otimizador, memoria ou concorrencia"),
    "PATCH": (
        ("ora-00600", "ora-07445", "erro interno", "plano", "regress",
         "corrup", "excecao"),
        "patch troca codigo do kernel e planos: bug novo ou comportamento novo"),
    "RESTART": (
        ("memoria", "sga", "pga", "cache", "hit ratio", "uptime", "plano",
         "cursor", "ora-04031"),
        "apos restart o cache esvazia e os planos sao recalculados"),
    "DDL": (
        ("espaco", "tablespace", "segmento", "indice", "index", "invalid",
         "plano", "regress", "sql", "bloqueio", "lock"),
        "DDL altera estrutura: espaco, planos e objetos dependentes"),
}

# DDL nao e uma coisa so, e tratar como se fosse produz vinculo absurdo.
# Flagrado no caso real HC_APPHO114: um `CREATE OR REPLACE PACKAGE` apareceu
# como gatilho provavel de "5 datafiles com AUTOEXTEND OFF" e de "ORA-01555".
# Compilar codigo PL/SQL nao consome um bloco de tablespace - o vinculo era
# so coincidencia de data, e mandava o DBA investigar o deploy errado.
#
# O tipo do objeto vem no campo `detalhe` da coleta ("PACKAGE (VALID)").
_DDL_CODIGO = ("package", "procedure", "function", "trigger", "view",
               "type", "synonym", "library")
# O que compilar codigo REALMENTE pode causar: dependente invalido, hard parse,
# troca de plano e library cache lock durante a compilacao.
_DDL_CODIGO_MARCAS = ("invalid", "plano", "regress", "sql", "bloqueio",
                      "lock", "library cache", "cursor", "parse")


def _marcas_ddl(mud: dict, marcas: tuple) -> tuple:
    """Marcas validas para este DDL, conforme o tipo de objeto alterado.

    Sem isto, qualquer deploy de package vira suspeito de qualquer coisa - e a
    correlacao perde justamente o que a torna util, que e ser especifica.
    """
    alvo = f"{mud.get('detalhe', '')} {mud.get('objeto', '')}".lower()
    if any(t in alvo for t in _DDL_CODIGO):
        return _DDL_CODIGO_MARCAS
    return marcas


# Janela -> confianca. Mudanca de ontem explica melhor que a do mes passado.
JANELAS = ((1, "alta"), (7, "media"), (30, "baixa"))

_DATA = re.compile(r"(\d{4}-\d{2}-\d{2})(?:[ T](\d{2}:\d{2}))?")


def _quando(txt: str):
    m = _DATA.search(txt or "")
    if not m:
        return None
    try:
        return datetime.strptime(
            m.group(1) + " " + (m.group(2) or "00:00"), "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def _confianca(dias: float) -> str:
    for limite, grau in JANELAS:
        if dias <= limite:
            return grau
    return ""


def _idx(heads: list, nome: str, padrao: int) -> int:
    """Posicao da coluna pelo nome, com posicao fixa como reserva.

    A tabela vem do ChangesAnalyzer, que nomeia as colunas; o padrao posicional
    cobre evidencia antiga, gerada antes de a coluna ganhar nome.
    """
    return heads.index(nome) if nome in heads else padrao


def extrair_mudancas(result) -> list:
    """Mudancas coletadas, a partir da secao do ChangesAnalyzer.

    Le do CaseResult (nao do disco): a analise ja parseou o arquivo, e reler
    daria duas fontes de verdade para o mesmo dado.
    """
    out = []
    for sec in result.sections:
        if SECAO_MUDANCAS not in (sec.title or "").lower():
            continue
        heads = [str(h).strip().lower() for h in (sec.table_headers or [])]
        i_q, i_t = _idx(heads, "quando", 0), _idx(heads, "tipo", 1)
        i_o, i_d = _idx(heads, "objeto", 2), _idx(heads, "detalhe", 3)
        for r in sec.table_rows or []:
            if len(r) <= i_t:
                continue
            quando = _quando(str(r[i_q]))
            if not quando:
                continue
            out.append({
                "quando": quando,
                "tipo": str(r[i_t]).strip().upper(),
                "objeto": str(r[i_o]).strip() if len(r) > i_o else "",
                "detalhe": str(r[i_d]).strip() if len(r) > i_d else "",
            })
    return out


def correlacionar(result, agora: datetime | None = None, limite: int = 8) -> list:
    """Liga achados a mudancas recentes que poderiam explica-los.

    So considera achado critico ou de atencao: correlacionar o que esta bem
    seria ruido.
    """
    mudancas = extrair_mudancas(result)
    if not mudancas:
        return []
    agora = agora or datetime.now()

    achados = [(s.title, f) for s in result.sections for f in s.findings
               if f.severity in (Severity.CRIT, Severity.WARN)]
    if not achados:
        return []

    vinculos = []
    for secao, f in achados:
        alvo = f"{f.title} {f.detail} {secao}".lower()
        melhor = None
        for mud in mudancas:
            marcas, porque = REGRAS.get(mud["tipo"], ((), ""))
            if mud["tipo"] == "DDL" and marcas:
                marcas = _marcas_ddl(mud, marcas)
                if marcas is _DDL_CODIGO_MARCAS:
                    porque = ("compilar codigo PL/SQL invalida dependentes e "
                              "pega library cache lock")
            if not marcas or not any(m in alvo for m in marcas):
                continue
            dias = (agora - mud["quando"]).total_seconds() / 86400.0
            if dias < 0:
                continue
            conf = _confianca(dias)
            if not conf:
                continue
            # entre duas mudancas do mesmo tipo, a mais recente e a candidata
            if melhor is None or mud["quando"] > melhor["mudanca"]["quando"]:
                melhor = {"mudanca": mud, "confianca": conf, "porque": porque,
                          "dias": dias}
        if melhor:
            vinculos.append({
                "achado": f.title,
                "severidade": f.severity,
                "secao": secao,
                "quando": melhor["mudanca"]["quando"].strftime("%Y-%m-%d %H:%M"),
                "tipo": melhor["mudanca"]["tipo"],
                "objeto": melhor["mudanca"]["objeto"],
                "detalhe": melhor["mudanca"]["detalhe"],
                "dias": round(melhor["dias"], 1),
                "confianca": melhor["confianca"],
                "porque": melhor["porque"],
            })

    ordem = {"alta": 0, "media": 1, "baixa": 2}
    sev = {Severity.CRIT: 0, Severity.WARN: 1}
    vinculos.sort(key=lambda v: (ordem.get(v["confianca"], 9),
                                 sev.get(v["severidade"], 9), v["dias"]))
    return vinculos[:limite]


def correlacao_markdown(result, agora: datetime | None = None) -> str:
    """Secao do relatorio. Sai vazia com uma frase honesta quando nao ha nada -
    silencio seria lido como 'nao houve mudanca'."""
    vinculos = correlacionar(result, agora=agora)
    if not vinculos:
        return ("- Nenhuma mudanca recente coincide com os achados (ou nao ha "
                "coleta de mudancas neste caso).")
    linhas = [
        "_Hipoteses a confirmar, nao causa provada: a mudanca abaixo e "
        "compativel com o achado no tempo e no tipo. Teste uma por vez "
        "(`CASO.py <caso> --hipotese \"...\"`)._", ""]
    for v in vinculos:
        linhas.append(
            f"- **{v['achado']}** pode estar ligado a **{v['tipo']}** de "
            f"{v['quando']} ({v['dias']} dia(s) antes) em `{v['objeto']}` "
            f"- confianca {v['confianca']}.")
        linhas.append(f"  - Porque: {v['porque']}.")
        if v["detalhe"]:
            linhas.append(f"  - Mudanca: {v['detalhe']}")
    return "\n".join(linhas)
