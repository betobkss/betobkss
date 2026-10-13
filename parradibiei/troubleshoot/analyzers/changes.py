"""
Analisador de Change Tracking ("o que mudou?").

Monta uma CRONOLOGIA unica das mudancas do ambiente (restart de instancia, DDL,
coleta de estatisticas, parametros, patches) e a CRUZA com o dia do salto de
carga detectado nas series diarias (workload_daily / workload_module_daily).

Responde a primeira pergunta de qualquer RCA: o framework ja dizia o *dia* em
que a carga saltou, mas nao dizia *o que* aconteceu naquele dia.

Entrada: changes.txt (checks changes_* do mcp/catalog.json), com as colunas
uniformes QUANDO, TIPO, OBJETO, DETALHE - todos os blocos usam o mesmo layout
para que a cronologia seja uma simples uniao das tabelas.

Principio de severidade: **mudanca nao e incidente**. A cronologia sozinha e
INFO (contexto). Ela sobe para ATENCAO apenas quando cai na janela do salto de
carga - e ai deixa de ser lista e passa a ser suspeito.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta
from typing import ClassVar

from ..model import Finding, Metric, Section, Severity
from .base import Analyzer, find_col, parse_fixed_width_tables, read_text

# Arquivos de serie diaria usados para achar o dia do salto de carga.
FONTES_CARGA = ("workload_daily.txt", "workload_module_daily.txt")

_DIA_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


class ChangesAnalyzer(Analyzer):
    kind = "changes"
    title = "Mudancas no ambiente"

    MAX_LINHAS = 60          # cronologia exibida na tabela
    MAX_DETALHE = 6          # mudancas citadas no detalhe do achado

    # Ordem de relevancia no detalhe do achado. Patch e restart explicam um salto
    # muito melhor que uma coleta de estatisticas de rotina; sem esse peso, seis
    # linhas de STATS empurram o patch para fora da amostra (visto no DBSRV114,
    # onde o RU 19.31 de 21/07 antecedeu o salto de carga de 22/07).
    PESO_TIPO: ClassVar[dict] = {"PATCH": 0, "RESTART": 1, "PARAMETRO": 2,
                                 "DDL": 3, "STATS": 4}

    def matches(self, filename: str, head: str) -> int:
        score = 0
        fn = filename.lower()
        if re.search(r"changes|mudanca", fn):
            score += 60
        h = head.upper()
        if "QUANDO" in h and "TIPO" in h:
            score += 40
        if "OBJETO" in h and "DETALHE" in h:
            score += 20
        return min(score, 100)

    # ── leitura ──
    @staticmethod
    def _dia(quando: str):
        """YYYY-MM-DD de um 'QUANDO', ou None quando a linha nao tem data
        (caso do bloco de parametros modificados, que so tem ISMODIFIED)."""
        m = _DIA_RE.match(str(quando).strip())
        return m.group(1) if m else None

    def _janela(self, dia: str) -> set:
        """Dia do salto + dia anterior.

        Uma coleta de estatisticas ou um deploy no fim da noite de D-1 se
        manifesta na carga de D; olhar so o proprio dia perderia esse caso.
        """
        try:
            d = datetime.strptime(dia, "%Y-%m-%d")
        except (ValueError, TypeError):
            return {dia}
        return {dia, (d - timedelta(days=1)).strftime("%Y-%m-%d")}

    def _saltos(self, path) -> dict:
        """{dia: [series]} dos saltos de carga, lidos dos arquivos vizinhos."""
        base = os.path.dirname(os.path.abspath(path))
        fontes = [os.path.join(base, f) for f in FONTES_CARGA]
        fontes = [f for f in fontes if os.path.exists(f)]
        if not fontes:
            return {}
        try:
            from .workload import dias_de_salto
            return dias_de_salto(fontes)
        except Exception:
            return {}

    def analyze(self, files, meta):
        linhas = []          # (quando, tipo, objeto, detalhe)
        headers = ["QUANDO", "TIPO", "OBJETO", "DETALHE"]
        saltos: dict = {}

        for path in files:
            if not saltos:
                saltos = self._saltos(path)
            for hd, rows in parse_fixed_width_tables(read_text(path)):
                ci_q = find_col(hd, "quando", "data", "dia")
                ci_t = find_col(hd, "tipo")
                if ci_q < 0 or ci_t < 0:
                    continue
                ci_o = find_col(hd, "objeto", "object")
                ci_d = find_col(hd, "detalhe", "detail")

                def g(r, i):
                    return str(r[i]).strip() if 0 <= i < len(r) else ""
                for r in rows:
                    linhas.append((g(r, ci_q), g(r, ci_t), g(r, ci_o), g(r, ci_d)))

        if not linhas:
            return [], ""

        # Cronologia: mais recente primeiro; linhas sem data ao fim.
        linhas.sort(key=lambda x: (self._dia(x[0]) or "0000-00-00", x[0]), reverse=True)

        janela = set()
        for dia in saltos:
            janela |= self._janela(dia)
        suspeitas = [l for l in linhas if self._dia(l[0]) in janela] if janela else []

        sec = Section(title="Mudancas no ambiente (30d)",
                      table_headers=headers,
                      table_rows=[list(l) for l in linhas[:self.MAX_LINHAS]])
        # Colore na tabela o que caiu na janela do salto - o olho vai direto.
        sec.row_severity = [Severity.WARN if self._dia(l[0]) in janela else Severity.INFO
                            for l in linhas[:self.MAX_LINHAS]]

        por_tipo: dict = {}
        for _, tipo, _, _ in linhas:
            por_tipo[tipo] = por_tipo.get(tipo, 0) + 1
        sec.metrics.append(Metric("Mudancas (30d)", str(len(linhas))))
        if por_tipo:
            sec.metrics.append(Metric(
                "Por tipo",
                ", ".join(f"{t}:{n}" for t, n in sorted(por_tipo.items(),
                                                        key=lambda x: -x[1])[:4])))
        sec.metrics.append(Metric(
            "No dia do salto", str(len(suspeitas)),
            severity=Severity.WARN if suspeitas else Severity.OK,
            hint="mudancas na janela do salto de carga (dia e dia anterior)"))

        for dia, series in sorted(saltos.items(), reverse=True):
            na_janela = [l for l in linhas if self._dia(l[0]) in self._janela(dia)]
            if not na_janela:
                continue
            relevantes = sorted(na_janela,
                                key=lambda l: (self.PESO_TIPO.get(l[1], 9), l[0]))
            amostra = "; ".join(f"{l[1]} {l[0]} {l[2]}"
                                for l in relevantes[:self.MAX_DETALHE])
            resto = len(na_janela) - self.MAX_DETALHE
            if resto > 0:
                amostra += f" (+{resto} outra(s))"
            quais = ", ".join(sorted(set(series))[:3])
            sec.findings.append(Finding(
                title=f"{len(na_janela)} mudanca(s) na janela do salto de carga de {dia}",
                severity=Severity.WARN,
                impact=f"salto em {quais} no dia {dia}",
                detail=amostra,
                recommendation=(
                    "Confrontar estas mudancas com o time de aplicacao/gestao de "
                    "mudanca ANTES de tunar: coleta de estatisticas e DDL nesta data "
                    "explicam troca de plano; restart explica cache frio. "
                    "Comparar o plano do SQL afetado antes e depois desta data."),
            ))

        if not sec.findings:
            sec.note = ("Cronologia informativa: nenhuma mudanca coincide com salto de "
                        "carga. Mudanca por si nao e incidente - esta secao existe para "
                        "datar a causa quando houver um.")
        if any(self._dia(l[0]) is None for l in linhas):
            sec.note += (" Linhas com '(sem data)' vem de V$SYSTEM_PARAMETER.ISMODIFIED "
                         "(parametro alterado por comando na instancia, sem data no "
                         "dicionario) e por isso nao entram na correlacao.")

        if sec.findings:
            summary = (f"{len(suspeitas)} mudanca(s) na janela do salto de carga - "
                       "candidata(s) a causa raiz; validar com gestao de mudanca.")
        else:
            summary = (f"{len(linhas)} mudanca(s) em 30 dias, nenhuma coincidente com "
                       "salto de carga.")
        return [sec], summary
