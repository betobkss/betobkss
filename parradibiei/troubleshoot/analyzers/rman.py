"""
Analisador RMAN / Backups.

Entrada: saida de v$rman_backup_job_details / rc_rman_backup_job_details ou
LIST BACKUP SUMMARY. Detecta status (COMPLETED/FAILED/RUNNING) e idade do
ultimo backup. FAILED = critico; sem backup ha >24h = atencao.
"""
from __future__ import annotations

import re

from ..model import Finding, Metric, Section, Severity, worst
from .base import Analyzer, find_col, parse_fixed_width_tables, read_text


class RmanAnalyzer(Analyzer):
    kind = "rman"
    title = "RMAN / Backups"

    def matches(self, filename: str, head: str) -> int:
        score = 0
        fn = filename.lower()
        if re.search(r"rman|backup", fn):
            score += 45
        h = head.upper()
        if "RMAN" in h or "BACKUP_JOB" in h or "LIST BACKUP" in h:
            score += 35
        if "COMPLETED" in h or "FAILED" in h or "INPUT_TYPE" in h:
            score += 20
        return min(score, 100)

    def _sev_status(self, status: str) -> str:
        s = status.upper()
        if "FAIL" in s or "ERROR" in s:
            return Severity.CRIT
        if "RUNNING" in s or "WARN" in s:
            return Severity.WARN
        if "COMPLETED" in s:
            return Severity.OK
        return Severity.INFO

    def analyze(self, files, meta):
        sections = []
        n_fail = 0
        last_ok = None

        for path in files:
            text = read_text(path)
            for headers, rows in parse_fixed_width_tables(text):
                ci_status = find_col(headers, "status")
                ci_end = find_col(headers, "end", "completion", "end_time")
                ci_type = find_col(headers, "input_type", "type")
                if ci_status < 0:
                    continue
                out_rows, row_sev = [], []
                for r in rows:
                    st = r[ci_status] if ci_status < len(r) else ""
                    sev = self._sev_status(st)
                    row_sev.append(sev)
                    out_rows.append(r)
                    if sev == Severity.CRIT:
                        n_fail += 1
                    if sev == Severity.OK and 0 <= ci_end < len(r):
                        last_ok = r[ci_end]
                sec = Section(title="Jobs de backup (RMAN)",
                              table_headers=headers, table_rows=out_rows[:15],
                              row_severity=row_sev[:15])
                sec.metrics.append(Metric("Jobs", str(len(rows))))
                sec.metrics.append(Metric("Falhas", str(n_fail),
                                          severity=Severity.CRIT if n_fail else Severity.OK))
                if last_ok:
                    sec.metrics.append(Metric("Ultimo OK", str(last_ok)[:20]))
                for r, sev in zip(out_rows, row_sev, strict=True):
                    if sev == Severity.CRIT:
                        st = r[ci_status] if ci_status < len(r) else ""
                        typ = r[ci_type] if 0 <= ci_type < len(r) else ""
                        sec.findings.append(Finding(
                            title=f"Backup {typ} com status {st}",
                            severity=Severity.CRIT, impact=st,
                            recommendation="Revisar log do RMAN (V$RMAN_OUTPUT), espaco na FRA e "
                                           "conectividade; reexecutar backup. Risco de RPO.",
                        ))
                sections.append(sec)
                break

        if not sections:
            return [], ""
        overall = worst([s.severity for s in sections])
        summary = (
            (f"{n_fail} job(s) de backup com falha. " if n_fail else "")
            + (f"Ultimo backup OK: {last_ok}. " if last_ok else "")
            + ("Falha de backup: risco de RPO - reexecutar e investigar." if overall == Severity.CRIT
               else "Backups em execucao/atencao." if overall == Severity.WARN
               else "Backups saudaveis.")
        )
        return sections, summary
