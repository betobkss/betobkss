"""
Analisador de Espaco: Tablespaces e ASM Diskgroups.

Entrada: saida de query SQL*Plus (queries/space.sql). Detecta colunas de uso
(% used / free) e aplica semaforo: >=90% critico, >=80% atencao.
"""
from __future__ import annotations

import os
import re
from typing import ClassVar

from ..model import Finding, Metric, Section, Severity, worst
from .base import Analyzer, find_col, num, parse_fixed_width_tables, read_text


class SpaceAnalyzer(Analyzer):
    kind = "space"
    title = "Espaco (Tablespace / ASM)"

    WARN = 80.0
    CRIT = 90.0

    # Sinal de que a tabela fala de OCUPACAO (e nao apenas cita tablespace).
    _USO_RE = re.compile(r"PCT_USED|PCT_ALLOC|PCT_FREE|USED_MB|USED_GB|FREE_MB|"
                         r"FREE_GB|USED%|PCT", re.I)

    def matches(self, filename: str, head: str) -> int:
        score = 0
        fn = filename.lower()
        if re.search(r"space|tablespace|espac|asm|diskgroup", fn):
            score += 50
        h = head.upper()
        if "TABLESPACE" in h:
            score += 30
        if "DISKGROUP" in h or "ASM" in h:
            score += 25
        tem_uso = bool(self._USO_RE.search(h))
        if tem_uso:
            score += 15
        else:
            # Tabela que só CITA tablespace (lista de datafiles, top segmentos)
            # nao e capacidade: cedendo o arquivo, o generico preserva o
            # subtitulo proprio dele em vez de rotular tudo como "Tablespaces".
            score = min(score, 10)
        return min(score, 100)

    def _sev_pct(self, pct) -> str:
        if pct is None:
            return Severity.INFO
        if pct >= self.CRIT:
            return Severity.CRIT
        if pct >= self.WARN:
            return Severity.WARN
        return Severity.OK

    def _pct_used(self, headers, row):
        """Calcula % usado, seja direto (coluna pct_used) ou por used/total."""
        ci_pct = find_col(headers, "pct_used", "used_pct", "%used", "pct", "used%", "usage")
        if ci_pct >= 0 and ci_pct < len(row):
            v = num(row[ci_pct])
            if v is not None:
                return v
        ci_used = find_col(headers, "used_gb", "used_mb", "used")
        ci_tot = find_col(headers, "total", "size", "alloc", "maxsize")
        if 0 <= ci_used < len(row) and 0 <= ci_tot < len(row):
            u, t = num(row[ci_used]), num(row[ci_tot])
            if u is not None and t:
                return u / t * 100.0
        ci_free = find_col(headers, "free", "pct_free")
        if 0 <= ci_free < len(row):
            f = num(row[ci_free])
            if (f is not None and "pct" in headers[ci_free].lower()) or (f is not None and f <= 100 and "%" in headers[ci_free]):
                return 100.0 - f
        return None

    def _name(self, headers, row):
        ci = find_col(headers, "tablespace", "name", "diskgroup", "ts")
        return row[ci] if 0 <= ci < len(row) else "?"

    # Rebaixamento quando a previsao de capacidade ja cobre o mesmo tablespace.
    _REBAIXA: ClassVar[dict] = {Severity.CRIT: Severity.WARN,
                                Severity.WARN: Severity.INFO}

    def _cobertos_pela_previsao(self, path) -> set:
        """Tablespaces que a secao de previsao ja reporta COM PRAZO.

        Para esses, o achado de '% de uso' aqui e o mesmo problema contado duas
        vezes no Health Score, e o prazo e a leitura mais acionavel - entao
        rebaixamos. Um tablespace cheio mas SEM crescimento (autoextend no teto,
        caso do TS_KPVD_01) nao aparece na previsao e segue critico aqui.
        Reusa o ForecastAnalyzer para nao duplicar a regra de severidade."""
        fc = os.path.join(os.path.dirname(os.path.abspath(path)), "forecast.txt")
        if not os.path.exists(fc):
            return set()
        from .forecast import ForecastAnalyzer
        fa = ForecastAnalyzer()
        nomes = set()
        for headers, rows in parse_fixed_width_tables(read_text(fc)):
            if find_col(headers, "dias_p_encher", "dias_ate_encher") < 0:
                continue
            for r in rows:
                sev = fa._sev(fa._dias(headers, r), fa._hist(headers, r))
                if sev in (Severity.WARN, Severity.CRIT):
                    nomes.add(str(fa._name(headers, r)).strip().upper())
        return nomes

    def _detalhe_autoextend(self, headers, row):
        """Contexto do autoextend: 87% do espaco ALOCADO com teto de 128 GB nao e
        a mesma coisa que 87% do teto. Sem isso o achado vira falso positivo."""
        ci_alloc = find_col(headers, "pct_alloc")
        ci_auto = find_col(headers, "autoext")
        partes = []
        if 0 <= ci_alloc < len(row):
            v = num(row[ci_alloc])
            if v is not None:
                partes.append(f"{v:.0f}% do espaco alocado")
        if 0 <= ci_auto < len(row) and row[ci_auto]:
            partes.append("autoextend " + ("ligado" if row[ci_auto].strip().upper()
                                           == "YES" else "DESLIGADO"))
        return "; ".join(partes)

    def analyze(self, files, meta):
        sections = []
        worst_items = []

        for path in files:
            text = read_text(path)
            fname = os.path.basename(path).lower()
            for headers, rows in parse_fixed_width_tables(text):
                if not headers:
                    continue
                has_ts = any("tablespace" in h.lower() for h in headers)
                is_asm = (
                    "asm" in fname
                    or any("diskgroup" in h.lower() or h.strip().upper() == "GROUP_NAME"
                           for h in headers)
                    or (not has_ts and find_col(headers, "state") >= 0
                        and find_col(headers, "type") >= 0)
                )
                tem_uso = find_col(headers, "pct_used", "pct_alloc", "pct_free",
                                   "used_mb", "used_gb", "free_mb", "free_gb",
                                   "pct") >= 0
                if not (is_asm or tem_uso):
                    continue

                row_sev, out_rows = [], []
                for r in rows:
                    pct = self._pct_used(headers, r)
                    sev = self._sev_pct(pct)
                    row_sev.append(sev)
                    out_rows.append(r)
                    if sev in (Severity.WARN, Severity.CRIT):
                        worst_items.append((self._name(headers, r), pct, sev))

                title = "ASM Diskgroups" if is_asm else "Tablespaces"
                sec = Section(title=title, table_headers=headers,
                              table_rows=out_rows, row_severity=row_sev)

                pcts = [self._pct_used(headers, r) for r in rows]
                pcts = [p for p in pcts if p is not None]
                if pcts:
                    mx = max(pcts)
                    sec.metrics.append(Metric("Maior uso", f"{mx:.1f}", "%",
                                              severity=self._sev_pct(mx),
                                              hint=">=80% atencao, >=90% critico"))
                    sec.metrics.append(Metric("Objetos", str(len(rows))))

                cobertos = set() if is_asm else self._cobertos_pela_previsao(path)
                rebaixados = 0
                for r in rows:
                    pct = self._pct_used(headers, r)
                    sev = self._sev_pct(pct)
                    if sev not in (Severity.CRIT, Severity.WARN):
                        continue
                    name = self._name(headers, r)
                    reco = ("Adicionar datafile/aumentar autoextend ou liberar espaco "
                            "urgentemente." if sev == Severity.CRIT else
                            "Planejar expansao; monitorar crescimento.")
                    if str(name).strip().upper() in cobertos:
                        sev = self._REBAIXA[sev]
                        rebaixados += 1
                        reco = ("Ver a secao de previsao de capacidade: la esta o prazo "
                                "estimado e o volume a provisionar.")
                    sec.findings.append(Finding(
                        # O achado diz de QUE tipo de espaco se trata. A secao
                        # ja separa ("ASM Diskgroups" x "Tablespaces"), mas o
                        # achado e consumido FORA dela - no plano de acao, nos
                        # destaques do relatorio e no motor de hipoteses -, e
                        # ali "DG_X com 88.8% de uso" era indistinguivel de uma
                        # tablespace. Quem le corre para o lugar errado, e o
                        # motor nao conseguia sustentar a hipotese de ASM: o
                        # vocabulario dela procura "diskgroup", palavra que o
                        # achado nunca dizia.
                        title=(f"Diskgroup ASM {name} com {pct:.1f}% de uso"
                               if is_asm else f"{name} com {pct:.1f}% de uso"),
                        detail=self._detalhe_autoextend(headers, r),
                        severity=sev, impact=f"{pct:.1f}% usado",
                        recommendation=reco,
                    ))

                if not is_asm and find_col(headers, "max_mb") >= 0:
                    sec.note = (
                        "PCT_USED e medido contra o TETO do autoextend (MAX_MB); "
                        "PCT_ALLOC e contra o que ja esta alocado (ALLOC_MB). Com "
                        "autoextend ligado, PCT_ALLOC alto so significa que o datafile "
                        "vai crescer - vira problema se AUTOEXT=NO ou se o "
                        "filesystem/ASM nao tiver espaco (ver diskgroups). "
                        "O prazo real esta na secao de previsao de capacidade.")
                    if rebaixados:
                        sec.note += (f" {rebaixados} achado(s) rebaixado(s) aqui por "
                                     "ja terem prazo estimado na previsao - o mesmo "
                                     "problema nao conta duas vezes no Health Score.")
                sections.append(sec)

        if not sections:
            return [], ""

        overall = worst([s.severity for s in sections])
        crit = [w for w in worst_items if w[2] == Severity.CRIT]
        warn = [w for w in worst_items if w[2] == Severity.WARN]
        summary = (
            (f"{len(crit)} objeto(s) CRITICO(S) (>=90%): "
             + ", ".join(f"{n} ({p:.0f}%)" for n, p, _ in crit) + ". " if crit else "")
            + (f"{len(warn)} em atencao (>=80%). " if warn else "")
            + ("Acao de espaco recomendada." if overall == Severity.CRIT else
               "Monitorar crescimento." if overall == Severity.WARN else
               "Uso de espaco saudavel.")
        )
        return sections, summary
