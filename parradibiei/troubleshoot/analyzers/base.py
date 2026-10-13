"""
Base dos analisadores + utilitarios de parsing.

- read_text: le arquivo texto/HTML tolerante a encoding.
- parse_fixed_width_tables: parseia saidas SQL*Plus (colunas de largura fixa
  delimitadas por linha de tracos '----').
- num: converte string numerica (com virgula/ponto/sufixo) em float.
"""
from __future__ import annotations

import os
import re
from datetime import datetime

from ..model import Section


def coletado_em(path: str) -> datetime:
    """Momento em que a evidencia foi gravada - o "agora" de quem analisa.

    Toda idade derivada (uptime da instancia, quando as estatisticas foram
    coletadas) tem de ser medida contra ESTE instante, nunca contra
    `datetime.now()`. Dois motivos:

    1. Correcao. A evidencia e uma foto. Reabrir em setembro um caso coletado
       em julho passava a dizer "estatisticas de 60 dias atras" quando no dia
       da coleta elas tinham 3 dias - o relatorio mentia sobre o passado.
    2. Reprodutibilidade. Reanalisar a MESMA evidencia tem de dar o MESMO
       diagnostico. Com `now()`, o texto "reiniciada ha N dia(s)" mudava
       sozinho a cada virada de dia e a bateria de regressao do corpus
       acusava 11 regressoes falsas por dia - ruido que treina o time a
       ignorar o alarme.

    Cai para `now()` se o arquivo sumiu: melhor um numero aproximado que
    derrubar a analise.
    """
    try:
        return datetime.fromtimestamp(os.path.getmtime(path))
    except OSError:
        return datetime.now()


def read_text(path: str) -> str:
    """Conteudo da evidencia; string vazia se o arquivo nao puder ser lido.

    Mesmo criterio do `mtime` acima. A lista de arquivos e montada no inicio da
    analise e lida depois: no intervalo o arquivo pode ter sido movido, zipado
    (`--pack`) ou apagado. Estourar aqui derrubava o analisador INTEIRO - os
    outros arquivos do mesmo grupo (saude le quatro de uma vez) iam junto, e a
    categoria sumia do relatorio por causa de um arquivo. Vazio, cada
    analisador ja sabe tratar como "sem dados".
    """
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return f.read()
    except OSError:
        return ""


