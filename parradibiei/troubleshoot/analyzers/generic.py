"""
Analisador generico (fallback).

Usado pelo orquestrador para arquivos que nenhum analisador especializado
reconheceu. Renderiza qualquer tabela SQL*Plus como secao e destaca linhas com
ORA- errors. Garante que QUALQUER evidencia apareca no relatorio padronizado.
"""
from __future__ import annotations

import os
import re

from ..model import Finding, Metric, Section, Severity, worst
from .base import Analyzer, find_col, parse_fixed_width_tables, read_text

_ORA = re.compile(r"ORA-\d{4,5}")
_SUBT = re.compile(r"^===\s*(.+?)\s*===\s*$", re.M)
_SEV_NAME = re.compile(r"status|severidade|severity|estado|alert|recomenda|backup_status|stats_status", re.I)
_NAME_HINTS = ("name", "tablespace", "username", "segment", "table", "index",
               "component", "parametro", "parameter", "resource", "machine",
               "dest", "member", "object", "file")

_FALLBACK_REC = "Avaliar conforme o status reportado pelo check."
# Linha de '-----' do SQL*Plus: se existe, o check rodou e trouxe cabecalho de
# tabela; sem linhas de dados significa 'nenhuma ocorrencia' (resultado bom).
_SEP_LINE = re.compile(r"^\s*-{2,}[\s-]*$", re.M)


def _catalog_recs() -> dict:
    """Recomendacoes do catalogo MCP por arquivo de saida (cache em modulo).
    Mantem o catalogo como fonte unica de verdade tambem para o fallback."""
    global _RECS
    if _RECS is None:
        try:
            from mcp import recommendations_by_file
            _RECS = recommendations_by_file()
        except Exception:
            _RECS = {}
    return _RECS


_RECS = None


def _map_sev(v):
    """Mapeia o texto da coluna de severidade (OK/WARNING/CRITICAL/INFO...) do
    checkp.sql para a severidade do framework."""
    u = (str(v) if v is not None else "").strip().upper()
    if not u:
        return None
    if "CRITIC" in u or "FATAL" in u or "FAILED" in u:
        return Severity.CRIT
    if u.startswith("ERROR") or "ERRO" in u:
        return Severity.CRIT
    if "WARN" in u or "OUTDATED" in u or "NO STATS" in u or "UNUSABLE" in u:
        return Severity.WARN
    if u.startswith("OK"):
        return Severity.OK
    if "INFO" in u:
        return Severity.INFO
    return None


class GenericAnalyzer(Analyzer):
    kind = "generic"
    title = "Evidencia (generico)"

    def matches(self, filename: str, head: str) -> int:
        return 1  # baixissima prioridade; usado so como fallback explicito

    def _name_col(self, headers) -> int:
        for hint in _NAME_HINTS:
            ci = find_col(headers, hint)
            if ci >= 0:
                return ci
        return 0

    def analyze(self, files, meta):
        sections = []
        recs = _catalog_recs()
        for path in files:
            fname = os.path.basename(path)
            rec_file = recs.get(fname) or _FALLBACK_REC
            text = read_text(path)
            subtitles = _SUBT.findall(text)
            tables = parse_fixed_width_tables(text)
            erros = sorted(set(_ORA.findall(text)))
            added = False
            for i, (headers, rows) in enumerate(tables):
                if not headers or not rows:
                    continue
                # titulo: usa o subtitulo "=== ... ===" quando a contagem casa
                if len(subtitles) == len(tables):
                    title = subtitles[i]
                else:
                    title = fname + (f" (tabela {i+1})" if len(tables) > 1 else "")

                # coluna de severidade embutida (checkp.sql): status/severidade/...
                ci_sev = next((j for j, h in enumerate(headers)
                               if _SEV_NAME.search(h or "")), -1)
                ci_name = self._name_col(headers)

                row_sev, findings = [], []
                for r in rows:
                    sv = _map_sev(r[ci_sev]) if 0 <= ci_sev < len(r) else None
                    row_sev.append(sv or Severity.INFO)
                    if sv in (Severity.WARN, Severity.CRIT):
                        nm = r[ci_name] if 0 <= ci_name < len(r) else "?"
                        stat = r[ci_sev] if 0 <= ci_sev < len(r) else ""
                        findings.append(Finding(
                            title=f"{title}: {str(nm).strip()[:60]}",
                            severity=sv, impact=str(stat).strip()[:60],
                            recommendation=rec_file,
                        ))

                sec = Section(title=title, table_headers=headers, table_rows=rows[:25],
                              row_severity=(row_sev[:25] if ci_sev >= 0 else []))
                order = {Severity.CRIT: 0, Severity.WARN: 1}
                findings.sort(key=lambda f: order.get(f.severity, 2))
                sec.findings.extend(findings[:8])
                if ci_sev >= 0:
                    nc = sum(1 for s in row_sev if s == Severity.CRIT)
                    nw = sum(1 for s in row_sev if s == Severity.WARN)
                    if nc or nw:
                        sec.metrics.append(Metric("Alertas", f"{nc} crit / {nw} atencao",
                                                  severity=worst(row_sev)))
                if erros and not findings:
                    sec.findings.append(Finding(
                        title="Erros ORA- presentes: " + ", ".join(erros[:8]),
                        severity=Severity.WARN,
                        recommendation="Ver seccao de Alert Log ou detalhar cada erro.",
                    ))
                sections.append(sec)
                added = True

            if not added:
                # Titulo real do check (=== SUBTITULO ===) em vez do nome do arquivo
                title = subtitles[0] if subtitles else fname
                snippet = text.strip()[:800]
                if _SEP_LINE.search(text) and not erros:
                    # Cabecalho de tabela sem linhas: check executou e nada foi
                    # encontrado (ex.: nenhum objeto invalido) -> resultado OK.
                    sec = Section(title=title,
                                  note="Sem ocorrencias: o check nao retornou linhas.")
                    sec.metrics.append(Metric("Ocorrencias", "0", severity=Severity.OK))
                    sections.append(sec)
                elif snippet:
                    sec = Section(title=title, note=snippet)
                    if erros:
                        sec.findings.append(Finding(
                            title="Erros ORA- presentes: " + ", ".join(erros[:8]),
                            severity=Severity.WARN))
                    sections.append(sec)

        if not sections:
            return [], ""
        crit = sum(1 for s in sections if s.severity == Severity.CRIT)
        warn = sum(1 for s in sections if s.severity == Severity.WARN)
        summary = (f"{len(sections)} secao(oes) generica(s)."
                   + (f" {crit} critica(s)." if crit else "")
                   + (f" {warn} em atencao." if warn else "")
                   + (" Verifique os alertas destacados." if (crit or warn)
                      else " Sem alertas destacados."))
        return sections, summary
