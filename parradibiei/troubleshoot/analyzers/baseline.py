"""
Analisador Baseline / Compare Period.

Compara duas janelas de tempo (RECENTE vs BASELINE) por SQL para detectar:
- **Regressao de plano**: plan_hash_value mudou entre as janelas.
- **Regressao de tempo**: avg elapsed/exec subiu de forma relevante.

Entrada: saida da query 'compare_period' (mcp/catalog.json), com as colunas
SQL_ID, PLANO_BASE, PLANO_REC, AVG_S_BASE, AVG_S_REC, VAR_PCT, STATUS
(a query ja calcula STATUS; aqui geramos achados descritivos e severidade).
Fonte: DBA_HIST (requer Diagnostics Pack).
"""
from __future__ import annotations

import os
import re

from ..model import Finding, Metric, Section, Severity, worst
from .base import Analyzer, cell, find_col, limiar, num, parse_fixed_width_tables, read_text
from .regression import (
    IMPACTO_CRIT_S,
    IMPACTO_WARN_S,
    REGRESSAO_MIN_PCT,
    fmt_impacto,
)

# Pisos de materialidade (segundos). Alinhados com o check 'compare_period'
# em mcp/catalog.json (thresholds.min_avg_s / min_delta_s).
MIN_AVG_S = 0.05     # elapsed/exec recente minimo p/ considerar regressao

# Piso de aumento por EXECUCAO, usado so quando o impacto e desconhecido.
#
# Era 0.05s (50 ms), e 50 ms por execucao nao diz nada sozinho: pode custar 3
# segundos de DB time ou 30 horas, dependendo de quantas vezes o SQL roda - e
# a evidencia sem a coluna de execucoes nao permite saber qual dos dois. Medido
# na frota de 28 casos: 47 dos 161 achados desta secao (29%) tinham aumento
# abaixo de 100 ms por execucao, e 82 (51%) abaixo de 250 ms.
#
# 0.25s e a MEDIANA da frota, escolhida como piso pelo que ela significa na
# ausencia de frequencia: abaixo de um quarto de segundo por execucao o achado
# nao pode ser priorizado, e passa a competir por atencao com os que podem.
#
# Este piso e um paliativo com prazo: a coleta agora projeta `execs_rec` e
# `impacto_s`, e onde o impacto existe quem decide e ele (IMPACTO_WARN_S /
# IMPACTO_CRIT_S), nao este numero.
MIN_DELTA_S = 0.25

# O piso percentual e os limiares de IMPACTO vem do RegressionAnalyzer de
# proposito: as duas secoes falam do mesmo SQL no mesmo relatorio. Se
# divergirem, uma diz ATENCAO e a outra fica calada sobre a mesma linha - e o
# leitor nao sabe em qual acreditar. Era exatamente o caso: aquela secao
# ranqueava por segundos de DB time e DECLARAVA no cabecalho que "5% num SQL
# que roda 10.000x/dia custa mais que 700% num que roda 2x", enquanto esta
# ranqueava por percentual - a doutrina escrita ali valia so para ela.


