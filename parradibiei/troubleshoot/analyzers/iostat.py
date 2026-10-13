"""
Analisador de I/O (v$filestat / v$iostat_file / AWR IO Stats).

Entrada: saida SQL*Plus com colunas de arquivo/tablespace e metricas de I/O
(reads/writes, avg read ms, io_stall). Severidade por latencia media de leitura:
>=20ms atencao, >=40ms critico (metodo USE: saturacao/erros).

Os limiares mudam conforme a PLATAFORMA. 20 ms e um numero de disco rotacional;
em Exadata o IO passa por flash cache e celulas, onde a leitura de bloco fica
tipicamente abaixo de 1 ms - aplicar 20 ms la significa nunca acusar uma celula
degradada. Quando a topologia detectada tem Exadata, os limiares caem para os
mesmos do check de celula (5/15 ms).
"""
from __future__ import annotations

import re

from ..model import Finding, Metric, Section, Severity, worst
from .base import Analyzer, find_col, num, parse_fixed_width_tables, read_text


class IoStatAnalyzer(Analyzer):
    kind = "io"
    title = "I/O / Storage"

    WARN_MS = 20.0
    CRIT_MS = 40.0
    # Exadata: flash + celulas. Alinhado ao threshold cell_read_ms do catalogo.
    WARN_MS_EXADATA = 5.0
    CRIT_MS_EXADATA = 15.0

    def _limiares(self, meta) -> tuple:
        """Limiares conforme a plataforma detectada na fase de topologia."""
        tags = ((meta or {}).get("topologia") or {}).get("tags") or []
        if "exadata" in tags:
            return self.WARN_MS_EXADATA, self.CRIT_MS_EXADATA
        return self.WARN_MS, self.CRIT_MS

    def matches(self, filename: str, head: str) -> int:
        score = 0
        fn = filename.lower()
        if re.search(r"iostat|filestat|\bio\b|disk|storage", fn):
            score += 40
        h = head.lower()
        if "phyrds" in h or "readtim" in h or "io_stall" in h or "avg_read" in h:
            score += 40
        if "datafile" in h or "file#" in h:
            score += 15
        return min(score, 100)

    def _sev(self, ms, warn=None, crit=None) -> str:
        warn = self.WARN_MS if warn is None else warn
        crit = self.CRIT_MS if crit is None else crit
        if ms is None:
            return Severity.INFO
        if ms >= crit:
            return Severity.CRIT
        if ms >= warn:
            return Severity.WARN
        return Severity.OK

    def analyze(self, files, meta):
        sections = []
        worst_file = None
        warn_ms, crit_ms = self._limiares(meta)

        for path in files:
            text = read_text(path)
            for headers, rows in parse_fixed_width_tables(text):
                ci_name = find_col(headers, "file", "name", "tablespace")
                ci_avg = find_col(headers, "avg_read", "readtim", "avg read", "avg_ms", "latency")
                ci_stall = find_col(headers, "io_stall", "stall")
                ci_rds = find_col(headers, "phyrds", "reads", "num_of_reads")
                if ci_name < 0 or (ci_avg < 0 and ci_stall < 0 and ci_rds < 0):
                    continue

                out_rows, row_sev = [], []
                for r in rows:
                    ms = None
                    if 0 <= ci_avg < len(r):
                        ms = num(r[ci_avg])
                    elif 0 <= ci_stall < len(r) and 0 <= ci_rds < len(r):
                        st, rd = num(r[ci_stall]), num(r[ci_rds])
                        ms = (st / rd) if (st and rd) else None
                    sev = self._sev(ms, warn_ms, crit_ms)
                    row_sev.append(sev)
                    out_rows.append(r)
                    name = r[ci_name] if ci_name < len(r) else "?"
                    if ms is not None and (worst_file is None or ms > worst_file[1]):
                        worst_file = (name, ms)

                sec = Section(title="Latencia de I/O por arquivo",
                              table_headers=headers, table_rows=out_rows[:15],
                              row_severity=row_sev[:15])
                if worst_file:
                    sec.metrics.append(Metric("Pior latencia leitura", f"{worst_file[1]:.1f}", "ms",
                                              severity=self._sev(worst_file[1], warn_ms, crit_ms),
                                              hint=f">={warn_ms:.0f}ms atencao, "
                                                   f">={crit_ms:.0f}ms critico"))
                for r, sev in zip(out_rows, row_sev, strict=True):
                    if sev in (Severity.WARN, Severity.CRIT):
                        name = r[ci_name] if ci_name < len(r) else "?"
                        ms = num(r[ci_avg]) if 0 <= ci_avg < len(r) else None
                        sec.findings.append(Finding(
                            title=f"Alta latencia de I/O em {name}",
                            severity=sev, impact=f"{ms:.1f} ms/leitura" if ms else "",
                            recommendation="Verificar storage/ASM, distribuicao de I/O e planos "
                                           "com full scan; avaliar AWR IO Stats.",
                        ))
                sections.append(sec)
                break

        if not sections:
            return [], ""
        overall = worst([s.severity for s in sections])
        summary = (
            (f"Pior latencia de leitura: {worst_file[0]} = {worst_file[1]:.1f} ms. "
             if worst_file else "")
            + (f"I/O critico (>={crit_ms:.0f}ms): investigar storage e planos." if overall == Severity.CRIT else
               "Latencia de I/O em atencao." if overall == Severity.WARN else
               "I/O dentro do esperado.")
        )
        return sections, summary
