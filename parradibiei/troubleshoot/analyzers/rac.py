"""
Analisador RAC / Global Cache (interconnect, gc waits).

Entrada: saida de gv$system_event / ASH com eventos. Foca em eventos 'gc *'
(global cache). 'gc * busy'/'congested'/'lost' indicam problema de
concorrencia/interconnect no cluster.
"""
from __future__ import annotations

import re

from ..model import Finding, Metric, Section, Severity, worst
from .base import (
    Analyzer,
    find_col,
    limiar,
    num,
    parse_fixed_width_tables,
    read_text,
)

# Patologia de interconnect: nao e questao de estar lento, e de o bloco nao ter
# chegado. 'lost' e perda de pacote entre os nos (leva a ORA-29740, evicao de
# instancia) e 'congested' e fila no proprio interconnect - ruins em qualquer
# latencia media, inclusive baixa, porque a media esconde o evento raro e grave.
_PATOLOGICO = re.compile(r"congest|lost", re.I)


def _severidade(evt: str, avg_ms: float | None) -> str:
    """Severidade de um evento de global cache.

    Antes saia do NOME do evento: qualquer coisa com 'block' virava ATENCAO. Mas
    'block' aparece em praticamente todo evento gc ('gc cr block 2-way', 'gc
    current block 3-way'), entao TODA linha saia ATENCAO - um `gc cr block` a
    47 ms e outro a 0,4 ms recebiam o mesmo rotulo. O catalogo declarava
    `avg_ms: warn 5 / crit 15` e ninguem lia.
    """
    if _PATOLOGICO.search(evt):
        return Severity.CRIT
    t = limiar("rac.txt", "avg_ms", warn=5.0, crit=15.0)
    if avg_ms is None:
        # Sem a coluna de latencia nao ha como afirmar contencao. Registrar o
        # evento como INFO e honesto; inventar ATENCAO nao e.
        return Severity.INFO
    if avg_ms >= t.get("crit", 15.0):
        return Severity.CRIT
    if avg_ms >= t.get("warn", 5.0):
        return Severity.WARN
    return Severity.INFO


class RacAnalyzer(Analyzer):
    kind = "rac"
    title = "RAC / Global Cache"

    def matches(self, filename: str, head: str) -> int:
        score = 0
        fn = filename.lower()
        if re.search(r"\brac\b|gc_|global_cache|interconnect|gescache", fn):
            score += 40
        h = head.lower()
        if "gc " in h or "global cache" in h or "gv$ges" in h:
            score += 40
        return min(score, 100)

    def analyze(self, files, meta):
        sections = []
        gc_rows, gc_sev = [], []
        worst_evt = None

        for path in files:
            text = read_text(path)
            for headers, rows in parse_fixed_width_tables(text):
                ci_evt = find_col(headers, "event")
                # A latencia media decide a severidade, entao e buscada em
                # coluna propria. Antes um unico `find_col` pegava a PRIMEIRA
                # entre total_waits e avg, e como total_waits vem antes no
                # SELECT, a tabela mostrava contagem de esperas onde o leitor
                # precisava de milissegundos - 482013 nao diz se ha contencao.
                ci_avg = find_col(headers, "avg_ms", "avg")
                ci_qtd = find_col(headers, "total_waits", "waits", "samples",
                                  "count")
                if ci_evt < 0:
                    continue
                for r in rows:
                    evt = r[ci_evt] if ci_evt < len(r) else ""
                    if not re.match(r"\s*gc ", evt, re.I) and "global cache" not in evt.lower():
                        continue
                    avg = num(r[ci_avg]) if 0 <= ci_avg < len(r) else None
                    qtd = num(r[ci_qtd]) if 0 <= ci_qtd < len(r) else None
                    sev = _severidade(evt, avg)
                    gc_rows.append([evt.strip(),
                                    f"{qtd:.0f}" if qtd is not None else "-",
                                    f"{avg:.2f}" if avg is not None else "-",
                                    Severity.LABEL.get(sev, "")])
                    gc_sev.append(sev)
                    # O achado nomeia UM evento e carrega a severidade do
                    # conjunto, entao tem de ser o pior de verdade. Antes ficava
                    # o PRIMEIRO com warn/crit: um 'gc blocks lost' (critico)
                    # mais abaixo na tabela era rotulado como critico no
                    # cabecalho mas o titulo citava outro evento - o leitor
                    # investigava o evento errado.
                    if sev in (Severity.WARN, Severity.CRIT):
                        rank = (Severity.ORDER.get(sev, 0), avg or 0.0)
                        if worst_evt is None or rank > worst_evt[0]:
                            worst_evt = (rank, evt.strip())
                if gc_rows:
                    break
            if gc_rows:
                break

        if not gc_rows:
            return [], ""

        sec = Section(title="Eventos de Global Cache (RAC)",
                      table_headers=["Evento", "Esperas", "Media (ms)",
                                     "Severidade"],
                      table_rows=gc_rows, row_severity=gc_sev)
        sec.metrics.append(Metric("Eventos GC", str(len(gc_rows)),
                                  severity=worst(gc_sev)))
        if worst_evt:
            sev = worst(gc_sev)
            sec.findings.append(Finding(
                title=f"Contencao de global cache: {worst_evt[1]}",
                severity=sev,
                recommendation="Verificar interconnect (latencia/erros), hot blocks e "
                               "distribuicao de carga entre instancias; checar evicoes (ORA-29740).",
            ))
        sections.append(sec)
        overall = worst(gc_sev)
        summary = (
            f"{len(gc_rows)} evento(s) de global cache. "
            + ("Contencao critica de GC/interconnect." if overall == Severity.CRIT else
               "Contencao de GC em atencao." if overall == Severity.WARN else
               "Global cache sem contencao relevante.")
        )
        return sections, summary
