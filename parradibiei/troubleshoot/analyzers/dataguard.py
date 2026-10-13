"""
Analisador Data Guard / HA (transport lag, apply lag, managed standby).

Entrada: saida de gv$dataguard_stats (name/value) e/ou gv$managed_standby.
Severidade por lag: >=15min atencao, >=1h critico. Perda de apply/transport
tambem e critica (risco de HA).
"""
from __future__ import annotations

import re

from ..model import Finding, Metric, Section, Severity, worst
from .base import Analyzer, find_col, parse_fixed_width_tables, read_text


def _lag_seconds(v: str):
    """Converte '+00 00:15:30' / '00:15:30' / '15 mins' em segundos."""
    if not v:
        return None
    m = re.search(r"(\d+)\s+(\d{1,2}):(\d{2}):(\d{2})", v)  # DD HH:MM:SS
    if m:
        d, h, mi, s = map(int, m.groups())
        return d * 86400 + h * 3600 + mi * 60 + s
    m = re.search(r"(\d{1,2}):(\d{2}):(\d{2})", v)          # HH:MM:SS
    if m:
        h, mi, s = map(int, m.groups())
        return h * 3600 + mi * 60 + s
    m = re.search(r"(\d+)\s*(sec|min|hour|hr|day)", v, re.I)
    if m:
        n = int(m.group(1))
        u = m.group(2).lower()
        return n * {"sec": 1, "min": 60, "hour": 3600, "hr": 3600, "day": 86400}[u[:3] if u[:3] in ("sec","min","hou","hr","day") else u]
    return None


class DataGuardAnalyzer(Analyzer):
    kind = "dataguard"
    title = "Data Guard / HA"

    def matches(self, filename: str, head: str) -> int:
        score = 0
        fn = filename.lower()
        if re.search(r"dataguard|dg|standby|replica", fn):
            score += 45
        h = head.lower()
        if "apply lag" in h or "transport lag" in h or "dataguard_stats" in h:
            score += 45
        if "managed_standby" in h or "mrp" in h:
            score += 20
        return min(score, 100)

    def _sev(self, secs) -> str:
        if secs is None:
            return Severity.INFO
        if secs >= 3600:
            return Severity.CRIT
        if secs >= 900:
            return Severity.WARN
        return Severity.OK

    def analyze(self, files, meta):
        sections = []
        max_lag = None
        lag_rows, lag_sev = [], []
        ms_headers, ms_rows = None, []

        for path in files:
            text = read_text(path)
            for headers, rows in parse_fixed_width_tables(text):
                ci_name = find_col(headers, "name", "metric")
                ci_val = find_col(headers, "value", "lag", "time")
                ci_proc = find_col(headers, "process")
                # Managed standby (gv$managed_standby): PROCESS + STATUS
                if ci_proc >= 0 and find_col(headers, "status") >= 0:
                    ms_headers = headers
                    ms_rows.extend(rows)
                    continue
                if ci_name < 0 or ci_val < 0:
                    continue
                for r in rows:
                    name = r[ci_name] if ci_name < len(r) else ""
                    val = r[ci_val] if ci_val < len(r) else ""
                    if "lag" in name.lower():
                        secs = _lag_seconds(val)
                        sev = self._sev(secs)
                        lag_rows.append([name, val, Severity.LABEL.get(sev, "")])
                        lag_sev.append(sev)
                        if secs is not None and (max_lag is None or secs > max_lag[1]):
                            max_lag = (name, secs, val)

        # tambem varrer texto por "apply lag ... HH:MM:SS"
        if not lag_rows:
            for path in files:
                text = read_text(path)
                for m in re.finditer(r"(transport lag|apply lag)\s*[:=]?\s*([+\d: ]+)", text, re.I):
                    name, val = m.group(1), m.group(2).strip()
                    secs = _lag_seconds(val)
                    sev = self._sev(secs)
                    lag_rows.append([name, val, Severity.LABEL.get(sev, "")])
                    lag_sev.append(sev)
                    if secs is not None and (max_lag is None or secs > max_lag[1]):
                        max_lag = (name, secs, val)

        summaries = []
        if lag_rows:
            sec = Section(title="Data Guard - Lag",
                          table_headers=["Metrica", "Valor", "Severidade"],
                          table_rows=lag_rows, row_severity=lag_sev)
            if max_lag:
                sec.metrics.append(Metric("Maior lag", max_lag[2],
                                          severity=self._sev(max_lag[1]),
                                          hint=">=15min atencao, >=1h critico"))
                if self._sev(max_lag[1]) in (Severity.WARN, Severity.CRIT):
                    sec.findings.append(Finding(
                        title=f"{max_lag[0]} elevado ({max_lag[2]})",
                        severity=self._sev(max_lag[1]), impact=max_lag[2],
                        recommendation="Verificar rede/transport, MRP (apply), archive gap e "
                                       "recursos do standby; risco de RPO/RTO.",
                    ))
            sections.append(sec)
            overall = worst(lag_sev)
            summaries.append(
                (f"Maior lag: {max_lag[0]} = {max_lag[2]}. " if max_lag else "")
                + ("Lag critico (>=1h): risco de HA/RPO - investigar apply/transport."
                   if overall == Severity.CRIT else
                   "Lag em atencao." if overall == Severity.WARN else
                   "Data Guard sincronizado."))

        # Secao de Managed Standby (processos MRP/RFS/ARCH)
        if ms_rows:
            sec = Section(title="Data Guard - Managed Standby",
                          table_headers=ms_headers, table_rows=ms_rows)
            procs = " ".join(" ".join(r) for r in ms_rows).upper()
            mrp = "MRP" in procs
            sec.metrics.append(Metric("Processos", str(len(ms_rows))))
            sec.metrics.append(Metric("Apply (MRP)", "ativo" if mrp else "nao detectado",
                                      severity=Severity.OK if mrp else Severity.INFO))
            sec.note = ("MRP (apply) em execucao no standby."
                        if mrp else "Processos de standby presentes (sem MRP visivel).")
            sections.append(sec)
            summaries.append(f"Managed standby: {len(ms_rows)} processo(s)"
                             + (", MRP (apply) ativo." if mrp else "."))

        if not sections:
            return [], ""
        return sections, "  ".join(summaries)
