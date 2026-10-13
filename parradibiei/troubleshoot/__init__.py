"""
Framework de Troubleshooting padronizado - Oracle (DBA Master)
==============================================================

Generaliza o fluxo do AWR para QUALQUER analise de troubleshooting:
voce fornece a evidencia (HTML de AWR/ASH/SQL Monitor/ADDM, saidas de query
em .txt, prints) e o framework gera SEMPRE o mesmo relatorio padronizado:
Resumo executivo -> Semaforo -> Evidencias -> Achados e recomendacoes,
em dashboard HTML + PPTX, no mesmo visual do AWR.

Arquitetura:
    troubleshoot/model.py      -> dataclasses padrao (Metric, Finding, Section, CaseResult)
    troubleshoot/report.py     -> gera dashboard HTML e PPTX
    troubleshoot/analyzers/    -> um modulo por tipo (sqlmon, locks, addm, space)
    TROUBLESHOOT.py            -> orquestrador (le troubleshooting/<caso>/)
"""

from .model import (
    CaseResult,
    Finding,
    Metric,
    Section,
    Severity,
    worst,
)

__all__ = [
    "CaseResult",
    "Finding",
    "Metric",
    "Section",
    "Severity",
    "worst",
]
