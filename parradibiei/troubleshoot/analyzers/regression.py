"""
Analisador do SQL Health Check de regressao (regressions.txt / planos.txt).

Le a saida de `troubleshoot.sqlhc` e transforma em achados priorizados por
**impacto absoluto em segundos** (Method R), nao por percentual: um SQL que
piorou 5% mas roda 10.000x/dia custa mais DB time que um que piorou 700% e
roda 2x. A recomendacao aponta a acao concreta e o script `acoes.sql`.

Severidade por impacto (segundos de DB time perdidos na janela):
  >= 300s  CRITICO   (5+ minutos de DB time perdidos)
  >=  60s  ATENCAO
  <   60s  OK/INFO
Mudanca de plano com piora sobe um nivel (regressao de plano e acionavel:
SQL Plan Baseline resolve rapido).
"""
from __future__ import annotations

import os
import re

from ..model import Finding, Metric, Section, Severity, worst
from .base import Analyzer, cell, find_col, num, parse_fixed_width_tables, read_text

IMPACTO_CRIT_S = 300.0
IMPACTO_WARN_S = 60.0

# Piso de materialidade em PERCENTUAL, aplicado so a quem NAO passa do piso de
# prejuizo absoluto (IMPACTO_WARN_S). E o que separa "piorou de verdade" de
# ruido: uma troca de plano de +5% e 0.12s virava ATENCAO no relatorio, e um
# achado desses ensina o DBA a ignorar a secao inteira.
#
# Nao e um corte de 30% puro de proposito. O ranking aqui e por impacto
# ABSOLUTO (Method R): 5% num SQL que roda 10.000x/dia custa mais DB time que
# 700% num que roda 2x. O percentual so decide o que fazer com a cauda curta.
REGRESSAO_MIN_PCT = 30.0

_SEM_OCORRENCIA = re.compile(r"sem ocorrencias", re.I)
# sql_id do Oracle: 13 caracteres em base32 minuscula.
_SQL_ID = re.compile(r"[0-9a-z]{13}")


def fmt_impacto(segundos) -> str:
    """Segundos de DB time com precisao suficiente para o numero nao mentir.

    Com `:.0f` fixo, uma troca de plano de 0.12s virava o titulo
    "+0s de DB time" - um achado de ATENCAO exibindo zero, que desmente a si
    mesmo e ensina o DBA a ignorar a secao. O piso de materialidade do coletor
    e 0.05s, entao sub-segundo aqui e legitimo: o que faltava era mostra-lo.
    """
    v = float(segundos or 0.0)
    if v >= 10:
        return f"{v:.0f}s"
    if v >= 1:
        return f"{v:.1f}s"
    return f"{v:.2f}s"


