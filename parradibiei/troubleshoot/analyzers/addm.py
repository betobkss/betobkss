"""
Analisador ADDM: extrai Findings (impacto %DB time) e Recomendacoes de um
relatorio ADDM em TEXTO ou HTML.
"""
from __future__ import annotations

import re

from ..model import Finding, Metric, Section, Severity, worst
from .base import Analyzer, read_text

_TAGS = re.compile(r"<[^>]+>")


class AddmAnalyzer(Analyzer):
    kind = "addm"
    title = "ADDM / Recomendacoes"

    def matches(self, filename: str, head: str) -> int:
        score = 0
        fn = filename.lower()
        if "addm" in fn:
            score += 60
        h = head.upper()
        if "ADDM" in h or "AUTOMATIC DATABASE DIAGNOSTIC" in h:
            score += 30
        if "FINDING" in h and "IMPACT" in h:
            score += 25
        return min(score, 100)

    def _sev_impact(self, pct) -> str:
        if pct is None:
            return Severity.INFO
        if pct >= 50:
            return Severity.CRIT
        if pct >= 25:
            return Severity.WARN
        return Severity.OK

    def analyze(self, files, meta):
        sections = []
        all_impacts = []

        for path in files:
            raw = read_text(path)
            text = _TAGS.sub("", raw) if "<" in raw and ">" in raw else raw
            text = re.sub(r"&nbsp;|&#160;", " ", text)

            sec = Section(title=f"ADDM - {path.rsplit(chr(92),1)[-1].rsplit('/',1)[-1]}")
            # Blocos de FINDING
            finding_re = re.compile(
                r"FINDING\s+\d+:\s*(\d+(?:\.\d+)?)\s*%\s*impact.*?(?:\((.*?)\))?\s*\n(.*?)"
                r"(?=FINDING\s+\d+:|\Z)", re.S | re.I)
            matches = list(finding_re.finditer(text))
            if not matches:
                # tenta formato "Finding: ... (NN% impact)"
                finding_re = re.compile(
                    r"Finding[:\s]+(.*?)\((\d+(?:\.\d+)?)\s*%\s*impact\)(.*?)(?=Finding[:\s]|\Z)",
                    re.S | re.I)
                for m in finding_re.finditer(text):
                    title = m.group(1).strip()[:120]
                    pct = float(m.group(2))
                    all_impacts.append(pct)
                    rec = self._first_reco(m.group(3))
                    sec.findings.append(Finding(
                        title=title, severity=self._sev_impact(pct),
                        impact=f"{pct:.0f}% DB time", recommendation=rec))
            else:
                for m in matches:
                    pct = float(m.group(1))
                    all_impacts.append(pct)
                    body = m.group(3).strip()
                    title = next((ln.strip() for ln in body.splitlines()
                                  if ln.strip() and not re.match(r"^[-=_.\s]+$", ln.strip())
                                  and not re.match(r"^(RECOMMENDATION|ACTION|RATIONALE)\b", ln.strip(), re.I)),
                                 "Finding ADDM")[:140]
                    rec = self._first_reco(body)
                    sec.findings.append(Finding(
                        title=title, severity=self._sev_impact(pct),
                        impact=f"{pct:.0f}% DB time", recommendation=rec))

            if sec.findings:
                sec.findings.sort(key=lambda f: -(float(re.match(r"[\d.]+", f.impact).group()) if f.impact else 0))
                mx = max(all_impacts) if all_impacts else None
                sec.metrics.append(Metric("Findings", str(len(sec.findings))))
                if mx is not None:
                    sec.metrics.append(Metric("Maior impacto", f"{mx:.0f}", "% DB time",
                                              severity=self._sev_impact(mx),
                                              hint=">=25% atencao, >=50% critico"))
                sections.append(sec)

        if not sections:
            return [], ""
        overall = worst([s.severity for s in sections])
        top = sorted(all_impacts, reverse=True)[:3]
        summary = (
            f"ADDM retornou {sum(len(s.findings) for s in sections)} finding(s). "
            + (f"Maiores impactos: {', '.join(f'{p:.0f}%' for p in top)} do DB time. " if top else "")
            + ("Ha finding critico (>=50%): priorizar a recomendacao principal." if overall == Severity.CRIT
               else "Findings de atencao a avaliar." if overall == Severity.WARN
               else "Sem findings relevantes.")
        )
        return sections, summary

    def _first_reco(self, body: str) -> str:
        m = re.search(r"RECOMMENDATION\s+\d+:\s*(.*)", body, re.I)
        if m:
            reco = m.group(1).strip()
            act = re.search(r"ACTION:\s*(.*)", body, re.I)
            if act:
                reco += " | ACAO: " + re.sub(r"\s+", " ", act.group(1)).strip()[:200]
            return reco[:300]
        return ""
