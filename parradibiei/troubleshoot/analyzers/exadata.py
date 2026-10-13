"""
Analisador Exadata (smart scan / offload e esperas de celula).

Por que Exadata precisa de analise propria: os limiares de IO do resto da
plataforma foram calibrados para disco. Em Exadata o IO passa por flash cache e
pelas celulas, e um avg_read_ms de 8 ms - aceitavel em disco comum - indica
celula degradada ou flash sem espaco. Usar o mesmo numero nos dois ambientes
erra nas duas pontas.

Cuidado deliberado com falso positivo no offload: pouco IO elegivel para
offload NAO e defeito. Um sistema OLTP em Exadata acessa por indice e tem
elegibilidade proxima de zero por desenho - alarmar nisso acusaria todo OLTP do
parque. O que indica problema de verdade e o smart scan estar ATIVO e devolver
quase tudo que leu, ou seja, filtrando pouco na celula.
"""
from __future__ import annotations

import os
import re

from ..model import Finding, Metric, Section, Severity, worst
from .base import Analyzer, find_col, num, parse_fixed_width_tables, read_text

PADROES = {
    ("exadata.txt", "offload_pct"): {"warn": 50.0, "crit": 20.0},
    ("exadata.txt", "cell_read_ms"): {"warn": 5.0, "crit": 15.0},
}

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


def _gb(bytes_):
    return (bytes_ or 0.0) / 1073741824.0