def num(s: str) -> float | None:
    """Converte texto numerico em float. Aceita 1,234.56 / 1.234,56 / 12K / 3M."""
    if s is None:
        return None
    t = str(s).strip().replace("%", "")
    if not t:
        return None
    mult = 1.0
    m = re.match(r"^([\d.,]+)\s*([KMGT])?$", t, re.I)
    if not m:
        # tenta extrair primeiro numero
        m2 = re.search(r"[-+]?[\d.,]+", t)
        if not m2:
            return None
        t = m2.group(0)
    else:
        suf = m.group(2)
        t = m.group(1)
        mult = {"K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12}.get((suf or "").upper(), 1.0)
    # normalizar separadores: se tem ambos, o ultimo e decimal
    if "," in t and "." in t:
        if t.rfind(",") > t.rfind("."):
            t = t.replace(".", "").replace(",", ".")
        else:
            t = t.replace(",", "")
    elif "," in t:
        # virgula sozinha: decimal se 1-2 casas, senao milhar
        if re.search(r",\d{1,2}$", t):
            t = t.replace(",", ".")
        else:
            t = t.replace(",", "")
    try:
        return float(t) * mult
    except ValueError:
        return None


def parse_fixed_width_tables(text: str) -> list[tuple[list[str], list[list[str]]]]:
    """Extrai tabelas de saida SQL*Plus (cabecalho + linha de '----').

    Retorna lista de (headers, rows). Suporta multiplas tabelas no mesmo texto.
    """
    lines = text.splitlines()
    tables = []
    i = 0
    sep_re = re.compile(r"^[\s]*[-]{2,}[\s\-]*$")
    while i < len(lines):
        line = lines[i]
        if sep_re.match(line) and "-" in line and i > 0:
            header_line = lines[i - 1]
            # limites de coluna a partir dos grupos de tracos
            spans = [(m.start(), m.end()) for m in re.finditer(r"-+", line)]
            if len(spans) < 1 or not header_line.strip():
                i += 1
                continue
            headers = [header_line[s:e].strip() for s, e in spans]
            if not any(headers):
                i += 1
                continue
            rows = []
            j = i + 1
            while j < len(lines):
                dl = lines[j]
                if not dl.strip():
                    break
                if re.search(r"\brows?\s+selected\b|\bno rows selected\b", dl, re.I):
                    break
                if sep_re.match(dl):
                    break
                cells = []
                for k, (s, e) in enumerate(spans):
                    # ultima coluna vai ate o fim
                    cell = dl[s:] if k == len(spans) - 1 else dl[s:e]
                    cells.append(cell.strip())
                rows.append(cells)
                j += 1
            if rows:
                tables.append((headers, rows))
            i = j
        else:
            i += 1
    return tables


def find_col(headers: list[str], *aliases: str) -> int:
    """Indice da 1a coluna cujo nome contem algum alias (case-insensitive)."""
    for al in aliases:
        al_l = al.lower()
        for idx, h in enumerate(headers):
            if al_l in h.lower():
                return idx
    return -1


_LIMIAR_CACHE: dict = {}


def limiar(arquivo: str, chave: str, **padrao: float) -> dict:
    """Limiares que o `mcp/catalog.json` DECLARA para um check.

    Existe porque o catalogo declarar um threshold que nenhum codigo le e um
    bug silencioso e recorrente: ja aconteceu com o FRA (warn 80/crit 90
    ignorados) e com o `rac_gc` (warn 5/crit 15 em ms, com a severidade saindo
    do NOME do evento). O numero fica visivel no catalogo, o revisor confia
    nele, e a classificacao ignora.

    `padrao` e o fallback de quem chama, usado quando o catalogo nao declara a
    chave - assim o analisador nunca fica sem limiar, mas o catalogo sempre
    vence.

    Nota: quatro analisadores (exadata, saude, goldengate, topologia) mantem
    copias proprias desta funcao, e elas DIVERGEM - a do exadata nao tem
    `break` no laco e a do saude tem, o que muda o resultado se dois checks
    declararem o mesmo `output_file`. Unificar e uma limpeza separada; este e o
    destino canonico.
    """
    ck = (arquivo, chave)
    if ck not in _LIMIAR_CACHE:
        val = {k: float(v) for k, v in padrao.items()}
        try:
            from mcp.loader import load_catalog
            for chk in load_catalog().get("checks", []):
                if chk.get("output_file") == arquivo:
                    for k, v in ((chk.get("thresholds") or {}).get(chave)
                                 or {}).items():
                        val[k] = float(v)
                    break
        except Exception:
            # Catalogo ilegivel nao pode derrubar a analise: o fallback de quem
            # chama mantem o analisador classificando.
            pass
        _LIMIAR_CACHE[ck] = val
    return _LIMIAR_CACHE[ck]


def saturacao_do_host(dir_caso: str) -> dict:
    """Evidencia de SATURACAO do host, lida do `osstat.txt` do proprio caso.

    Existe para analisadores que precisam QUALIFICAR um achado com o estado do
    host sem reimplementar a leitura. O metodo USE - que as regras do projeto
    citam - separa utilizacao de saturacao, e a diferenca decide a cor: carga
    que subiu num host que a absorveu nao e o mesmo problema que carga que subiu
    num host que ja esta enfileirando.

    Le a MESMA evidencia e os MESMOS limiares do catalogo que o
    `OperacaoAnalyzer` usa (`limiar("osstat.txt", ...)`), para as duas secoes
    nunca discordarem sobre o mesmo host no mesmo relatorio.

    Devolve {} quando nao ha evidencia do host - o que e comum: o `host_osstat`
    e opcional e falha sem `SELECT_CATALOG_ROLE`. Ausencia de evidencia nao e
    evidencia de ausencia, entao quem chama deve tratar {} como "nao sei", e
    nunca como "host saudavel".

    Duas leituras, de propósito - e a diferença é TEMPORAL:

    - `saturado`: ha evidencia de saturacao, incluindo a historica. E a mesma
      leitura do `OperacaoAnalyzer` para o achado de memoria.
    - `fila_agora`: o recurso esta enfileirando NESTE momento. Só o load por
      CPU serve aqui, porque `LOAD` e uma media movel corrente; os contadores
      VM_IN/VM_OUT sao CUMULATIVOS desde o startup, entao "ja paginou" pode ser
      de meses atras e nao prova pressao agora.

    Quem for PROMOVER a severidade de outro achado deve usar `fila_agora`:
    dizer "critico, aja" com base num contador historico manda alguem correr
    por causa de um evento que pode ter acontecido no backup do semestre
    passado. Medido na frota: e exatamente a diferenca entre 1 critico e 0.

    Chaves: load_por_cpu, mem_livre_pct, paginou, saturado, fila_agora, motivo.
    """
    caminho = os.path.join(dir_caso, "osstat.txt")
    if not os.path.isfile(caminho):
        return {}
    tl = limiar("osstat.txt", "load_por_cpu", warn=1.0, crit=2.0)
    tm = limiar("osstat.txt", "memoria_livre_pct", warn=10.0, crit=5.0)
    por_inst: dict = {}
    for headers, rows in parse_fixed_width_tables(read_text(caminho)):
        ci_inst = find_col(headers, "inst_id", "inst")
        ci_nome = find_col(headers, "stat_name", "stat")
        ci_val = find_col(headers, "value", "valor")
        if ci_nome < 0 or ci_val < 0:
            continue
        for r in rows:
            inst = r[ci_inst].strip() if 0 <= ci_inst < len(r) else "1"
            nome = r[ci_nome].strip().upper() if ci_nome < len(r) else ""
            val = num(r[ci_val]) if ci_val < len(r) else None
            if nome and val is not None:
                por_inst.setdefault(inst, {})[nome] = val
    if not por_inst:
        return {}

    fora: dict = {"load_por_cpu": None, "mem_livre_pct": None,
                  "paginou": False, "saturado": False, "fila_agora": False,
                  "motivo": ""}
    motivos = []
    for d in por_inst.values():
        cpus = d.get("NUM_CPUS") or d.get("NUM_CPU_CORES") or 0
        load = d.get("LOAD")
        total = d.get("PHYSICAL_MEMORY_BYTES") or 0
        livre = d.get("FREE_MEMORY_BYTES")
        # Pior instancia manda: num cluster, um no enfileirando ja e saturacao
        # do servico que o usuario ve.
        if load is not None and cpus:
            pc = load / cpus
            if fora["load_por_cpu"] is None or pc > fora["load_por_cpu"]:
                fora["load_por_cpu"] = pc
        if livre is not None and total:
            pct = livre / total * 100.0
            if fora["mem_livre_pct"] is None or pct < fora["mem_livre_pct"]:
                fora["mem_livre_pct"] = pct
        # VM_IN/VM_OUT sao CUMULATIVOS desde o startup: >0 prova que o host JA
        # paginou nesta vida da instancia, nao que esteja paginando agora.
        if (d.get("VM_IN_BYTES") or 0) + (d.get("VM_OUT_BYTES") or 0) > 0:
            fora["paginou"] = True

    # Saturacao = FILA, nao utilizacao alta. Para CPU e load por CPU acima de 1
    # (ha processo esperando sua vez); para memoria, utilizacao critica COM
    # paginacao - memoria livre baixa sem swap, no Linux, e page cache.
    lpc = fora["load_por_cpu"]
    if lpc is not None and lpc >= tl.get("warn", 1.0):
        motivos.append(f"load {lpc:.2f} por CPU (fila de processos)")
        # Unica evidencia CORRENTE de fila: `LOAD` do v$osstat e a media movel
        # do sistema, medida agora.
        fora["fila_agora"] = True
    mem = fora["mem_livre_pct"]
    if mem is not None and mem <= tm.get("crit", 5.0) and fora["paginou"]:
        # Tempo VERBAL importa e ja custou caro neste projeto: VM_IN/VM_OUT sao
        # cumulativos, entao a evidencia e "ja paginou nesta vida da instancia"
        # e nao "esta paginando agora". O `operacao.py` documenta exatamente
        # isso ao lado do mesmo dado; escrever "com paginacao" aqui faria duas
        # secoes do mesmo relatorio afirmarem coisas diferentes sobre o mesmo
        # numero - e a mais forte das duas seria a errada.
        motivos.append(f"memoria livre {mem:.1f}% e o host ja paginou "
                       "nesta vida da instancia")
    fora["saturado"] = bool(motivos)
    fora["motivo"] = " e ".join(motivos)
    return fora


def cell(row: list, i: int) -> str:
    """Celula `i` da linha, ou "" se o indice nao existe.

    A evidencia vem de saida de texto do banco: coluna que faltou na versao do
    Oracle, linha truncada ou `find_col` que devolveu -1 sao rotina. Indexar
    direto estouraria IndexError no meio da analise e derrubaria o caso inteiro
    por causa de uma coluna ausente.

    Substitui o `def g(i): return r[i] ...` que estava replicado dentro do laco
    em cinco analisadores - forma que o linter acusa (B023) por prender a
    variavel do laco na closure.
    """
    return row[i] if 0 <= i < len(row) else ""


class Analyzer:
    """Interface base. Cada analisador define kind, matches() e analyze()."""
    kind = "misc"
    title = "Analise"

    def matches(self, filename: str, head: str) -> int:
        """Score 0..100 de quao bem este analisador cobre o arquivo."""
        return 0

    def analyze(self, files: list[str], meta: dict) -> tuple[list[Section], str]:
        """Retorna (sections, summary)."""
        raise NotImplementedError
