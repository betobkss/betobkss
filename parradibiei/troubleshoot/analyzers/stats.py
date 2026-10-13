"""
Analisador de Estatisticas do otimizador.

Entrada: saida de dba_tab_statistics / dba_tab_modifications (table_name,
last_analyzed, stale_stats, num_rows). Flag estatisticas STALE ou antigas.
"""
from __future__ import annotations

import re
from datetime import datetime

from ..model import Finding, Metric, Section, Severity, worst
from .base import (
    Analyzer,
    coletado_em,
    find_col,
    parse_fixed_width_tables,
    read_text,
)

DIAS_PARA_ANTIGA = 7          # mesmo corte do check 'stale_stats' no catalogo

# Abreviacao de mes como o Oracle imprime conforme o NLS_LANGUAGE da sessao.
# O coletor deste framework formata em ISO, mas evidencia colada do SQL
# Developer / modo Citrix chega no formato default da sessao - e ai o idioma
# do banco decide se e JUL, JUL ou JUL... nem sempre: SET/SEP e DEZ/DIC mudam.
_MESES = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
    "FEV": 2, "ABR": 4, "MAI": 5, "AGO": 8, "SET": 9, "OUT": 10, "DEZ": 12,
    "ENE": 1, "DIC": 12,
}

_RE_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_RE_ORACLE = re.compile(r"(\d{1,2})[/-]([A-Za-z]{3})[/-](\d{2,4})")


def _ano(bruto: str) -> int:
    """Ano de 2 digitos pela regra RR do Oracle: 00-49 e 20xx, 50-99 e 19xx."""
    n = int(bruto)
    if len(bruto) == 4:
        return n
    return 2000 + n if n < 50 else 1900 + n


def parse_data(s: str):
    """Data de LAST_ANALYZED em qualquer formato que chega na evidencia.

    Existe porque o mesmo campo aparece em dois formatos: o coletor daqui
    emite `YYYY-MM-DD`, mas o modo Citrix e o copiar/colar do SQL Developer
    trazem o default da sessao (`19-JUL-26`). Antes so o ISO era lido - e a
    consequencia era silenciosa: estatistica de dois anos atras num relatorio
    em DD-MON-YY passava como se estivesse em dia.

    Devolve None quando nao ha data reconhecivel (celula vazia, 'N/A').
    """
    if not s:
        return None
    m = _RE_ISO.search(s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:                 # 2026-13-45 e lixo, nao data
            return None
    m = _RE_ORACLE.search(s)
    if m:
        mes = _MESES.get(m.group(2).upper())
        if not mes:
            return None
        try:
            return datetime(_ano(m.group(3)), mes, int(m.group(1)))
        except ValueError:
            return None
    return None


class StatsAnalyzer(Analyzer):
    kind = "stats"
    title = "Estatisticas do Otimizador"

    def matches(self, filename: str, head: str) -> int:
        score = 0
        fn = filename.lower()
        if re.search(r"stats|estatistic|stale|dbms_stats|last_analyzed", fn):
            score += 40
        h = head.lower()
        if "stale_stats" in h or "last_analyzed" in h:
            score += 45
        return min(score, 100)

    def _old(self, s: str, ref: datetime | None = None) -> bool:
        """Estatistica coletada ha mais de DIAS_PARA_ANTIGA dias.

        `ref` e o momento da COLETA, nao o de agora: a idade da estatistica e
        um fato do dia em que a evidencia foi tirada. Sem essa ancora, reabrir
        um caso semanas depois marcava como antiga uma estatistica que estava
        em dia na hora do incidente.

        Data ausente devolve False de proposito: 'nao sei quando foi' nao e o
        mesmo que 'faz tempo'. O caso de LAST_ANALYZED nulo ja e capturado
        pela coluna STALE_STATS.
        """
        d = parse_data(s)
        if d is None:
            return False
        return ((ref or datetime.now()) - d).days > DIAS_PARA_ANTIGA

    def analyze(self, files, meta):
        sections = []
        n_stale = 0

        for path in files:
            text = read_text(path)
            ref = coletado_em(path)
            for headers, rows in parse_fixed_width_tables(text):
                ci_name = find_col(headers, "table_name", "segment", "name")
                ci_stale = find_col(headers, "stale_stats", "stale")
                ci_last = find_col(headers, "last_analyzed", "last_anal")
                if ci_name < 0 or (ci_stale < 0 and ci_last < 0):
                    continue
                out_rows, row_sev = [], []
                for r in rows:
                    stale = (r[ci_stale].strip().upper() == "YES") if 0 <= ci_stale < len(r) else False
                    old = (self._old(r[ci_last], ref)
                           if 0 <= ci_last < len(r) else False)
                    sev = Severity.WARN if (stale or old) else Severity.OK
                    if sev == Severity.WARN:
                        n_stale += 1
                    row_sev.append(sev)
                    out_rows.append(r)
                sec = Section(title="Estatisticas de tabelas",
                              table_headers=headers, table_rows=out_rows[:20],
                              row_severity=row_sev[:20])
                sec.metrics.append(Metric("Objetos", str(len(rows))))
                sec.metrics.append(Metric("Stale/antigas", str(n_stale),
                                          severity=Severity.WARN if n_stale else Severity.OK))
                if n_stale:
                    stale_names = [r[ci_name]
                                   for r, s in zip(out_rows, row_sev, strict=True)
                                   if s == Severity.WARN][:10]
                    sec.findings.append(Finding(
                        title=f"{n_stale} objeto(s) com estatisticas STALE/antigas",
                        detail="Ex.: " + ", ".join(stale_names),
                        severity=Severity.WARN, impact=f"{n_stale} objeto(s)",
                        recommendation="Coletar estatisticas: DBMS_STATS.GATHER_TABLE_STATS "
                                       "(ou GATHER_SCHEMA_STATS) nos objetos afetados.",
                    ))
                sections.append(sec)
                break

        if not sections:
            return [], ""
        overall = worst([s.severity for s in sections])
        summary = (
            f"{n_stale} objeto(s) com estatisticas desatualizadas. "
            + ("Recomenda-se recoletar estatisticas (pode causar planos ruins)."
               if overall == Severity.WARN else "Estatisticas atualizadas.")
        )
        return sections, summary
