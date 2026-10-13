"""
Analisador GoldenGate integrado (capture / apply / lag).

Le goldengate.txt: DBA_CAPTURE, DBA_APPLY e DBA_APPLY_PROGRESS.

Por que separado do Data Guard: sao mecanismos diferentes com falhas
diferentes. Data Guard replica blocos e o lag aparece em gv$dataguard_stats;
GoldenGate integrado replica transacoes logicas via capture/apply e falha de
formas que o banco NAO acusa - um apply ABORTED por conflito de chave deixa o
banco de origem perfeitamente saudavel enquanto o destino congela.

Escopo honesto: cobre GoldenGate INTEGRADO (capture/apply no dicionario do
banco). GoldenGate classico, cujo extract roda fora do banco, nao aparece nessas
views - para ele a fonte e o GGSCI, fora do alcance de uma coleta SQL.
"""
from __future__ import annotations

import os
import re

from ..model import Finding, Metric, Section, Severity, worst
from .base import Analyzer, find_col, num, parse_fixed_width_tables, read_text

PADROES = {("goldengate.txt", "lag_min"): {"warn": 15.0, "crit": 60.0}}

_SUBT = re.compile(r"^===\s*(.+?)\s*===\s*$", re.M)
_CACHE: dict = {}


def limiar(arquivo: str, chave: str) -> dict:
    ck = (arquivo, chave)
    if ck not in _CACHE:
        val = dict(PADROES.get(ck, {}))
        try:
            from mcp.loader import load_catalog
            for chk in load_catalog().get("checks", []):
                if chk.get("output_file") == arquivo:
                    for k, v in ((chk.get("thresholds") or {}).get(chave) or {}).items():
                        val[k] = float(v)
        except Exception:
            pass
        _CACHE[ck] = val
    return _CACHE[ck]


# ABORTED e parada por ERRO; DISABLED costuma ser parada administrativa. A acao
# do DBA e diferente, por isso a severidade tambem e.
_SEV_STATUS = {"ABORTED": Severity.CRIT, "DISABLED": Severity.WARN,
               "ENABLED": Severity.OK}


class GoldenGateAnalyzer(Analyzer):
    kind = "goldengate"
    title = "GoldenGate (replicacao)"

    def matches(self, filename: str, head: str) -> int:
        fn = os.path.basename(filename or "").lower()
        base = fn[:-4] if fn.endswith(".txt") else fn
        h = (head or "").upper()
        if base in ("goldengate", "gg") or "GOLDENGATE" in h:
            return 95
        if "=== CAPTURE ===" in h or "=== APPLY ===" in h or "LAG DE APPLY" in h:
            return 95
        return 0

    def _processos(self, headers, rows, tipo: str) -> Section:
        ci_nome = find_col(headers, f"{tipo}_name", "name")
        ci_st = find_col(headers, "status")
        ci_err = find_col(headers, "error_message", "error")

        sec = Section(title=f"GoldenGate - {tipo}",
                      table_headers=headers, table_rows=rows[:20])
        sevs, ruins = [], []
        for r in rows:
            st = (r[ci_st].strip().upper() if 0 <= ci_st < len(r) else "")
            sev = _SEV_STATUS.get(st, Severity.WARN if st else Severity.INFO)
            sevs.append(sev)
            if sev in (Severity.WARN, Severity.CRIT):
                nome = r[ci_nome].strip() if 0 <= ci_nome < len(r) else "?"
                err = r[ci_err].strip() if 0 <= ci_err < len(r) else ""
                ruins.append((nome, st, err))

        sec.metrics.append(Metric(f"Processos de {tipo}", str(len(rows)),
                                  severity=worst(sevs)))
        if ruins:
            det = "; ".join(f"{n} [{s}]" + (f" {e[:60]}" if e else "")
                            for n, s, e in ruins[:5])
            parado = "ABORTED" in {s for _, s, _ in ruins}
            sec.findings.append(Finding(
                title=f"{len(ruins)} processo(s) de {tipo} do GoldenGate fora de ENABLED",
                detail=det, severity=worst(sevs),
                impact=f"{len(ruins)} de {len(rows)}",
                recommendation=(
                    ("Capture parado nao le mais redo: verificar ERROR_MESSAGE, "
                     "espaco no STREAMS_POOL, retencao de archivelog (o capture "
                     "precisa dos redos ainda em disco) e "
                     "ENABLE_GOLDENGATE_REPLICATION=TRUE."
                     if tipo.lower() == "capture" else
                     "Apply parado congela o destino sem alarme na origem: ver "
                     "DBA_APPLY_ERROR para o erro exato, conflitos de chave e se "
                     "ha handler de conflito definido.")
                    + (" ABORTED indica parada por ERRO, nao administrativa - "
                       "trate como incidente." if parado else "")),
            ))
        return sec

    def _lag(self, headers, rows) -> Section:
        t = limiar("goldengate.txt", "lag_min")
        warn, crit = t.get("warn", 15.0), t.get("crit", 60.0)
        ci_nome = find_col(headers, "apply_name", "name")
        ci_lag = find_col(headers, "lag_min", "lag")

        sec = Section(title="GoldenGate - lag de apply",
                      table_headers=headers, table_rows=rows[:20])
        pior, nome_pior = 0.0, ""
        for r in rows:
            lag = num(r[ci_lag]) if 0 <= ci_lag < len(r) else None
            if lag is None:
                continue
            if lag > pior:
                pior = lag
                nome_pior = r[ci_nome].strip() if 0 <= ci_nome < len(r) else "?"

        sev = (Severity.CRIT if pior >= crit else
               Severity.WARN if pior >= warn else Severity.OK)
        sec.metrics.append(Metric("Maior lag de apply", f"{pior:.1f} min",
                                  severity=sev))
        if sev in (Severity.WARN, Severity.CRIT):
            sec.findings.append(Finding(
                title=f"Lag de apply de {pior:.1f} min em {nome_pior}",
                severity=sev, impact=f"{pior:.1f} min",
                recommendation=(
                    "Lag alto significa destino desatualizado - risco de perda "
                    "de dados em failover e de relatorio lendo dado velho. "
                    "Comparar throughput do apply com o volume do capture, "
                    "avaliar paralelismo (APPLY_SERVERS) e contencao no "
                    "destino (indices, triggers, bloqueios)."),
            ))
        return sec

    def analyze(self, files, meta):
        sections = []
        for path in files:
            texto = read_text(path)
            tabelas = parse_fixed_width_tables(texto)
            marcadores = _SUBT.findall(texto)
            for idx, (headers, rows) in enumerate(tabelas):
                if not (headers and rows):
                    continue
                marc = (marcadores[idx] if idx < len(marcadores) else "").upper()
                if "LAG" in marc or find_col(headers, "lag_min") >= 0:
                    sections.append(self._lag(headers, rows))
                elif "CAPTURE" in marc or find_col(headers, "capture_name") >= 0:
                    sections.append(self._processos(headers, rows, "capture"))
                elif "APPLY" in marc or find_col(headers, "apply_name") >= 0:
                    sections.append(self._processos(headers, rows, "apply"))

        if not sections:
            return [], ""
        achados = [f for s in sections for f in s.findings]
        if achados:
            resumo = "GoldenGate: " + "; ".join(f.title for f in achados[:3]) + "."
        else:
            resumo = "GoldenGate: capture, apply e lag sem alerta."
        return sections, resumo
