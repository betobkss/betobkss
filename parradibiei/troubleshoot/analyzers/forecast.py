"""
Analisador de Previsao de Capacidade ("dias ate encher").

Entrada: saida do check `tablespace_forecast`, que aplica REGR_SLOPE sobre
DBA_HIST_TBSPC_SPACE_USAGE (30 dias) e devolve, por tablespace, o crescimento
em MB/dia e a estimativa de dias ate atingir o tamanho maximo.

Por que existe: "SYSAUX com 87% de uso" nao diz se a acao e para hoje ou para o
trimestre. "SYSAUX enche em ~12 dias" prioriza sozinho. E a leitura preditiva que
Foglight/Datadog vendem, aqui feita com regressao linear simples e explicavel.

Regra de severidade (dias ate encher):
- <= 30 dias  -> CRITICO (cabe dentro de um ciclo de mudanca)
- <= 90 dias  -> ATENCAO (planejar no trimestre)
- > 90 dias   -> OK
Sem crescimento (slope <= 0) -> estavel, nao gera achado.
Historico curto (< MIN_DIAS_HIST dias) -> nao extrapola, so informa.
"""
from __future__ import annotations

import re

from ..model import Finding, Metric, Section, Severity, worst
from .base import Analyzer, find_col, num, parse_fixed_width_tables, read_text


