"""
Analisador de SQL lento / SQL Monitor.

Entrada preferida: relatorio SQL Monitor em TEXTO
  (DBMS_SQLTUNE.REPORT_SQL_MONITOR(..., type=>'TEXT')) salvo em .txt/.lst.
Tambem aceita HTML (extrai o que conseguir apos remover tags).
Extrai: SQL ID, Status, Duration, tempos (Elapsed/CPU/IO), SQL text e as
operacoes do plano com maior % de atividade.
"""
from __future__ import annotations

import re

from ..model import Finding, Metric, Section, Severity, worst
from .base import Analyzer, find_col, num, parse_fixed_width_tables, read_text
from .regression import IMPACTO_CRIT_S, IMPACTO_WARN_S

_TAGS = re.compile(r"<[^>]+>")


class SqlMonitorAnalyzer(Analyzer):
    kind = "sql"
    title = "SQL lento / SQL Monitor"

    def matches(self, filename: str, head: str) -> int:
        score = 0
        fn = filename.lower()
        if re.search(r"sqlmon|sql_monitor|monitor|sqltune|sql_id|slow", fn):
            score += 45
        h = head.lower()
        if "sql monitoring report" in h:
            score += 50
        if "global information" in h or "global stats" in h:
            score += 25
        return min(score, 100)

    def _kv(self, text: str, label: str):
        m = re.search(rf"{label}\s*[:=]\s*(.+)", text, re.I)
        return m.group(1).strip() if m else None

    def analyze(self, files, meta):
        sections = []
        summary_bits = []
        overall_sev = Severity.INFO
        # Algum SQL ainda rodando? Decide o TEXTO do resumo, nao a severidade.
        algum_ativo = False

        for path in files:
            raw = read_text(path)
            is_html = "<" in raw and ">" in raw and "sql monitoring report" not in raw.lower()[:200]
            text = _TAGS.sub("", raw) if is_html else raw
            text = re.sub(r"&nbsp;|&#160;", " ", text)

            sql_id = self._kv(text, "SQL ID") or self._kv(text, "SQL_ID")
            status = self._kv(text, "Status")
            duration = self._kv(text, "Duration")
            dbtime = self._kv(text, "DB Time") or self._kv(text, "Elapsed Time")
            inst = self._kv(text, "Instance ID")
            user = self._kv(text, "User") or self._kv(text, "Username")

            sec = Section(title=f"SQL Monitor{f' - {sql_id}' if sql_id else ''}")

            # severidade heuristica (diferencia EXECUTING de DONE/concluido)
            sev = Severity.INFO
            is_error = bool(status and re.search(r"error", status, re.I))
            # So "concluido" e testado: qualquer outro status (em execucao,
            # enfileirado ou ilegivel) cai no ramo critico de proposito - um
            # SQL longo que ainda NAO terminou e incidente ativo.
            is_done = bool(status and re.search(r"\b(done|complete)", status, re.I))
            if is_error:
                sev = Severity.CRIT
            dur_s = num(duration) if duration else None
            # ── a duracao decide, ter terminado nao rebaixa ───
            # A regra era: concluido = ATENCAO (candidato a tuning), em
            # execucao = CRITICO (incidente ativo). A intencao faz sentido numa
            # TRIAGEM AO VIVO - e este analisador alimenta um relatorio
            # POST-MORTEM, feito sobre evidencia coletada depois.
            #
            # O problema: se o SQL ja havia terminado quando o coletor rodou e
            # ACIDENTE DE CRONOMETRAGEM, nao propriedade do problema. O mesmo
            # SQL, coletado dez segundos antes, saia CRITICO.
            #
            # E contradizia a regua do proprio framework. `regression.py`,
            # `baseline.py` e a linha do tempo de planos usam 300s de DB time
            # como piso de CRITICO; aqui um SQL de 10330s (2,9 HORAS) saia
            # ATENCAO por ter terminado. Medido na frota: 5 achados acima de
            # 300s classificados como ATENCAO, e um deles e a CAUSA RAIZ que um
            # DBA registrou a mao no HC_APPHO600 (996s) - enquanto o unico
            # CRITICO daquele relatorio era "4 esperas por checkpoint
            # incompleto", que nao tinha relacao com o incidente.
            #
            # Os limiares vem do `regression.py` de proposito: quatro secoes
            # falando de SQL custoso no mesmo relatorio nao podem discordar
            # sobre o que e critico.
            if dur_s and not is_done:
                algum_ativo = True
            if dur_s and dur_s >= IMPACTO_CRIT_S:
                sev = worst([sev, Severity.CRIT])
            elif dur_s and dur_s >= IMPACTO_WARN_S:
                # Nesta faixa a distincao continua valendo: um SQL de 60 a 300s
                # AINDA EM EXECUCAO pode estar longe do fim, e o que se ve e um
                # incidente em curso; concluido, e candidato a tuning.
                sev = worst([sev, Severity.WARN if is_done else Severity.CRIT])

            if sql_id:
                sec.metrics.append(Metric("SQL ID", sql_id))
            if status:
                sec.metrics.append(Metric("Status", status[:24],
                                          severity=Severity.CRIT if sev == Severity.CRIT else Severity.OK))
            if duration:
                sec.metrics.append(Metric("Duracao", duration[:16],
                                          severity=sev if dur_s else Severity.INFO))
            if dbtime:
                sec.metrics.append(Metric("DB Time", dbtime[:16]))
            if inst:
                sec.metrics.append(Metric("Instancia", inst[:8]))
            if user:
                sec.metrics.append(Metric("Usuario", user[:16]))

            # O SQL custoso precisa ser ACHADO, nao so metrica. Como metrica ele
            # pintava a secao de CRITICO e, ao mesmo tempo, ficava invisivel para
            # tudo que consome achados: o plano de acao priorizado, os destaques
            # do relatorio e o proprio Health Score (que soma por Finding). Um
            # SQL de 845s em execucao aparecia na tela e nao pesava no score.
            if sev in (Severity.WARN, Severity.CRIT):
                _quanto = (f"{dur_s:.0f}s" if dur_s else (duration or "").strip()[:16])
                sec.findings.append(Finding(
                    title=(f"SQL {sql_id or '?'} custoso"
                           + (f": {_quanto} de duracao" if _quanto else "")
                           + (" (concluido)" if is_done else " (em execucao)")),
                    detail=(f"Status '{status or '?'}'"
                            + (f", DB Time {dbtime.strip()}" if dbtime else "")
                            + (f", usuario {user.strip()}" if user else "") + "."),
                    severity=sev,
                    impact=_quanto,
                    recommendation=(
                        "SQL ja concluido: candidato a tuning, nao incidente "
                        "ativo. Comparar com a execucao boa (DBMS_XPLAN.DISPLAY_AWR) "
                        "antes de mexer."
                        if is_done else
                        "SQL ainda em execucao: confirmar com a aplicacao se o "
                        "tempo e esperado. Ver a operacao dominante do plano "
                        "abaixo e as estatisticas dos objetos envolvidos."),
                ))

            # SQL text
            mtxt = re.search(r"SQL Text\s*\n[-\s]*\n(.*?)(?:\n\s*\n|Global Information)", text, re.S | re.I)
            if mtxt:
                sql_text = re.sub(r"\s+", " ", mtxt.group(1)).strip()
                if sql_text:
                    sec.note = "SQL: " + sql_text[:600]

            # Plano / operacoes com maior atividade
            plan_added = False
            for headers, rows in parse_fixed_width_tables(text):
                ci_act = find_col(headers, "activity", "% activity", "activit")
                ci_op = find_col(headers, "operation", "plan", "name")
                if ci_op < 0:
                    continue
                # so tabelas que parecem plano
                if ci_act < 0 and find_col(headers, "rows", "cost", "estimated") < 0:
                    continue
                row_sev = []
                for r in rows:
                    a = num(r[ci_act]) if 0 <= ci_act < len(r) else None
                    row_sev.append(Severity.CRIT if (a or 0) >= 40 else
                                   Severity.WARN if (a or 0) >= 15 else Severity.OK)
                psec = Section(title="Plano de execucao (operacoes)",
                               table_headers=headers, table_rows=rows[:15],
                               row_severity=row_sev[:15])
                # top operacao por atividade
                if ci_act >= 0:
                    ranked = sorted(
                        [(num(r[ci_act]) or 0, r[ci_op] if 0 <= ci_op < len(r) else "?") for r in rows],
                        reverse=True)
                    for a, opname in ranked[:3]:
                        if a >= 15:
                            osev = Severity.CRIT if a >= 40 else Severity.WARN
                            # SQL ja concluido: rebaixa para ATENCAO (candidato a tuning, nao incidente ativo)
                            if is_done and not is_error and osev == Severity.CRIT:
                                osev = Severity.WARN
                            sev = worst([sev, osev])
                            reco = ("Avaliar indice/estatisticas/plano para esta operacao "
                                    "(SQL Tuning Advisor, criar/ajustar indice, coletar stats).")
                            if is_done:
                                reco += " (SQL ja concluido - candidato a tuning, nao incidente ativo)."
                            psec.findings.append(Finding(
                                title=f"Operacao '{opname.strip()[:60]}' concentra atividade",
                                severity=osev, impact=f"{a:.0f}% activity",
                                recommendation=reco,
                            ))
                sections.append(psec)
                plan_added = True
                break

            if not sec.metrics and not plan_added:
                # nao parseou nada util
                continue

            overall_sev = worst([overall_sev, sev])
            sections.insert(0, sec) if not plan_added else sections.append(sec)
            bit = f"SQL {sql_id or '?'}"
            if duration:
                bit += f" com duracao {duration}"
            if status:
                bit += f" (status {status})"
            summary_bits.append(bit)

        if not sections:
            return [], ""

        summary = (
            "; ".join(summary_bits[:3]) + ". "
            # O texto NAO pode afirmar "em execucao" so porque a severidade e
            # critica: desde que a duracao passou a decidir, um SQL CONCLUIDO
            # de 996s tambem e critico, e dizer que ele esta rodando manda o
            # DBA procurar uma sessao ativa que nao existe. O resumo diz o que
            # o STATUS diz, e a severidade vem separada.
            + ("SQL critico por duracao"
               + (" (ainda em execucao)" if algum_ativo else " (ja concluido)")
               + ": priorizar tuning (indice/estatisticas/plano)."
               if overall_sev == Severity.CRIT else
               "SQL de longa duracao ja concluido: candidato a tuning (nao e incidente ativo)."
               if overall_sev == Severity.WARN else
               "SQL sem indicadores criticos.")
        )
        return sections, summary
