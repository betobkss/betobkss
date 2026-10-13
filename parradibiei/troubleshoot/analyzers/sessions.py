"""
Analisador de Sessoes / Metricas de instancia.

Entrada: saida de gv$session (top sessions por espera) ou contagem de sessoes
por status. Flag sessoes com espera longa e alto numero de sessoes ativas.
"""
from __future__ import annotations

import re

from ..model import Finding, Metric, Section, Severity, worst
from .base import Analyzer, find_col, num, parse_fixed_width_tables, read_text


class SessionsAnalyzer(Analyzer):
    kind = "sessions"
    title = "Sessoes / Instancia"

    def matches(self, filename: str, head: str) -> int:
        score = 0
        fn = filename.lower()
        if re.search(r"session|sessao|sessoes|instancia|sysmetric|conn", fn):
            score += 35
        h = head.lower()
        if ("sid" in h and ("serial" in h or "status" in h or "event" in h)):
            score += 35
        if "active" in h and "inactive" in h:
            score += 20
        return min(score, 100)

    def analyze(self, files, meta):
        sections = []
        long_waits = 0

        for path in files:
            text = read_text(path)
            for headers, rows in parse_fixed_width_tables(text):
                ci_sid = find_col(headers, "sid", "session")
                ci_secs = find_col(headers, "seconds_in_wait", "secs", "wait_time", "elapsed")
                ci_evt = find_col(headers, "event")
                ci_stat = find_col(headers, "status")
                if ci_sid < 0 and ci_stat < 0:
                    continue

                out_rows, row_sev = [], []
                for r in rows:
                    secs = num(r[ci_secs]) if 0 <= ci_secs < len(r) else None
                    sev = (Severity.CRIT if (secs or 0) >= 600 else
                           Severity.WARN if (secs or 0) >= 120 else Severity.OK)
                    if sev in (Severity.WARN, Severity.CRIT):
                        long_waits += 1
                    row_sev.append(sev)
                    out_rows.append(r)

                sec = Section(title="Sessoes",
                              table_headers=headers, table_rows=out_rows[:15],
                              row_severity=row_sev[:15])
                sec.metrics.append(Metric("Sessoes listadas", str(len(rows))))
                if long_waits:
                    sec.metrics.append(Metric("Esperas longas", str(long_waits),
                                              severity=worst(row_sev)))
                # strict: as duas listas sao preenchidas no mesmo laco. Se um
                # dia sairem de sincronia, o zip frouxo truncaria em silencio e
                # ESCONDERIA achados criticos - melhor estourar.
                for r, sev in zip(out_rows, row_sev, strict=True):
                    if sev == Severity.CRIT:
                        sid = r[ci_sid] if 0 <= ci_sid < len(r) else "?"
                        evt = r[ci_evt] if 0 <= ci_evt < len(r) else ""
                        secs = num(r[ci_secs]) if 0 <= ci_secs < len(r) else None
                        sec.findings.append(Finding(
                            title=f"Sessao {sid} em espera longa"
                                  + (f" ({evt})" if evt else ""),
                            severity=Severity.CRIT,
                            impact=f"{secs:.0f}s" if secs else "",
                            recommendation="Investigar o que a sessao aguarda (SQL, lock, I/O); "
                                           "avaliar acao apos confirmar com a aplicacao.",
                        ))
                sections.append(sec)
                break

        if not sections:
            return [], ""
        overall = worst([s.severity for s in sections])
        summary = (
            f"{long_waits} sessao(oes) com espera longa. "
            + ("Ha sessao critica (>=10min em espera)." if overall == Severity.CRIT else
               "Sessoes em atencao." if overall == Severity.WARN else
               "Sessoes sem esperas longas relevantes.")
        )
        return sections, summary