class ForecastAnalyzer(Analyzer):
    kind = "forecast"
    title = "Previsao de capacidade (dias ate encher)"

    CRIT_DIAS = 30.0
    WARN_DIAS = 90.0
    MIN_DIAS_HIST = 5          # menos que isso a reta nao tem sustentacao
    HORIZONTE_DIAS = 180       # dimensionamento sugerido na recomendacao

    def matches(self, filename: str, head: str) -> int:
        score = 0
        fn = filename.lower()
        if re.search(r"forecast|previsao|crescimento|growth|capacity", fn):
            score += 60
        h = head.upper()
        if "DIAS_P_ENCHER" in h or "DIAS_ATE_ENCHER" in h:
            score += 40
        if "GROWTH_MB_DIA" in h or "CRESC_MB_DIA" in h:
            score += 20
        return min(score, 100)

    # ── leitura das colunas ──
    def _dias(self, headers, row):
        ci = find_col(headers, "dias_p_encher", "dias_ate_encher", "days_to_full")
        return num(row[ci]) if 0 <= ci < len(row) else None

    def _hist(self, headers, row):
        ci = find_col(headers, "dias_hist", "amostras", "samples")
        v = num(row[ci]) if 0 <= ci < len(row) else None
        return v

    def _growth(self, headers, row):
        ci = find_col(headers, "growth_mb_dia", "cresc_mb_dia", "growth")
        return num(row[ci]) if 0 <= ci < len(row) else None

    def _pct(self, headers, row):
        ci = find_col(headers, "pct_used", "pct")
        return num(row[ci]) if 0 <= ci < len(row) else None

    def _free_mb(self, headers, row):
        ci_max = find_col(headers, "max_mb")
        ci_used = find_col(headers, "used_mb")
        if 0 <= ci_max < len(row) and 0 <= ci_used < len(row):
            mx, us = num(row[ci_max]), num(row[ci_used])
            if mx is not None and us is not None:
                return mx - us
        return None

    def _name(self, headers, row):
        ci = find_col(headers, "tablespace", "name")
        return row[ci] if 0 <= ci < len(row) else "?"

    def _sev(self, dias, hist) -> str:
        """Severidade pelo prazo. Sem crescimento ou historico curto nao alarma."""
        if hist is not None and hist < self.MIN_DIAS_HIST:
            return Severity.INFO
        if dias is None or dias <= 0:
            return Severity.OK          # estavel ou encolhendo
        if dias <= self.CRIT_DIAS:
            return Severity.CRIT
        if dias <= self.WARN_DIAS:
            return Severity.WARN
        return Severity.OK

    def _gb(self, mb) -> str:
        if mb is None:
            return "?"
        return f"{mb/1024:.1f} GB" if abs(mb) >= 1024 else f"{mb:.0f} MB"

    def _prazo(self, dias):
        """Prazo legivel: '4426 dias' obriga o leitor a fazer conta; acima de um
        ano o numero exato nao muda decisao nenhuma."""
        if dias >= 365:
            return f"{dias/365:.1f}", "anos"
        return f"{dias:.0f}", "dias"

    def _reco(self, dias, growth, free_mb) -> str:
        """Quanto espaco pedir para cobrir o horizonte, no ritmo atual."""
        if growth is None or growth <= 0:
            return "Sem crescimento no periodo; manter monitoramento."
        precisa = growth * self.HORIZONTE_DIAS - (free_mb or 0)
        base = (f"No ritmo de {self._gb(growth)}/dia, cobrir {self.HORIZONTE_DIAS} dias "
                f"pede ~{self._gb(max(precisa, 0))} adicionais. ")
        if dias is not None and dias <= self.CRIT_DIAS:
            return (base + "Acao imediata: adicionar datafile ou elevar MAXSIZE do "
                    "autoextend; avaliar purge/retencao antes de comprar espaco.")
        return (base + "Planejar expansao na proxima janela; verificar se o "
                "crescimento e esperado (retencao, carga nova, falta de purge).")

    def analyze(self, files, meta):
        sections = []
        piores = []          # (nome, dias, sev)

        for path in files:
            text = read_text(path)
            for headers, rows in parse_fixed_width_tables(text):
                if not headers:
                    continue
                if find_col(headers, "dias_p_encher", "dias_ate_encher",
                            "days_to_full") < 0:
                    continue

                row_sev, curtos = [], 0
                for r in rows:
                    dias, hist = self._dias(headers, r), self._hist(headers, r)
                    sev = self._sev(dias, hist)
                    row_sev.append(sev)
                    if hist is not None and hist < self.MIN_DIAS_HIST:
                        curtos += 1
                    if sev in (Severity.WARN, Severity.CRIT):
                        piores.append((self._name(headers, r), dias, sev))

                sec = Section(title="Previsao de capacidade (dias ate encher)",
                              table_headers=headers, table_rows=rows,
                              row_severity=row_sev)

                prazos = [d for d, s in ((self._dias(headers, r),
                                          self._sev(self._dias(headers, r),
                                                    self._hist(headers, r)))
                                         for r in rows)
                          if d is not None and d > 0 and s != Severity.INFO]
                if prazos:
                    mn = min(prazos)
                    valor, unidade = self._prazo(mn)
                    sec.metrics.append(Metric(
                        "Menor prazo", valor, unidade,
                        severity=self._sev(mn, None),
                        hint=f"<={self.CRIT_DIAS:.0f}d critico, <={self.WARN_DIAS:.0f}d atencao"))
                sec.metrics.append(Metric("Em crescimento", str(len(prazos))))

                for r in rows:
                    dias, hist = self._dias(headers, r), self._hist(headers, r)
                    sev = self._sev(dias, hist)
                    if sev not in (Severity.WARN, Severity.CRIT):
                        continue
                    nome = self._name(headers, r)
                    pct, growth = self._pct(headers, r), self._growth(headers, r)
                    free_mb = self._free_mb(headers, r)
                    det = []
                    if pct is not None:
                        det.append(f"{pct:.1f}% usado")
                    if growth is not None:
                        det.append(f"crescendo {self._gb(growth)}/dia")
                    if free_mb is not None:
                        det.append(f"{self._gb(free_mb)} livres")
                    if hist is not None:
                        det.append(f"{hist:.0f} dias de historico")
                    sec.findings.append(Finding(
                        title=f"{nome} enche em ~{dias:.0f} dias",
                        detail="; ".join(det),
                        severity=sev,
                        impact=f"{dias:.0f} dias no ritmo atual",
                        recommendation=self._reco(dias, growth, free_mb),
                    ))

                if curtos:
                    sec.note = (f"{curtos} objeto(s) com menos de {self.MIN_DIAS_HIST} "
                                "dias de historico: sem extrapolacao (retencao do AWR "
                                "curta ou tablespace recem-criada).")
                sections.append(sec)

        if not sections:
            return [], ""

        overall = worst([s.severity for s in sections])
        crit = [p for p in piores if p[2] == Severity.CRIT]
        warn = [p for p in piores if p[2] == Severity.WARN]
        crit.sort(key=lambda p: p[1])
        summary = (
            (f"{len(crit)} objeto(s) enchem em <= {self.CRIT_DIAS:.0f} dias: "
             + ", ".join(f"{n} (~{d:.0f}d)" for n, d, _ in crit[:5]) + ". " if crit else "")
            + (f"{len(warn)} em atencao (<= {self.WARN_DIAS:.0f} dias). " if warn else "")
            + ("Provisionar espaco agora." if overall == Severity.CRIT else
               "Planejar expansao no trimestre." if overall == Severity.WARN else
               "Nenhum tablespace com prazo curto.")
        )
        return sections, summary
