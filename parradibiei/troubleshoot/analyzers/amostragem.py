"""
Analisador da AMOSTRAGEM DE SESSOES (perfil de esperas sem Diagnostics Pack).

Le `amostragem.txt`, produzido pelo coletor amostrando `gv$session` em alta
frequencia. Sem o Diagnostics Pack o banco nao tem ASH e o framework ficava sem
a informacao mais importante do Method R: **onde o tempo esta indo**.

A tecnica e a mesma do Snapper (Tanel Poder) e do YaASHs: ASH e, no fundo, um
historico de v$session - amostrar v$session da o mesmo perfil, sem licenca.

Limite honesto, que o relatorio declara: a amostra cobre a JANELA DA COLETA
(segundos), nao as ultimas horas. Serve para o problema que esta acontecendo
agora; nao substitui AWR/ASH para analise retroativa.
"""
from __future__ import annotations

import math
import os

from ..model import Finding, Metric, Section, Severity
from .base import Analyzer, find_col, num, parse_fixed_width_tables, read_text
from .saude import limiar

# Amostras abaixo disto tornam o perfil indicativo, nao conclusivo. Com 30
# amostras (a janela padrao de 15s a cada 0,5s) cada amostra vale 3,3 pontos
# percentuais: um evento visto uma vez aparece como "3%" e nao se distingue de
# ruido. O relatorio mostrava esse 3% com a mesma cara de certeza de um ASH de
# 7 dias.
AMOSTRAS_CONFIAVEIS = 60


def margem_pp(pct: float, n: int) -> float:
    """Margem de erro de uma proporcao, em pontos percentuais (95%).

    E um PISO da incerteza, nao a incerteza real, e a diferenca importa:
    amostras consecutivas separadas por 0,5s NAO sao independentes - uma espera
    que dura 3 segundos aparece em seis amostras seguidas. A formula binomial
    assume independencia, entao a incerteza verdadeira e MAIOR que esta. Serve
    para dizer "no minimo isto", nunca "exatamente isto".
    """
    if not n or n <= 0:
        return 100.0
    p = max(0.0, min(1.0, float(pct) / 100.0))
    return 1.96 * math.sqrt(p * (1.0 - p) / n) * 100.0


