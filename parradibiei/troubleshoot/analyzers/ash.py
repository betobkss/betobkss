"""
Analisador ASH / Waits (top wait classes, eventos e SQL por atividade).

Entrada: saida SQL*Plus de ASH (ex.: queries/ash.sql) com colunas de
wait_class/event e samples/activity, ou top SQL por atividade.
Aplica Response Time = Service (CPU) + Wait (Cary Millsap): destaca a maior
classe de espera nao-CPU.
"""
from __future__ import annotations

import re

from ..model import Finding, Metric, Section, Severity, worst
from .base import Analyzer, find_col, num, parse_fixed_width_tables, read_text


class AshAnalyzer(Analyzer):
    kind = "ash"
    title = "ASH / Waits"

    def matches(self, filename: str, head: str) -> int:
        score = 0
        fn = filename.lower()
        if re.search(r"\bash\b|wait|event|sysmetric|systemevent", fn):
            score += 40
        h = head.lower()
        if "wait_class" in h or "wait class" in h:
            score += 35
        if "active_session_history" in h or "samples" in h:
            score += 20
        return min(score, 100)

    def analyze(self, files, meta):
        sections = []
        top_nonidle = None

        for path in files:
            text = read_text(path)
            for headers, rows in parse_fixed_width_tables(text):
                ci_cls = find_col(headers, "wait_class", "wait class", "class")
                ci_evt = find_col(headers, "event")
                ci_val = find_col(headers, "samples", "activity", "count", "pct", "%", "seconds", "time_waited")
                label_idx = ci_cls if ci_cls >= 0 else ci_evt
                if label_idx < 0 or ci_val < 0:
                    continue

                # total para %
                vals = [(r[label_idx] if label_idx < len(r) else "?",
                         num(r[ci_val]) if ci_val < len(r) else 0) for r in rows]
                total = sum(v for _, v in vals if v) or 1.0
                ranked = sorted(vals, key=lambda x: -(x[1] or 0))

                out_rows, row_sev = [], []
                for name, v in ranked:
                    pct = (v or 0) / total * 100
                    is_cpu = re.search(r"cpu", str(name), re.I)
                    sev = (Severity.OK if is_cpu else
                           Severity.CRIT if pct >= 50 else
                           Severity.WARN if pct >= 30 else Severity.OK)
                    out_rows.append([name, f"{v:.0f}" if v else "0", f"{pct:.1f}%"])
                    row_sev.append(sev)
                    if not is_cpu and (top_nonidle is None or pct > top_nonidle[1]):
                        top_nonidle = (name, pct)

                sec = Section(title="Distribuicao de esperas (ASH)",
                              table_headers=[headers[label_idx], "Valor", "% do total"],
                              table_rows=out_rows, row_severity=row_sev)
                if ranked:
                    dom, domv = ranked[0]
                    dom_pct = (domv or 0) / total * 100
                    sec.metrics.append(Metric("Classe dominante", str(dom)[:20],
                                              severity=worst(row_sev)))
                    sec.metrics.append(Metric("% dominante", f"{dom_pct:.1f}", "%"))
                if top_nonidle and top_nonidle[1] >= 30:
                    sev = Severity.CRIT if top_nonidle[1] >= 50 else Severity.WARN
                    sec.findings.append(Finding(
                        title=f"Espera '{top_nonidle[0]}' concentra {top_nonidle[1]:.0f}% da atividade",
                        severity=sev, impact=f"{top_nonidle[1]:.0f}% ASH",
                        recommendation=self._reco(str(top_nonidle[0])),
                    ))
                sections.append(sec)
                break  # 1a tabela util por arquivo

        if not sections:
            return [], ""
        overall = worst([s.severity for s in sections])
        summary = (
            (f"Maior espera nao-CPU: {top_nonidle[0]} ({top_nonidle[1]:.0f}% da atividade). "
             if top_nonidle else "")
            + ("Gargalo dominante identificado: atacar a classe de espera principal (Millsap: "
               "Response Time = Service + Wait)." if overall == Severity.CRIT else
               "Espera relevante a investigar." if overall == Severity.WARN else
               "Distribuicao de esperas saudavel (predominio de CPU/atividade normal).")
        )
        return sections, summary

    def _reco(self, cls: str) -> str:
        c = cls.lower()
        if "user i/o" in c or "system i/o" in c:
            return "Investigar I/O: planos com full scan, indices, storage/AWR IO Stats."
        if "concurrency" in c or "enq" in c or "lock" in c:
            return "Investigar concorrencia: blocking chain, transacoes longas, hot blocks."
        if "cluster" in c or "gc " in c:
            return "Investigar RAC/global cache: interconnect, hot blocks, distribuicao."
        if "commit" in c or "log file sync" in c:
            return "Investigar commits/redo: LGWR, I/O de redo, commits frequentes."
        if "configuration" in c:
            return "Investigar configuracao: shared pool, log buffer, undo."
        return "Investigar a classe de espera dominante com AWR/ASH detalhado."
