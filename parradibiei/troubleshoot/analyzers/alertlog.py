"""
Analisador de Alert Log / erros ORA-.

Entrada: trecho do alert.log (.txt/.log). Agrupa erros ORA-##### por codigo,
conta ocorrencias, captura ultima data/hora e uma amostra.

A interpretacao (severidade, causa, acao, nota MOS) vem do dicionario
`mcp/erros_ora.json` via `troubleshoot/erros.py`: antes ficava numa tabela
embutida aqui, o que travava o conhecimento dentro do codigo. Como dado, o
radar da comunidade acrescenta erro novo sem tocar em analisador.
"""
from __future__ import annotations

import re
from collections import defaultdict

from .. import erros as dic_erros
from ..model import Finding, Metric, Section, Severity, worst
from .base import Analyzer, read_text

_TS = re.compile(
    r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
    r"|[A-Z][a-z]{2}\s+[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\s+\d{4})")
_ORA = re.compile(r"(ORA-\d{4,5})")


class AlertLogAnalyzer(Analyzer):
    kind = "alertlog"
    title = "Alert Log / Erros ORA-"

    def matches(self, filename: str, head: str) -> int:
        score = 0
        fn = filename.lower()
        if re.search(r"alert|alertlog|ora-|oralog", fn):
            score += 45
        n = len(_ORA.findall(head))
        if n >= 1:
            score += min(50, 15 + n * 5)
        if "alert log" in head.lower() or "starting ORACLE".lower() in head.lower():
            score += 10
        return min(score, 100)

    def _sev(self, code: str) -> str:
        return dic_erros.severidade(code, Severity.WARN)

    def analyze(self, files, meta):
        counts = defaultdict(int)
        last_ts = {}
        sample = {}

        for path in files:
            text = read_text(path)
            cur_ts = ""
            for line in text.splitlines():
                mts = _TS.search(line)
                if mts:
                    cur_ts = mts.group(1)
                for code in _ORA.findall(line):
                    counts[code] += 1
                    if cur_ts:
                        last_ts[code] = cur_ts
                    if code not in sample:
                        sample[code] = line.strip()[:160]

        if not counts:
            return [], ""

        # ordenar: severidade depois contagem
        order = {Severity.CRIT: 0, Severity.WARN: 1, Severity.OK: 2, Severity.INFO: 3}
        codes = sorted(counts, key=lambda c: (order.get(self._sev(c), 3), -counts[c]))

        headers = ["Erro", "Ocorr.", "Severidade", "Ultima vez", "Descricao"]
        rows, row_sev, findings = [], [], []
        n_crit = n_warn = n_novos = 0
        for c in codes:
            sev = self._sev(c)
            info = dic_erros.explicar(c)
            desc = info.get("titulo", "Erro Oracle (sem entrada no dicionario)")
            if not info:
                n_novos += 1
            rows.append([c, str(counts[c]), Severity.LABEL.get(sev, ""),
                         last_ts.get(c, "-"), desc])
            row_sev.append(sev)
            if sev == Severity.CRIT:
                n_crit += 1
            elif sev == Severity.WARN:
                n_warn += 1
            if sev in (Severity.CRIT, Severity.WARN) and (n_crit + n_warn) <= 8:
                # A causa explica o erro; a amostra do log prova que aconteceu.
                detalhe = " ".join(x for x in (info.get("causa", ""),
                                               sample.get(c, "")) if x)
                findings.append(Finding(
                    title=f"{c} ({counts[c]}x) - {desc}"
                          + (" [abrir SR]" if dic_erros.escalavel(c) else ""),
                    detail=detalhe,
                    severity=sev,
                    impact=f"{counts[c]} ocorrencia(s)",
                    recommendation=self._reco(c),
                ))

        sec = Section(title="Erros no Alert Log", table_headers=headers,
                      table_rows=rows, row_severity=row_sev, findings=findings)
        sec.metrics.append(Metric("Codigos distintos", str(len(codes))))
        sec.metrics.append(Metric("Total ocorrencias", str(sum(counts.values()))))
        sec.metrics.append(Metric("Criticos", str(n_crit),
                                  severity=Severity.CRIT if n_crit else Severity.OK))
        if n_novos:
            # Nao e alarme: e pauta de melhoria continua. Codigo sem entrada
            # significa plantao comecando do zero na proxima vez.
            sec.metrics.append(Metric("Sem entrada no dicionario", str(n_novos)))

        overall = worst(row_sev)
        summary = (
            f"{sum(counts.values())} ocorrencia(s) de {len(codes)} erro(s) ORA- distintos. "
            + (f"{n_crit} critico(s): {', '.join(c for c in codes if self._sev(c)==Severity.CRIT)}. "
               if n_crit else "")
            + ("Ha erro critico: investigar imediatamente / abrir SR se ORA-600/7445."
               if overall == Severity.CRIT else
               "Erros de atencao a avaliar." if overall == Severity.WARN else
               "Sem erros criticos.")
        )
        return [sec], summary

    def _reco(self, code: str) -> str:
        return dic_erros.acao(code)