class AmostragemAnalyzer(Analyzer):
    kind = "amostragem"
    title = "Perfil de esperas (amostragem)"

    def matches(self, filename: str, head: str) -> int:
        fn = os.path.basename(filename or "").lower()
        base = fn[:-4] if fn.endswith(".txt") else fn
        h = (head or "").upper()
        if base == "amostragem" or "AMOSTRAGEM DE SESSOES" in h:
            return 95
        return 0

    def _resumo(self, headers, rows) -> tuple:
        if not rows:
            return None
        r = rows[0]

        def g(*alias):
            i = find_col(headers, *alias)
            return num(r[i]) if 0 <= i < len(r) else None

        n = g("amostras") or 0
        # `resolucao_pp` passou a ser gravada pelo coletor. Evidencia coletada
        # antes disso nao tem a coluna, e ai derivamos de 100/N - melhor
        # calcular que perder a leitura de precisao no historico ja coletado.
        res = g("resolucao_pp")
        if res is None and n:
            res = 100.0 / n
        return (n, g("segundos") or 0,
                g("aas_medio") or 0.0, g("aas_max") or 0,
                g("amostras_bloqueadas") or 0, res or 0.0)

    def analyze(self, files, meta):
        sections = []
        for path in files:
            text = read_text(path)
            tabelas = parse_fixed_width_tables(text)
            if not tabelas:
                continue

            dados = self._resumo(*tabelas[0])
            if not dados:
                continue
            n_amostras, segundos, aas, aas_max, bloqueadas, resol = dados
            magra = n_amostras and n_amostras < AMOSTRAS_CONFIAVEIS

            sec = Section(title="Perfil de esperas (amostragem de sessoes)")
            sec.note = (
                f"Perfil montado com {n_amostras:.0f} amostras de gv$session em "
                f"~{segundos:.0f}s. Tecnica usada por nao haver Diagnostics "
                "Pack: cobre a JANELA DA COLETA, nao as ultimas horas. "
                f"Cada amostra vale {resol:.1f} ponto(s) percentual(is) do "
                "perfil, entao evento com uma unica amostra nao se distingue "
                "de ruido.")
            if magra:
                sec.note += (
                    f" Amostra MAGRA ({n_amostras:.0f} < {AMOSTRAS_CONFIAVEIS}): "
                    "leia como indicacao de direcao, nao como medida. Para "
                    "medir, repita com --amostragem 120.")
            sec.metrics.append(Metric("Sessoes ativas (media)", f"{aas:.2f}"))
            sec.metrics.append(Metric("Pico de sessoes ativas", f"{aas_max:.0f}"))
            sec.metrics.append(Metric(
                "Amostras", f"{n_amostras:.0f}",
                severity=Severity.WARN if magra else Severity.OK,
                hint=f"resolucao de {resol:.1f}pp por amostra"
                     + ("; janela curta para medir proporcao" if magra else "")))

            # ── perfil de esperas ──
            if len(tabelas) > 1:
                h, rows = tabelas[1]
                sec.table_headers, sec.table_rows = h, rows[:20]
                ci_wc = find_col(h, "wait_class")
                ci_ev = find_col(h, "event")
                ci_pct = find_col(h, "pct")
                por_classe: dict = {}
                pior_ev, pior_pct = "", 0.0
                for r in rows:
                    wc = r[ci_wc].strip() if 0 <= ci_wc < len(r) else "?"
                    pct = (num(r[ci_pct]) or 0.0) if 0 <= ci_pct < len(r) else 0.0
                    por_classe[wc] = por_classe.get(wc, 0.0) + pct
                    if pct > pior_pct:
                        pior_pct = pct
                        pior_ev = r[ci_ev].strip() if 0 <= ci_ev < len(r) else "?"

                cpu = por_classe.get("CPU", 0.0)
                espera = max(0.0, 100.0 - cpu)
                t = limiar("amostragem.txt", "espera_pct")
                warn, crit = t.get("warn", 50.0), t.get("crit", 75.0)
                sev = (Severity.CRIT if espera >= crit else
                       Severity.WARN if espera >= warn else Severity.OK)
                # ── a incerteza atravessa o limiar? ───────────
                # 78% de espera medidos em 30 amostras tem margem de +-15pp: o
                # intervalo vai de 63% a 93% e atravessa o limiar critico de
                # 75%. Afirmar CRITICO ali e afirmar sobre o lado do intervalo
                # que convem. Rebaixar para ATENCAO nao perde o sinal - a
                # espera segue reportada - e nao gasta a palavra "critico" num
                # numero que a proxima coleta pode desmentir.
                margem = margem_pp(espera, int(n_amostras))
                incerto = sev == Severity.CRIT and (espera - margem) < crit
                if incerto:
                    sev = Severity.WARN
                sec.metrics.append(Metric(
                    "Tempo em espera", f"{espera:.0f}%", severity=sev,
                    hint=(f"+-{margem:.0f}pp em {n_amostras:.0f} amostras "
                          "(piso: amostras vizinhas nao sao independentes)"
                          + (f"; intervalo cruza o limiar critico de {crit:.0f}%"
                             if incerto else ""))))
                sec.metrics.append(Metric("Tempo em CPU", f"{cpu:.0f}%"))

                if sev in (Severity.WARN, Severity.CRIT) and pior_ev:
                    classe_top = max(por_classe.items(),
                                     key=lambda x: (x[1] if x[0] != "CPU" else -1))
                    detalhe = (f"Classe dominante: {classe_top[0]} "
                               f"({classe_top[1]:.0f}%). Media de {aas:.2f} "
                               f"sessao(oes) ativa(s), pico de {aas_max:.0f}. "
                               f"Medido em {n_amostras:.0f} amostras "
                               f"(+-{margem:.0f}pp).")
                    if incerto:
                        detalhe += (
                            f" Rebaixado de critico para atencao: o intervalo "
                            f"de confianca cruza o limiar de {crit:.0f}%, "
                            "entao a amostra nao sustenta a afirmacao mais "
                            "forte.")
                    sec.findings.append(Finding(
                        title=(f"{espera:.0f}% da atividade em espera; "
                               f"evento dominante: {pior_ev}"),
                        detail=detalhe,
                        severity=sev, impact=f"{pior_pct:.0f}% das amostras",
                        recommendation=(
                            "Trate a classe dominante antes do SQL individual "
                            "(Method R: o tempo esta nela). Sem Diagnostics "
                            "Pack nao ha historico: se o sintoma for "
                            "intermitente, repita a coleta durante a janela de "
                            "problema para confirmar o perfil"
                            + (f" - e com janela maior (--amostragem 120), "
                               f"porque {n_amostras:.0f} amostras nao medem "
                               "proporcao com precisao." if magra else ".")),
                    ))

            # ── bloqueio dentro da janela ──
            # O coletor ja contava as amostras com BLOCKING_SESSION preenchido,
            # mas o analisador jogava o numero fora. Sem pack esta e a UNICA
            # pista de bloqueio ao longo do tempo: o locks.txt e uma foto de um
            # instante e nao ve o enfileiramento que aparece e some.
            #
            # Entra DEPOIS do perfil de esperas de proposito: a classe de espera
            # dominante e o primeiro achado da secao (Method R), e bloqueio
            # costuma ser consequencia dela, nao a causa.
            if n_amostras:
                pct_blq = 100.0 * bloqueadas / n_amostras
                t = limiar("amostragem.txt", "amostras_bloqueadas_pct")
                warn, crit = t.get("warn", 20.0), t.get("crit", 50.0)
                sev_blq = (Severity.CRIT if pct_blq >= crit else
                           Severity.WARN if pct_blq >= warn else Severity.OK)
                sec.metrics.append(Metric(
                    "Amostras com bloqueio", f"{pct_blq:.0f}%",
                    severity=sev_blq,
                    hint=f"{bloqueadas:.0f} de {n_amostras:.0f} com sessao "
                         f"bloqueadora"))
                if sev_blq in (Severity.WARN, Severity.CRIT):
                    sec.findings.append(Finding(
                        title=(f"{pct_blq:.0f}% das amostras com sessao "
                               "bloqueada"),
                        detail=(f"{bloqueadas:.0f} de {n_amostras:.0f} amostras "
                                "tinham BLOCKING_SESSION preenchido durante a "
                                "janela de coleta."),
                        severity=sev_blq,
                        impact="sessao esperando por outra sessao, nao por "
                               "recurso do banco",
                        recommendation=(
                            "Veja locks.txt para identificar o bloqueador raiz. "
                            "Bloqueio intermitente nao aparece numa foto unica: "
                            "se o locks.txt vier limpo, repita a coleta durante "
                            "a janela do sintoma."),
                    ))
            sections.append(sec)

            # ── top SQL ──
            if len(tabelas) > 2:
                h, rows = tabelas[2]
                s2 = Section(title="Top SQL por amostras (sem pack)",
                             table_headers=h, table_rows=rows[:15])
                if rows:
                    ci_sql = find_col(h, "sql_id")
                    ci_pct = find_col(h, "pct")
                    ci_user = find_col(h, "username")
                    r = rows[0]
                    sql_id = r[ci_sql].strip() if 0 <= ci_sql < len(r) else "?"
                    pct = (num(r[ci_pct]) or 0.0) if 0 <= ci_pct < len(r) else 0.0
                    user = r[ci_user].strip() if 0 <= ci_user < len(r) else "?"
                    t = limiar("amostragem.txt", "sql_dominante_pct")
                    warn = t.get("warn", 30.0)
                    if pct >= warn:
                        s2.findings.append(Finding(
                            title=f"SQL {sql_id} concentra {pct:.0f}% das amostras",
                            detail=f"Executado por {user}.",
                            severity=Severity.WARN,
                            impact=f"{pct:.0f}% da atividade",
                            recommendation=(
                                f"Detalhe o plano: DETALHAR_SQL.py <caso> "
                                f"{sql_id}. Sem Tuning Pack, use "
                                "DBMS_XPLAN.DISPLAY_CURSOR e as estatisticas "
                                "de v$sql em vez do SQL Monitor."),
                        ))
                sections.append(s2)

        if not sections:
            return [], ""
        return sections, ("Perfil de esperas obtido por amostragem de "
                          "gv$session (sem Diagnostics Pack).")