class ExadataAnalyzer(Analyzer):
    kind = "exadata"
    title = "Exadata (smart scan e celulas)"

    def matches(self, filename: str, head: str) -> int:
        fn = os.path.basename(filename or "").lower()
        base = fn[:-4] if fn.endswith(".txt") else fn
        h = (head or "").upper()
        if base == "exadata" or "OFFLOAD" in h or "ESPERAS DE CELULA" in h:
            return 95
        return 0

    def _offload(self, headers, rows) -> Section:
        """Soma os contadores de gv$sysstat (cumulativos desde o startup) e
        deriva a eficiencia de filtragem na celula."""
        ci_nome = find_col(headers, "name", "nome")
        ci_val = find_col(headers, "value", "valor")
        acum: dict = {}
        for r in rows:
            if not (0 <= ci_nome < len(r)):
                continue
            nome = r[ci_nome].strip().lower()
            v = num(r[ci_val]) if 0 <= ci_val < len(r) else 0.0
            acum[nome] = acum.get(nome, 0.0) + (v or 0.0)

        eleg = acum.get("cell physical io bytes eligible for predicate offload", 0.0)
        devolvido = acum.get(
            "cell physical io interconnect bytes returned by smart scan", 0.0)
        stor_idx = acum.get("cell physical io bytes saved by storage index", 0.0)
        flash = acum.get("cell flash cache read hits", 0.0)
        total_lido = acum.get("physical read total bytes", 0.0)

        sec = Section(title="Offload / smart scan (Exadata)",
                      table_headers=headers, table_rows=rows[:20])
        sec.metrics.append(Metric("Leitura fisica total", f"{_gb(total_lido):.1f} GB"))
        sec.metrics.append(Metric("Elegivel para offload", f"{_gb(eleg):.1f} GB"))
        if stor_idx:
            sec.metrics.append(Metric("Poupado por storage index",
                                      f"{_gb(stor_idx):.1f} GB"))
        if flash:
            sec.metrics.append(Metric("Flash cache read hits", f"{flash:.0f}"))

        if total_lido > 0:
            # Informativo, SEM severidade: baixa elegibilidade e normal em OLTP.
            sec.metrics.append(Metric("Do total, elegivel",
                                      f"{100.0 * eleg / total_lido:.1f}%"))

        if eleg <= 0:
            sec.note = ("Sem IO elegivel para offload na janela: o acesso e por "
                        "indice (tipico de OLTP). Nao ha smart scan a avaliar.")
            return sec

        t = limiar("exadata.txt", "offload_pct")
        warn, crit = t.get("warn", 50.0), t.get("crit", 20.0)
        eficiencia = 100.0 * (1.0 - (devolvido / eleg)) if eleg else 0.0
        eficiencia = max(0.0, min(100.0, eficiencia))
        sev = (Severity.CRIT if eficiencia < crit else
               Severity.WARN if eficiencia < warn else Severity.OK)
        sec.metrics.append(Metric("Filtrado na celula", f"{eficiencia:.1f}%",
                                  severity=sev))
        if sev in (Severity.WARN, Severity.CRIT):
            sec.findings.append(Finding(
                title=f"Smart scan filtrando pouco: {eficiencia:.1f}% de reducao",
                detail=(f"{_gb(eleg):.1f} GB elegiveis e {_gb(devolvido):.1f} GB "
                        f"devolvidos pelo interconnect. O offload esta ativo mas "
                        f"quase nada e descartado na celula."),
                severity=sev, impact=f"{_gb(devolvido):.1f} GB pelo interconnect",
                recommendation=(
                    "Predicado pouco seletivo ou nao offloadavel (funcao PL/SQL, "
                    "tipo nao suportado) obriga a celula a devolver tudo. "
                    "Revisar o predicado, avaliar particionamento/zone map para "
                    "storage index e conferir CELL_OFFLOAD_PROCESSING. Interconnect "
                    "saturado por smart scan ineficiente afeta o cluster inteiro."),
            ))
        return sec

    def _cell_waits(self, headers, rows) -> Section:
        t = limiar("exadata.txt", "cell_read_ms")
        warn, crit = t.get("warn", 5.0), t.get("crit", 15.0)
        ci_evt = find_col(headers, "event")
        ci_ms = find_col(headers, "avg_ms", "avg")
        ci_w = find_col(headers, "total_waits", "waits")
        ci_inst = find_col(headers, "inst_id", "inst")

        sec = Section(title="Esperas de celula (Exadata)",
                      table_headers=headers, table_rows=rows[:15])
        pior_ms, pior_evt, pior_inst = 0.0, "", ""
        sevs = []
        for r in rows:
            evt = r[ci_evt].strip() if 0 <= ci_evt < len(r) else ""
            ms = num(r[ci_ms]) if 0 <= ci_ms < len(r) else None
            waits = num(r[ci_w]) if 0 <= ci_w < len(r) else 0.0
            if ms is None:
                continue
            # 'cell smart table scan' e agregado por sessao e nao tem a mesma
            # semantica de latencia de bloco - avaliar so leitura de bloco.
            if not re.search(r"cell (single|multiblock) block", evt, re.I):
                continue
            sev = (Severity.CRIT if ms >= crit else
                   Severity.WARN if ms >= warn else Severity.OK)
            sevs.append(sev)
            if ms > pior_ms and (waits or 0) > 0:
                pior_ms, pior_evt = ms, evt
                pior_inst = r[ci_inst].strip() if 0 <= ci_inst < len(r) else ""

        if pior_evt:
            sec.metrics.append(Metric("Pior leitura de bloco", f"{pior_ms:.2f} ms",
                                      severity=worst(sevs)))
        if worst(sevs) in (Severity.WARN, Severity.CRIT):
            sec.findings.append(Finding(
                title=f"Leitura de celula lenta: {pior_evt} em {pior_ms:.2f} ms",
                detail=(f"Instancia {pior_inst}. " if pior_inst else "")
                       + "Em Exadata a leitura de bloco servida por flash "
                         "costuma ficar abaixo de 1 ms.",
                severity=worst(sevs), impact=f"{pior_ms:.2f} ms",
                recommendation=(
                    "Checar no lado das celulas: 'cellcli -e list metriccurrent' "
                    "para latencia por celula, estado do flash cache "
                    "(FLASHCACHEMODE, celulas com flash degradado) e disco "
                    "predictive failure. Uma unica celula lenta degrada todo o "
                    "cluster - comparar as celulas entre si."),
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
                if "OFFLOAD" in marc or find_col(headers, "name") >= 0:
                    sections.append(self._offload(headers, rows))
                elif "CELULA" in marc or find_col(headers, "event") >= 0:
                    sections.append(self._cell_waits(headers, rows))

        if not sections:
            return [], ""
        achados = [f for s in sections for f in s.findings]
        if achados:
            resumo = ("Exadata: " + "; ".join(f.title for f in achados[:3]) + ".")
        else:
            resumo = "Exadata: offload e latencia de celula sem alerta."
        return sections, resumo
