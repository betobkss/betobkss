"""
Modelo de dados padronizado do framework de troubleshooting.

Toda analise (SQL, Locks, ADDM, Espaco, AWR...) produz um CaseResult com a
mesma estrutura, para que o gerador de relatorio (report.py) monte sempre o
mesmo layout de dashboard/PPT.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar


# ── Severidade / semaforo ──────────────────────────────────
class Severity:
    """Niveis de severidade -> cor do semaforo."""
    OK = "ok"        # verde
    WARN = "warn"    # laranja
    CRIT = "crit"    # vermelho
    INFO = "info"    # neutro (cinza/azul)

    # ClassVar: sao tabelas de consulta da propria classe, nao estado de
    # instancia. Severity nunca e instanciada - funciona como namespace.
    ORDER: ClassVar[dict] = {INFO: 0, OK: 1, WARN: 2, CRIT: 3}
    COLOR: ClassVar[dict] = {
        OK: "#22c55e",
        WARN: "#f59e0b",
        CRIT: "#ef4444",
        INFO: "#38bdf8",
    }
    LABEL: ClassVar[dict] = {
        OK: "OK",
        WARN: "ATENCAO",
        CRIT: "CRITICO",
        INFO: "INFO",
    }
    EMOJI: ClassVar[dict] = {
        OK: "\U0001f7e2",
        WARN: "\U0001f7e0",
        CRIT: "\U0001f534",
        INFO: "\U0001f535",
    }


def worst(severities) -> str:
    """Retorna a pior severidade de uma lista."""
    best = Severity.INFO
    for s in severities:
        if Severity.ORDER.get(s, 0) > Severity.ORDER.get(best, 0):
            best = s
    return best


@dataclass
class Metric:
    """Um indicador quantitativo (ex.: '%DB time', 'Elapsed', 'Used %')."""
    name: str
    value: str
    unit: str = ""
    severity: str = Severity.INFO
    hint: str = ""  # texto de threshold/observacao

    @property
    def display(self) -> str:
        v = f"{self.value}"
        if self.unit:
            v += f" {self.unit}"
        return v


@dataclass
class Finding:
    """Um achado da analise, com recomendacao opcional."""
    title: str
    detail: str = ""
    severity: str = Severity.INFO
    impact: str = ""          # ex.: '42% DB time'
    recommendation: str = ""


@dataclass
class Section:
    """Uma secao do relatorio: metricas + tabela + achados + imagem."""
    title: str
    metrics: list[Metric] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    # tabela generica: (headers, rows) onde rows e lista de listas de str
    table_headers: list[str] = field(default_factory=list)
    table_rows: list[list[str]] = field(default_factory=list)
    # severidade por linha da tabela (opcional, mesmo tamanho de table_rows)
    row_severity: list[str] = field(default_factory=list)
    note: str = ""
    images: list[str] = field(default_factory=list)  # caminhos de prints

    @property
    def severity(self) -> str:
        sev = [f.severity for f in self.findings]
        sev += [m.severity for m in self.metrics]
        sev += list(self.row_severity)
        return worst(sev) if sev else Severity.INFO


@dataclass
class CaseResult:
    """Resultado completo de um caso de troubleshooting."""
    case: str                      # nome do caso (pasta)
    kind: str                      # tipo (sql, locks, addm, space, awr, misc)
    title: str = ""               # titulo do relatorio
    subtitle: str = ""            # subtitulo (host/pdb/data...)
    summary: str = ""             # resumo executivo (texto - fallback)
    # resumo executivo estruturado: lista de (categoria, texto, severidade)
    summary_items: list = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    generated_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M"))
    meta: dict = field(default_factory=dict)
    # diferencas vs a execucao anterior (preenchido por report.record_and_diff)
    diff: dict = field(default_factory=dict)
    # analisadores que lancaram excecao nesta analise. Antes o erro so aparecia
    # como [AVISO] no terminal e sumia: o caso saia com uma secao a menos e
    # ninguem notava. Registrado aqui, a rotina de melhoria continua acusa.
    erros: list = field(default_factory=list)
    # Checks que a COLETA nao conseguiu executar (ORA-, timeout, query
    # bloqueada). Lista separada de `erros` de proposito: sao problemas de
    # naturezas diferentes e quem os le decide coisas diferentes. Analisador
    # que explode e defeito NOSSO, a corrigir no codigo; check que nao rodou e
    # evidencia que falta, a recoletar.
    #
    # Foram misturados por um momento e o efeito apareceu na hora: a rotina de
    # melhoria continua marcou o caso como `analisador_quebrou` quando o que
    # houve foi um ORA-14552 no banco. Rotulo errado em rotina de regressao
    # manda quem investiga para o arquivo errado.
    coleta_incompleta: list = field(default_factory=list)

    @property
    def severity(self) -> str:
        return worst([s.severity for s in self.sections]) if self.sections else Severity.INFO

    # Pesos do Health Score. CRIT/WARN por achado, mas com teto por categoria e
    # retorno decrescente entre categorias (ver score()).
    PEN_CRIT = 12
    PEN_WARN = 4
    SEC_PENALTY_CAP = 25            # nenhuma categoria isolada derruba o score
    SEC_WEIGHTS = (1.0, 0.6, 0.4, 0.25)
    SEC_WEIGHT_TAIL = 0.15

    @property
    def score(self) -> int:
        """Health Score 0-100 (estilo orachk / SolarWinds DPA).

        Penaliza por CATEGORIA (secao), nao por achado solto:
        - teto por categoria: 9 tablespaces acima do limite sao UM problema de
          espaco, nao nove;
        - retorno decrescente entre categorias: a pior pesa 100%, a segunda 60%,
          e assim por diante.
        Somando achado por achado o score saturava em 0 (qualquer ambiente com
        3 categorias criticas dava 0) e perdia o poder de comparar execucoes.
        """
        pen = []
        for s in self.sections:
            p = sum(self.PEN_CRIT if f.severity == Severity.CRIT else
                    self.PEN_WARN if f.severity == Severity.WARN else 0
                    for f in s.findings)
            if p:
                pen.append(min(float(p), float(self.SEC_PENALTY_CAP)))
        pen.sort(reverse=True)
        total = sum(p * (self.SEC_WEIGHTS[i] if i < len(self.SEC_WEIGHTS)
                         else self.SEC_WEIGHT_TAIL)
                    for i, p in enumerate(pen))
        return max(0, min(100, round(100 - total)))

    def add(self, section: Section) -> None:
        if section is not None:
            self.sections.append(section)