class RegressionAnalyzer(Analyzer):
    kind = "regression"
    title = "SQL Health Check - Regressao"

    def matches(self, filename: str, head: str) -> int:
        fn = filename.lower()
        h = head.upper()
        if fn.startswith("regressions"):
            return 100
        if fn.startswith("planos"):
            return 100
        if "TOP REGRESSOES POR IMPACTO" in h or "IMPACTO_S" in h:
            return 95
        return 0

    def _sev(self, impacto_s, plano_mudou: bool, var_pct=None) -> str:
        i = impacto_s if impacto_s is not None else 0
        # O SQL MELHOROU (ou ficou igual): nao e regressao, nem com troca de
        # plano. Plano novo mais rapido e o resultado desejado - reporta-lo como
        # achado inverte o sinal do relatorio.
        if var_pct is not None and var_pct <= 0:
            return Severity.INFO
        if i >= IMPACTO_CRIT_S:
            return Severity.CRIT
        if i >= IMPACTO_WARN_S:
            return Severity.CRIT if plano_mudou else Severity.WARN
        # Abaixo do piso de prejuizo: so vira achado com piora percentual
        # material. A troca de plano nao promove sozinha - era por ela que o
        # ruido de 0.12s entrava.
        if var_pct is not None and var_pct < REGRESSAO_MIN_PCT:
            return Severity.OK
        if i > 0:
            return Severity.WARN if plano_mudou else Severity.OK
        return Severity.INFO

    def _rec(self, plano_mudou: bool, tem_full_scan: bool, stats_ruim: bool) -> str:
        if plano_mudou:
            return ("Regressao de PLANO: comparar os planos em planos.txt "
                    "(DBMS_XPLAN.DISPLAY_AWR). Para estabilizar rapido, fixar o "
                    "plano bom com SQL Plan Baseline "
                    "(DBMS_SPM.LOAD_PLANS_FROM_AWR) e so depois tratar a causa.")
        if stats_ruim:
            return ("Estatisticas defasadas nos objetos do SQL: rodar os comandos "
                    "DBMS_STATS de acoes.sql (ja gerados por objeto) e reavaliar.")
        if tem_full_scan:
            return ("Mesmo plano, mais tempo, com FULL SCAN no caminho: avaliar o "
                    "indice candidato sugerido em acoes.sql (validar seletividade) "
                    "ou particionamento/purga se a tabela cresceu.")
        return ("Mesmo plano, mais tempo: tipicamente CRESCIMENTO DE DADOS ou "
                "contencao (I/O, CPU, lock). Comparar volume da tabela e a wait "
                "class dominante do periodo antes de mexer no SQL.")

    @staticmethod
    def _planos_do_bloco(bloco: str) -> list:
        """[{plano, desde, ate, execs, avg_s, total_s}] de um bloco de SQL."""
        fora = []
        for headers, rows in parse_fixed_width_tables(bloco):
            ci = {k: find_col(headers, k) for k in
                  ("plano", "desde", "ate", "execs", "avg_s", "total_s")}
            if ci["plano"] < 0 or ci["avg_s"] < 0:
                continue
            for r in rows:
                avg = num(cell(r, ci["avg_s"]))
                if avg is None:
                    continue
                fora.append({
                    "plano": str(cell(r, ci["plano"])).strip(),
                    "desde": str(cell(r, ci["desde"])).strip(),
                    "ate": str(cell(r, ci["ate"])).strip(),
                    "execs": num(cell(r, ci["execs"])) or 0.0,
                    "avg_s": avg,
                    "total_s": num(cell(r, ci["total_s"])) or 0.0,
                })
        return fora

    def _secao_timeline(self, text: str):
        """Historico de planos por SQL, e o plano melhor que sobrou no AWR.

        A pergunta acionavel nao e "o plano mudou?" - as outras duas secoes ja
        respondem isso. E "existe um plano melhor AINDA NO REPOSITORIO?".
        Quando existe, a correcao e fixa-lo com DBMS_SPM.LOAD_PLANS_FROM_AWR:
        minutos, sem tocar a aplicacao, e reversivel. Sem esta leitura a
        recomendacao possivel era a generica "compare os planos".
        """
        sec = Section(title="Historico de planos por SQL (30d)")
        linhas, sev_linhas = [], []
        warn_s, crit_s = IMPACTO_WARN_S, IMPACTO_CRIT_S
        for bloco in re.split(r"^---\s+([0-9a-z]{13})\s+---\s*$", text,
                              flags=re.M)[1:]:
            if _SQL_ID.fullmatch(bloco.strip()):
                sql_id = bloco.strip()
                continue
            planos = self._planos_do_bloco(bloco)
            if len(planos) < 2:
                continue
            # Plano ATUAL = o que rodou mais recentemente. Nao e o de maior
            # volume: um plano ruim que acabou de entrar tem pouco volume e e
            # exatamente o que interessa.
            atual = max(planos, key=lambda p: p["ate"])
            # Candidato = o mais rapido ENTRE OS OUTROS, e com execucoes
            # suficientes para o numero significar algo. Plano que rodou 2
            # vezes pode ter sido rapido por cache, nao por ser melhor.
            outros = [p for p in planos if p["plano"] != atual["plano"]
                      and p["execs"] >= 10]
            melhor = min(outros, key=lambda p: p["avg_s"]) if outros else None
            for p in planos:
                linhas.append([sql_id, p["plano"], p["desde"], p["ate"],
                               f"{p['execs']:.0f}", f"{p['avg_s']:.3f}",
                               f"{p['total_s']:.0f}",
                               "ATUAL" if p is atual else ""])
                sev_linhas.append(Severity.INFO)
            if not melhor or melhor["avg_s"] <= 0:
                continue
            ratio = atual["avg_s"] / melhor["avg_s"]
            # Ganho = o DB time que o plano melhor teria poupado no volume que
            # o plano atual ja rodou. E a mesma regua de impacto das outras
            # secoes, para as tres nao discordarem do mesmo SQL.
            ganho = (atual["avg_s"] - melhor["avg_s"]) * atual["execs"]
            if ratio < 1.0 + REGRESSAO_MIN_PCT / 100.0 or ganho <= 0:
                continue
            sev = (Severity.CRIT if ganho >= crit_s else
                   Severity.WARN if ganho >= warn_s else Severity.OK)
            if sev == Severity.OK:
                continue
            sec.findings.append(Finding(
                title=(f"SQL {sql_id}: plano {atual['plano']} e "
                       f"{ratio:.1f}x mais lento que o {melhor['plano']}, "
                       f"que ainda esta no AWR"),
                severity=sev,
                impact=f"+{fmt_impacto(ganho)} de DB time",
                detail=(f"O plano {atual['plano']} roda desde "
                        f"{atual['desde']} a {atual['avg_s']:.3f}s/exec. O "
                        f"plano {melhor['plano']} rodou de {melhor['desde']} a "
                        f"{melhor['ate']} a {melhor['avg_s']:.3f}s/exec em "
                        f"{melhor['execs']:.0f} execucoes. No volume atual "
                        f"({atual['execs']:.0f} execucoes), o plano antigo "
                        f"teria poupado {fmt_impacto(ganho)} de DB time."),
                recommendation=(
                    f"O plano bom ainda esta no repositorio: fixe-o com "
                    f"DBMS_SPM.LOAD_PLANS_FROM_AWR (sql_id {sql_id}, "
                    f"plan_hash_value {melhor['plano']}) para estabilizar em "
                    "minutos, sem tocar a aplicacao e de forma reversivel. "
                    f"Depois investigue por que mudou em {atual['desde']} - "
                    "coleta de estatisticas, mudanca de parametro ou volume - "
                    "porque o baseline trata o sintoma."),
            ))
        if not linhas:
            return None
        sec.table_headers = ["SQL_ID", "PLANO", "DESDE", "ATE", "EXECS",
                             "AVG_S", "TOTAL_S", ""]
        sec.table_rows = linhas[:40]
        sec.row_severity = sev_linhas[:40]
        sec.note = ("Planos que cada SQL usou nos ultimos 30 dias, com a data em"
                    " que cada um entrou. A data e o que se cruza com deploy, "
                    "coleta de estatistica e mudanca de parametro. Quando um "
                    "plano antigo era mais rapido e ainda esta no AWR, ele "
                    "aparece como achado: da para fixar com SQL Plan Baseline.")
        sec.metrics.append(Metric(
            "SQL com troca de plano",
            str(len({linha[0] for linha in linhas}))))
        if sec.findings:
            sec.metrics.append(Metric(
                "Com plano melhor no AWR", str(len(sec.findings)),
                severity=worst([f.severity for f in sec.findings]),
                hint=f">={warn_s:.0f}s de ganho atencao, >={crit_s:.0f}s critico"))
        return sec

    def analyze(self, files, meta):
        sections = []
        summary_bits = []
        overall = Severity.INFO
        case_dir = os.path.dirname(files[0]) if files else ""
        tem_acoes = os.path.exists(os.path.join(case_dir, "acoes.sql"))

        for path in files:
            fname = os.path.basename(path).lower()
            text = read_text(path)

            # planos_timeline.txt: qual plano rodou QUANDO, e se existe um
            # melhor ainda no AWR. Tem de vir ANTES do ramo `planos*`, que
            # casaria pelo prefixo e trataria a timeline como texto de apoio.
            if fname.startswith("planos_timeline"):
                sec = self._secao_timeline(text)
                if sec:
                    sections.append(sec)
                    overall = worst([overall, sec.severity])
                    if sec.findings:
                        summary_bits.append(
                            f"{len(sec.findings)} SQL com plano melhor no AWR")
                continue

            # planos.txt e evidencia de apoio (DISPLAY_AWR): referencia, nao tabela
            if fname.startswith("planos"):
                nplanos = text.count("| plano ")
                if nplanos:
                    sec = Section(
                        title="Planos de execucao (base x recente)",
                        note=(f"{nplanos} plano(s) capturados via "
                              "DBMS_XPLAN.DISPLAY_AWR em planos.txt, para comparar "
                              "o plano da janela BASE com o da RECENTE."))
                    sec.metrics.append(Metric("Planos capturados", str(nplanos)))
                    sections.append(sec)
                continue

            if _SEM_OCORRENCIA.search(text):
                sec = Section(
                    title="Regressao de SQL (top por impacto)",
                    note=("Sem ocorrencias: nenhuma regressao material de SQL de "
                          "aplicacao na janela (criterios de materialidade "
                          "aplicados: >= 0.05s/exec e piora >= 0.05s)."))
                sec.metrics.append(Metric("SQLs com regressao", "0",
                                          severity=Severity.OK))
                sections.append(sec)
                overall = worst([overall, Severity.OK])
                continue

            # objetos com estatisticas ruins (para direcionar a recomendacao)
            stats_ruins = set()
            full_scan_sqls = set()
            for headers, rows in parse_fixed_width_tables(text):
                ci_stale = find_col(headers, "stale_stats")
                ci_dias = find_col(headers, "dias_sem_stats")
                if ci_stale < 0:
                    continue
                for r in rows:
                    st = str(r[ci_stale]).strip().upper() if ci_stale < len(r) else ""
                    dias = num(r[ci_dias]) if 0 <= ci_dias < len(r) else None
                    if st == "YES" or (dias is not None and dias > 7):
                        stats_ruins.add("__any__")
            for m in re.finditer(r"---\s+([0-9a-z]{13})\s+\(plano", text):
                full_scan_sqls.add(m.group(1))

            for headers, rows in parse_fixed_width_tables(text):
                ci_sql = find_col(headers, "sql_id")
                ci_imp = find_col(headers, "impacto_s", "impacto")
                if ci_sql < 0 or ci_imp < 0:
                    continue                     # so a tabela do top de regressoes

                ci_var = find_col(headers, "var_pct")
                ci_base = find_col(headers, "avg_s_base")
                ci_rec = find_col(headers, "avg_s_rec")
                ci_pb = find_col(headers, "plano_base")
                ci_pr = find_col(headers, "plano_rec")
                ci_sch = find_col(headers, "schema")
                ci_ex = find_col(headers, "execs_rec")

                sec = Section(title="Regressao de SQL (top por impacto)",
                              table_headers=headers, table_rows=rows[:25])
                row_sev = []
                impacto_total = 0.0
                nplano = 0
                for r in rows:
                    sql_id = str(cell(r, ci_sql)).strip()
                    imp = num(cell(r, ci_imp))
                    var = num(cell(r, ci_var)) if ci_var >= 0 else None
                    a_base = num(cell(r, ci_base)) if ci_base >= 0 else None
                    a_rec = num(cell(r, ci_rec)) if ci_rec >= 0 else None
                    execs = num(cell(r, ci_ex)) if ci_ex >= 0 else None
                    pb, pr = str(cell(r, ci_pb)).strip(), str(cell(r, ci_pr)).strip()
                    schema = str(cell(r, ci_sch)).strip() if ci_sch >= 0 else ""
                    plano_mudou = bool(pb and pr and pb != pr
                                       and num(pb) and num(pr))
                    sev = self._sev(imp, plano_mudou, var)
                    row_sev.append(sev)
                    if imp:
                        impacto_total += imp
                    if plano_mudou:
                        nplano += 1
                    if sev in (Severity.WARN, Severity.CRIT):
                        det = []
                        if a_base is not None and a_rec is not None:
                            det.append(f"{a_base}s -> {a_rec}s")
                        if var is not None:
                            det.append(f"{var:+.0f}%")
                        if execs:
                            det.append(f"{execs:.0f} exec")
                        if plano_mudou:
                            det.append(f"plano {pb}->{pr}")
                        sec.findings.append(Finding(
                            title=(f"SQL {sql_id}"
                                   + (f" ({schema})" if schema else "")
                                   + f": +{fmt_impacto(imp)} de DB time"),
                            detail="; ".join(det),
                            severity=sev,
                            impact=f"+{fmt_impacto(imp)}",
                            recommendation=self._rec(
                                plano_mudou,
                                sql_id in full_scan_sqls,
                                bool(stats_ruins)),
                        ))

                sec.row_severity = row_sev[:25]
                if impacto_total:
                    sec.metrics.append(Metric(
                        "DB time perdido", f"{impacto_total:.0f}", "s",
                        severity=worst(row_sev) if row_sev else Severity.INFO,
                        hint=f">={IMPACTO_WARN_S:.0f}s atencao, "
                             f">={IMPACTO_CRIT_S:.0f}s critico (por SQL)"))
                sec.metrics.append(Metric("SQLs regredidos", str(len(rows))))
                if nplano:
                    sec.metrics.append(Metric("Por troca de plano", str(nplano),
                                              severity=Severity.CRIT))
                if tem_acoes:
                    sec.note = ("Script acoes.sql gerado no caso com os comandos "
                                "DBMS_STATS por objeto e os indices candidatos. "
                                "NADA foi executado pela coleta - revise antes.")
                sections.append(sec)
                overall = worst([overall, sec.severity])
                if sec.findings:
                    summary_bits.append(
                        f"{len(sec.findings)} SQL(s) com regressao material "
                        f"({impacto_total:.0f}s de DB time)")

        if not sections:
            return [], ""
        summary = ("; ".join(summary_bits[:2]) + ". " if summary_bits else "")
        summary += ("Regressao critica de SQL: priorizar o plano de acao "
                    "(acoes.sql) e a comparacao de planos."
                    if overall == Severity.CRIT else
                    "Regressao de SQL em atencao: validar estatisticas e planos."
                    if overall == Severity.WARN else
                    "Sem regressao material de SQL de aplicacao.")
        return sections, summary