class BaselineAnalyzer(Analyzer):
    kind = "baseline"
    title = "Baseline / Compare Period"

    def matches(self, filename: str, head: str) -> int:
        score = 0
        fn = filename.lower()
        # regressions.txt/planos.txt sao do SQL Health Check (RegressionAnalyzer),
        # com secoes e colunas proprias - nao tentar interpretar aqui.
        if fn.startswith(("regressions", "planos")) or "IMPACTO_S" in head.upper():
            return 0
        if re.search(r"compare|baseline|regress|period", fn):
            score += 55
        h = head.upper()
        if "AVG_S_REC" in h or "PLANO_REC" in h or "VAR_PCT" in h:
            score += 45
        if "COMPARE PERIOD" in h:
            score += 30
        return min(score, 100)

    @staticmethod
    def _limiares_impacto() -> tuple:
        """Limiares de impacto declarados no catalogo para o compare_period."""
        t = limiar("compare_period.txt", "impacto_s",
                   warn=IMPACTO_WARN_S, crit=IMPACTO_CRIT_S)
        return t.get("warn", IMPACTO_WARN_S), t.get("crit", IMPACTO_CRIT_S)

    def _sev_por_impacto(self, impacto_s, plano_mudou, var_pct) -> str:
        """Severidade por SEGUNDOS DE DB TIME perdidos - a regra do Method R.

        Espelha `RegressionAnalyzer._sev` de proposito, ate na promocao por
        troca de plano: as duas secoes falam dos mesmos SQL no mesmo relatorio,
        e uma divergencia aqui faria a mesma linha sair ATENCAO numa e CRITICO
        na outra.
        """
        warn_s, crit_s = self._limiares_impacto()
        # O SQL MELHOROU: plano novo mais rapido e o resultado desejado, e
        # reporta-lo como achado inverte o sinal do relatorio.
        if var_pct is not None and var_pct <= 0:
            return Severity.INFO
        i = impacto_s or 0.0
        if i >= crit_s:
            return Severity.CRIT
        if i >= warn_s:
            # Troca de plano promove: e a regressao mais acionavel que existe
            # (SQL Plan Baseline estabiliza sem mexer na aplicacao).
            return Severity.CRIT if plano_mudou else Severity.WARN
        # Abaixo do piso de prejuizo, so vira achado com piora percentual
        # material - e a troca de plano nao promove sozinha.
        if var_pct is not None and var_pct < REGRESSAO_MIN_PCT:
            return Severity.OK
        if i > 0:
            return Severity.WARN if plano_mudou else Severity.OK
        return Severity.INFO

    def _sev(self, var_pct, plano_mudou, a_base=None, a_rec=None,
             impacto_s=None) -> str:
        """Severidade da regressao.

        Com `impacto_s` (coleta nova) quem decide e o DB time perdido. Sem ele
        (evidencia coletada antes desta mudanca) resta o percentual com piso
        por execucao - uma leitura cega a frequencia, que e o que esta secao
        fazia para TODA linha.

        Percentual isolado engana: 0.001s -> 0.005s e +400% mas sao 4ms, nao um
        incidente. So consideramos regressao quando o tempo recente e o aumento
        absoluto passam de MIN_AVG_S/MIN_DELTA_S. Mudanca de plano que ficou mais
        rapida (ou igual) tambem nao e regressao."""
        if impacto_s is not None:
            return self._sev_por_impacto(impacto_s, plano_mudou, var_pct)
        v = var_pct if var_pct is not None else 0
        # Tempo recente insignificante -> nunca e achado
        if a_rec is not None and a_rec < MIN_AVG_S:
            return Severity.INFO
        # Aumento absoluto insignificante -> nunca e achado
        if a_base is not None and a_rec is not None and (a_rec - a_base) < MIN_DELTA_S:
            return Severity.INFO
        # O SQL MELHOROU (ou ficou igual): nao e regressao, nem com troca de
        # plano. Ate aqui isso so era barrado quando a tabela trazia as colunas
        # de tempo absoluto; quando vinha so o VAR_PCT, os dois guardas acima
        # eram pulados e o `or plano_mudou` la embaixo promovia a plano novo
        # MAIS RAPIDO a "regressao" - com o ganho impresso como "-45% elapsed".
        if v <= 0:
            return Severity.INFO
        # Piora abaixo do piso: nao vira achado nem com troca de plano.
        if v < REGRESSAO_MIN_PCT:
            return Severity.OK
        if plano_mudou and v >= 50:
            return Severity.CRIT          # regressao de plano com piora
        if v >= 100:
            return Severity.CRIT          # tempo dobrou
        return Severity.WARN

    def analyze(self, files, meta):
        sections = []
        summary_bits = []
        overall = Severity.INFO

        for path in files:
            text = read_text(path)
            # Quando o SQL Health Check rodou (regressions.txt), ELE e a fonte
            # autoritativa da regressao de SQL: traz impacto em segundos, objetos,
            # planos e acoes. Aqui rebaixamos para nao contar o mesmo SQL duas
            # vezes no Health Score (era o que zerava o score).
            secundario = os.path.exists(
                os.path.join(os.path.dirname(os.path.abspath(path)), "regressions.txt"))
            for headers, rows in parse_fixed_width_tables(text):
                ci_sql = find_col(headers, "sql_id")
                ci_var = find_col(headers, "var_pct", "variacao", "var")
                ci_base = find_col(headers, "avg_s_base", "base_s", "avg_base")
                ci_rec = find_col(headers, "avg_s_rec", "rec_s", "avg_rec")
                ci_pbase = find_col(headers, "plano_base", "plan_base", "phv_base")
                ci_prec = find_col(headers, "plano_rec", "plan_rec", "phv_rec")
                # Colunas da coleta nova. `impacto_s` = (elapsed/exec recente -
                # base) x execucoes da janela recente, ou seja os SEGUNDOS DE DB
                # TIME que a piora custou em 24h. Sem elas a secao so tem
                # percentual, que nao distingue um SQL que roda 2x/dia de um que
                # roda 2M - e era o unico criterio que ela tinha.
                ci_imp = find_col(headers, "impacto_s", "impacto")
                ci_execs = find_col(headers, "execs_rec", "execucoes", "execs")
                if ci_sql < 0 or ci_rec < 0:
                    continue

                row_sev = []
                sec = Section(title="Compare Period (regressao de plano/tempo)",
                              table_headers=headers, table_rows=rows[:25])
                worst_var = 0
                impacto_total = 0.0
                achados_com_impacto: list = []
                for r in rows:
                    sql_id = str(cell(r, ci_sql)).strip()
                    var_pct = num(cell(r, ci_var)) if ci_var >= 0 else None
                    a_base = num(cell(r, ci_base)) if ci_base >= 0 else None
                    a_rec = num(cell(r, ci_rec)) if ci_rec >= 0 else None
                    imp = num(cell(r, ci_imp)) if ci_imp >= 0 else None
                    execs = num(cell(r, ci_execs)) if ci_execs >= 0 else None
                    if var_pct is None and a_base and a_rec:
                        var_pct = (a_rec - a_base) / a_base * 100.0
                    # Impacto derivado quando a coleta trouxe execucoes mas nao
                    # a coluna pronta: melhor calcular que descartar o dado.
                    if imp is None and execs is not None \
                            and a_base is not None and a_rec is not None:
                        imp = (a_rec - a_base) * execs
                    pbase = str(cell(r, ci_pbase)).strip()
                    prec = str(cell(r, ci_prec)).strip()
                    # PHV 0/vazio nao e plano real (ex.: bloco PL/SQL): ignora
                    plano_mudou = bool(pbase and prec and pbase != prec
                                       and num(pbase) and num(prec))
                    sev = self._sev(var_pct, plano_mudou, a_base, a_rec, imp)
                    if secundario and sev == Severity.CRIT:
                        sev = Severity.WARN
                    row_sev.append(sev)
                    if var_pct is not None and sev != Severity.INFO:
                        worst_var = max(worst_var, var_pct)
                    if imp and sev in (Severity.WARN, Severity.CRIT):
                        impacto_total += imp
                    if sev in (Severity.WARN, Severity.CRIT):
                        motivo = []
                        if plano_mudou:
                            motivo.append(f"plano {pbase}->{prec}")
                        if var_pct is not None:
                            motivo.append(f"elapsed {a_base}->{a_rec}s ({var_pct:+.0f}%)")
                        # O impacto entra no TITULO quando existe: e o numero
                        # que decide a ordem de trabalho, e titulo que so mostra
                        # percentual faz o leitor priorizar pelo criterio errado.
                        if imp is not None:
                            motivo.append(f"+{fmt_impacto(imp)} de DB time em 24h"
                                          + (f" em {execs:,.0f} execucoes".replace(",", ".")
                                             if execs else ""))
                        achado = Finding(
                            title=f"SQL {sql_id}: regressao ({'; '.join(motivo)})",
                            severity=sev,
                            impact=(f"+{fmt_impacto(imp)} de DB time" if imp is not None
                                    else f"{var_pct:+.0f}% elapsed" if var_pct is not None
                                    else "regressao"),
                            recommendation=(
                                "Regressao de PLANO: comparar planos (DBMS_XPLAN.DISPLAY_AWR), "
                                "avaliar SQL Plan Baseline/Profile e estatisticas."
                                if plano_mudou else
                                "Aumento de tempo: revisar estatisticas, volume de dados e "
                                "contencao; comparar execucoes por snapshot (DBA_HIST_SQLSTAT)."),
                        )
                        sec.findings.append(achado)
                        achados_com_impacto.append((imp, achado))
                tem_impacto = ci_imp >= 0 or ci_execs >= 0
                if secundario:
                    sec.note = ("Visao por PERCENTUAL, complementar. A priorizacao "
                                "por impacto (segundos de DB time), objetos, planos "
                                "e acoes prontas esta na secao 'Regressao de SQL "
                                "(top por impacto)'. Util aqui: blocos PL/SQL "
                                "(PHV 0), que aquela secao nao cobre.")
                elif not tem_impacto:
                    # Declarar a limitacao em vez de deixar o leitor supor que a
                    # ordem da tabela e ordem de prioridade. Sem execucoes, o
                    # primeiro da lista pode custar menos DB time que o ultimo.
                    sec.note = ("Evidencia coletada SEM a coluna de execucoes: esta "
                                "ordenacao e por aumento de tempo POR EXECUCAO e nao "
                                "distingue um SQL que roda 2x/dia de um que roda 2 "
                                "milhoes - o impacto real em DB time nao pode ser "
                                "calculado. Uma nova coleta traz `execs_rec` e "
                                "`impacto_s` e passa a priorizar por segundos "
                                "perdidos (Method R).")
                sec.row_severity = row_sev[:25]
                # Achado de maior prejuizo primeiro: a secao e uma fila de
                # trabalho, e ordenar por percentual poe na frente o SQL que
                # piorou muito e custa pouco.
                #
                # A ordenacao usa o impacto guardado ao lado do achado, e nao o
                # numero lido de volta do titulo: titulo e texto para humano, e
                # reparsear o proprio texto quebra calado no dia em que alguem
                # mudar a frase - deixando a fila de trabalho em ordem aleatoria
                # sem nenhum sinal de erro.
                if tem_impacto:
                    ordenados = [f for _, f in sorted(
                        achados_com_impacto, key=lambda p: -(p[0] or 0.0))]
                    sec.findings[:] = ordenados
                if impacto_total:
                    warn_s, crit_s = self._limiares_impacto()
                    sec.metrics.append(Metric(
                        "DB time perdido", fmt_impacto(impacto_total),
                        severity=worst(row_sev) if row_sev else Severity.INFO,
                        hint=f">={warn_s:.0f}s atencao, >={crit_s:.0f}s "
                             "(ou plano+piora) critico - em 24h"))
                if worst_var:
                    sec.metrics.append(Metric(
                        "Pior variacao", f"{worst_var:+.0f}", "%",
                        severity=worst(row_sev) if row_sev else Severity.INFO,
                        # A legenda tem de dizer o criterio que DECIDE. Com
                        # impacto, percentual e contexto e nao veredito.
                        hint=("contexto: a severidade sai do DB time perdido"
                              if tem_impacto else
                              ">=50% atencao, >=100% ou plano+piora critico")))
                nplan = sum(1 for f in sec.findings if "plano" in f.title)
                sec.metrics.append(Metric("SQLs com regressao", str(len(sec.findings))))
                sections.append(sec)
                overall = worst([overall, sec.severity])
                if sec.findings:
                    summary_bits.append(f"{len(sec.findings)} SQL(s) com regressao"
                                        + (f" ({nplan} por mudanca de plano)" if nplan else ""))

        if not sections:
            return [], ""
        summary = ("; ".join(summary_bits[:2]) + ". " if summary_bits else "")
        summary += ("Regressao critica: priorizar analise de plano/estatisticas."
                    if overall == Severity.CRIT else
                    "Regressao em atencao: monitorar e comparar planos."
                    if overall == Severity.WARN else
                    "Sem regressao relevante entre as janelas.")
        return sections, summary
