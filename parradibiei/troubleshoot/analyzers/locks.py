"""
Analisador de Locks / Bloqueios (blocking sessions).

Entrada: saida de query (.txt/.log/.lst) em formato SQL*Plus. Reconhece tres
formatos, todos produzidos pelo catalogo MCP:

- ARVORE (agora): checks 'blocking_chain' - CONNECT BY em GV$SESSION, com
  ARVORE/NIVEL/BLOCKER. NIVEL 1 e o bloqueador RAIZ (nao espera ninguem), NIVEL
  >= 2 sao as vitimas.
- HISTORICO (ASH 7d): check 'blocking_history' - JANELA por hora e
  SEGS_BLOQUEADO acumulado. Escala diferente da arvore (soma de varias sessoes
  ao longo de uma hora), por isso usa limiares proprios.
- TOP BLOQUEADORES (ASH 7d): check 'blocking_top_blockers' - MODULO/PROGRAMA e
  SEGS_CAUSADOS. E o "quem causou", usado para acionar o time responsavel.

Tambem aceita o formato antigo de lista de pares (bloqueador x bloqueada) para
nao invalidar coletas historicas.
"""
from __future__ import annotations

import re

from ..model import Finding, Metric, Section, Severity, worst
from .base import Analyzer, find_col, num, parse_fixed_width_tables, read_text

# Limiares da espera de UMA sessao no instante da coleta (arvore).
SECS_WARN, SECS_CRIT = 60, 300
# Limiares do tempo bloqueado ACUMULADO em uma janela de 1h (ASH). Escala maior:
# sao varias sessoes somadas, cada amostra do ASH valendo ~10s.
HIST_WARN, HIST_CRIT = 600, 3600
# Bloqueador raiz INACTIVE por mais que isto = transacao aberta sem commit.
IDLE_MIN_WARN = 5
# Profundidade da arvore que caracteriza CASCATA: a partir do NIVEL 3 existe uma
# sessao que e vitima E bloqueadora ao mesmo tempo (raiz -> intermediaria ->
# vitima). Importa separar de "varias vitimas da mesma raiz": ali basta tratar a
# raiz, aqui cada elo que morre pode liberar so parte da fila, e o impacto
# cresce em avalanche - e o cenario classico de fila de conexoes estourando.
CASCATA_NIVEL = 3


class LocksAnalyzer(Analyzer):
    kind = "locks"
    title = "Locks / Bloqueios"

    def matches(self, filename: str, head: str) -> int:
        score = 0
        fn = filename.lower()
        if re.search(r"lock|block|bloque", fn):
            score += 50
        h = head.lower()
        if "blocking" in h or "enq:" in h or "row lock" in h or "bloquead" in h:
            score += 40
        if "waiting" in h and "session" in h:
            score += 20
        return min(score, 100)

    def _sev_secs(self, secs) -> str:
        if secs is None:
            return Severity.INFO
        if secs >= SECS_CRIT:
            return Severity.CRIT
        if secs >= SECS_WARN:
            return Severity.WARN
        return Severity.OK

    def _sev_hist(self, secs) -> str:
        if secs is None:
            return Severity.INFO
        if secs >= HIST_CRIT:
            return Severity.CRIT
        if secs >= HIST_WARN:
            return Severity.WARN
        return Severity.OK

    @staticmethod
    def _cell(row, idx) -> str:
        return row[idx].strip() if 0 <= idx < len(row) else ""

    # ------------------------------------------------------------------ arvore
    def _analisa_arvore(self, headers, rows, estado) -> Section:
        ci_nivel = find_col(headers, "nivel", "level")
        ci_sid = find_col(headers, "sid")
        ci_blk = find_col(headers, "blocker", "blocking")
        ci_secs = find_col(headers, "secs", "seconds", "sec")
        ci_idle = find_col(headers, "idle")
        ci_stat = find_col(headers, "status")
        ci_evt = find_col(headers, "event")
        ci_obj = find_col(headers, "objeto", "object")
        ci_user = find_col(headers, "username", "user")
        ci_sql = find_col(headers, "sql_id")

        sec = Section(title="Arvore de bloqueio (no momento da coleta)",
                      table_headers=headers, table_rows=rows)
        row_sev, vitimas, raizes = [], [], []
        for r in rows:
            nivel = num(self._cell(r, ci_nivel)) or 1
            secs = num(self._cell(r, ci_secs))
            if nivel <= 1:
                # RAIZ: nao espera por lock, o SECS dela e a espera do proprio
                # trabalho (ou o tempo ocioso). Pontuar por SECS aqui geraria
                # CRIT em qualquer sessao de sistema parada.
                idle = num(self._cell(r, ci_idle))
                inativa = self._cell(r, ci_stat).upper() == "INACTIVE"
                sev = (Severity.CRIT if inativa and (idle or 0) >= IDLE_MIN_WARN
                       else Severity.INFO)
                raizes.append((r, sev, idle, inativa))
            else:
                sev = self._sev_secs(secs)
                vitimas.append((r, sev, secs))
                if secs:
                    estado["max_secs"] = max(estado["max_secs"], secs)
            row_sev.append(sev)
        sec.row_severity = row_sev
        estado["vitimas"] += len(vitimas)

        # Duracao e o que importa (Method R): bloqueio com SECS=0 e instantaneo -
        # locks sao adquiridos e liberados o tempo todo, inclusive entre as
        # proprias sessoes paralelas da coleta. Contar como ATENCAO so pela
        # existencia da vitima gerava alerta em coleta de banco saudavel.
        relevantes = [v for v in vitimas if (v[2] or 0) >= SECS_WARN]
        sec.metrics.append(Metric(
            "Sessoes bloqueadas", str(len(vitimas)),
            severity=Severity.WARN if relevantes else Severity.OK,
            hint=(f"vitimas (NIVEL >= 2); a raiz nao esta bloqueada. "
                  f"{len(relevantes)} com espera >= {SECS_WARN}s")))
        if len(rows) > len(vitimas):
            sec.metrics.append(Metric("Bloqueadores raiz", str(len(raizes)),
                                      severity=Severity.INFO))
        if estado["max_secs"]:
            sec.metrics.append(Metric(
                "Maior espera", f"{estado['max_secs']:.0f}", "s",
                severity=self._sev_secs(estado["max_secs"]),
                hint=f">={SECS_WARN}s atencao, >={SECS_CRIT}s critico"))

        # A raiz ociosa e a causa: quem tem transacao aberta sem commit.
        for r, _sev, idle, inativa in raizes:
            sid = self._cell(r, ci_sid) or "?"
            estado["raizes"].add(sid)
            if not vitimas:
                continue
            if inativa and (idle or 0) >= IDLE_MIN_WARN:
                sec.findings.append(Finding(
                    title=f"Bloqueador raiz SID {sid} INACTIVE com transacao aberta",
                    detail=(f"Sessao raiz da cadeia esta INACTIVE ha {idle:.0f} min "
                            f"(usuario {self._cell(r, ci_user) or '-'}, "
                            f"SQL {self._cell(r, ci_sql) or '-'}) e mantem "
                            f"{len(vitimas)} sessao(oes) bloqueada(s). INACTIVE "
                            "significa que nao executa nada: o lock esta retido por "
                            "transacao sem COMMIT/ROLLBACK, nao por lentidao."),
                    severity=Severity.CRIT,
                    impact=f"{len(vitimas)} sessao(oes) bloqueada(s)",
                    recommendation=(
                        "Acionar o dono da aplicacao para encerrar a transacao. "
                        "Se indisponivel e o impacto persistir, avaliar ALTER SYSTEM "
                        "KILL SESSION com aprovacao (causa rollback do trabalho nao "
                        "commitado). Revisar no codigo o commit ausente / timeout de "
                        "transacao aberta."),
                ))

        # CASCATA: alguem e vitima e bloqueador ao mesmo tempo. Vem antes das
        # vitimas individuais de proposito - com a cadeia em cascata, tratar uma
        # vitima isolada e o conselho errado.
        fundo = max((num(self._cell(r, ci_nivel)) or 1) for r in rows) if rows else 1
        if fundo >= CASCATA_NIVEL and vitimas:
            meio = [self._cell(r, ci_sid) or "?" for r in rows
                    if CASCATA_NIVEL - 1 <= (num(self._cell(r, ci_nivel)) or 1) < fundo]
            estado["cascata"] = max(estado["cascata"], int(fundo))
            sec.findings.append(Finding(
                title=(f"Bloqueio em CASCATA: cadeia com {int(fundo)} niveis "
                       f"e {len(vitimas)} sessao(oes) na fila"),
                detail=("Ha sessao que esta bloqueada E bloqueando outras "
                        + (f"(SID {', '.join(meio[:5])})" if meio else "")
                        + ". Diferente de varias vitimas da mesma raiz: aqui a "
                        "fila e sequencial, entao liberar um elo do meio nao "
                        "resolve a cadeia inteira, e cada nova sessao que "
                        "chega entra na fila - o impacto cresce em avalanche."),
                severity=Severity.CRIT,
                impact=f"{int(fundo)} niveis, {len(vitimas)} sessao(oes)",
                recommendation=(
                    "Tratar a RAIZ (NIVEL 1) primeiro - e a unica acao que "
                    "desmonta a cadeia toda. Matar sessao do meio libera so o "
                    "trecho abaixo dela e mantem o bloqueio de origem. "
                    "Confirmar com o dono da aplicacao antes de qualquer kill "
                    "(rollback do trabalho nao commitado)."),
            ))

        for r, sev, secs in vitimas:
            if sev not in (Severity.WARN, Severity.CRIT):
                continue
            sec.findings.append(Finding(
                title=(f"Sessao {self._cell(r, ci_sid) or '?'} bloqueada por "
                       f"{self._cell(r, ci_blk) or '?'}"
                       + (f" em {self._cell(r, ci_obj)}"
                          if self._cell(r, ci_obj) not in ("", "-") else "")),
                detail=(f"Evento: {self._cell(r, ci_evt) or '-'}. "
                        f"Esperando ha {secs:.0f}s."),
                severity=sev,
                impact=f"{secs:.0f}s",
                recommendation=(
                    "Tratar o bloqueador RAIZ da cadeia (topo da arvore), nao esta "
                    "sessao: matar a vitima nao libera o lock."
                    if sev == Severity.CRIT else
                    "Monitorar; confirmar se a raiz da cadeia tem transacao longa."),
            ))
        return sec

    # --------------------------------------------------------------- historico
    def _analisa_historico(self, headers, rows, estado) -> Section:
        ci_jan = find_col(headers, "janela")
        ci_evt = find_col(headers, "event")
        ci_obj = find_col(headers, "objeto")
        ci_secs = find_col(headers, "segs_bloqueado")
        ci_vit = find_col(headers, "vitimas")

        sec = Section(title="Bloqueios no historico (ASH, 7 dias)",
                      table_headers=headers, table_rows=rows)
        row_sev, total, pico, pior = [], 0.0, 0.0, None
        # Agrega por evento+objeto: 3 achados acionaveis valem mais que 25 linhas
        # de janela por hora, que sao o mesmo fenomeno repetido.
        agreg: dict[tuple[str, str], dict] = {}
        for r in rows:
            secs = num(self._cell(r, ci_secs)) or 0.0
            sev = self._sev_hist(secs)
            row_sev.append(sev)
            total += secs
            if secs > pico:
                pico, pior = secs, r
            chave = (self._cell(r, ci_evt) or "-", self._cell(r, ci_obj) or "-")
            a = agreg.setdefault(chave, {"secs": 0.0, "janelas": 0, "vitimas": 0.0,
                                         "pior_janela": "", "pior_secs": 0.0})
            a["secs"] += secs
            a["janelas"] += 1
            a["vitimas"] = max(a["vitimas"], num(self._cell(r, ci_vit)) or 0)
            if secs > a["pior_secs"]:
                a["pior_secs"], a["pior_janela"] = secs, self._cell(r, ci_jan)
        sec.row_severity = row_sev
        estado["hist_total"] += total

        if rows:
            sec.metrics.append(Metric(
                "Tempo bloqueado (7d)",
                f"{total/60:.0f}" if total >= 60 else f"{total:.0f}",
                "min" if total >= 60 else "s", severity=Severity.INFO,
                hint="soma estimada do ASH (10s por amostra)"))
            sec.metrics.append(Metric(
                "Pico por hora", f"{pico:.0f}", "s", severity=self._sev_hist(pico),
                hint=f">={HIST_WARN}s atencao, >={HIST_CRIT}s critico na janela"))
            if pior is not None:
                sec.metrics.append(Metric("Pior janela", self._cell(pior, ci_jan),
                                          severity=Severity.INFO))

        for (evt, obj), a in sorted(agreg.items(), key=lambda kv: -kv[1]["secs"])[:3]:
            sev = self._sev_hist(a["secs"])
            if sev not in (Severity.WARN, Severity.CRIT):
                continue
            alvo = f" em {obj}" if obj not in ("", "-") else ""
            sec.findings.append(Finding(
                title=f"Contencao recorrente: {evt}{alvo}",
                detail=(f"{a['secs']/60:.0f} min bloqueados em 7 dias, distribuidos "
                        f"em {a['janelas']} janela(s) de hora; pior janela "
                        f"{a['pior_janela']} com {a['pior_secs']:.0f}s e ate "
                        f"{a['vitimas']:.0f} sessao(oes) afetada(s). Nao aparece na "
                        "arvore de bloqueio porque ja passou - lock e transitorio."),
                severity=sev,
                impact=f"{a['secs']/60:.0f} min/7d",
                recommendation=(
                    "Revisar a transacao que atualiza a mesma linha/objeto em "
                    "concorrencia (reduzir escopo e duracao, commitar mais cedo)."
                    if evt.lower().startswith("enq: tx") else
                    "Verificar DDL/recompilacao concorrente com execucao "
                    "(library cache) ou hot block/indice sequencial (buffer busy)."),
            ))
        return sec

    # ---------------------------------------------------------- top bloqueadores
    def _analisa_top(self, headers, rows, estado) -> Section:
        ci_mod = find_col(headers, "modulo")
        ci_prog = find_col(headers, "programa")
        ci_sql = find_col(headers, "sql_id")
        ci_secs = find_col(headers, "segs_causados")
        ci_vit = find_col(headers, "vitimas")

        sec = Section(title="Top bloqueadores por modulo (ASH, 7 dias)",
                      table_headers=headers, table_rows=rows,
                      note="Quem CAUSOU o bloqueio - use para acionar o responsavel.")
        # Secao de contexto: severidade INFO nas linhas para nao inflar o Health
        # Score, que ja e penalizado pela secao de historico (mesmo fenomeno).
        sec.row_severity = [Severity.INFO] * len(rows)
        if rows:
            r = rows[0]
            secs = num(self._cell(r, ci_secs)) or 0.0
            sec.metrics.append(Metric("Maior causador",
                                      self._cell(r, ci_mod) or "-",
                                      severity=Severity.INFO))
            sec.metrics.append(Metric("Tempo causado", f"{secs:.0f}", "s",
                                      severity=Severity.INFO))
            if secs >= HIST_WARN:
                estado["top_causador"] = (self._cell(r, ci_mod) or "-", secs)
                sec.findings.append(Finding(
                    title=f"Modulo {self._cell(r, ci_mod) or '-'} lidera os bloqueios",
                    detail=(f"Programa {self._cell(r, ci_prog) or '-'}, SQL "
                            f"{self._cell(r, ci_sql) or '-'}, causou {secs:.0f}s de "
                            f"espera em ate {num(self._cell(r, ci_vit)) or 0:.0f} "
                            "sessao(oes) nos ultimos 7 dias."),
                    severity=Severity.INFO,
                    impact=f"{secs:.0f}s causados",
                    recommendation=("Acionar o time dono do modulo com o SQL_ID; "
                                    "revisar duracao da transacao e ponto de commit."),
                ))
        return sec

    # ------------------------------------------------------------------ legado
    def _analisa_pares(self, headers, rows, estado) -> Section | None:
        ci_block = find_col(headers, "blocking", "blocker", "holder")
        ci_wait = find_col(headers, "waiting", "waiter", "blocked", "sid")
        ci_secs = find_col(headers, "sec", "wait_time", "ctime", "min")
        ci_evt = find_col(headers, "event", "lock", "type")
        ci_obj = find_col(headers, "object", "obj", "segment")
        if ci_block < 0 and ci_wait < 0:
            return None

        sec = Section(title="Cadeia de bloqueio", table_headers=headers,
                      table_rows=rows)
        row_sev, maior = [], 0.0
        for r in rows:
            secs = num(self._cell(r, ci_secs))
            row_sev.append(self._sev_secs(secs))
            if secs:
                maior = max(maior, secs)
            if self._cell(r, ci_block):
                estado["raizes"].add(self._cell(r, ci_block))
        sec.row_severity = row_sev
        estado["vitimas"] += len(rows)
        estado["max_secs"] = max(estado["max_secs"], maior)

        sec.metrics.append(Metric("Sessoes bloqueadas", str(len(rows)),
                                  severity=Severity.WARN if rows else Severity.OK))
        if maior:
            sec.metrics.append(Metric("Maior espera", f"{maior:.0f}", "s",
                                      severity=self._sev_secs(maior),
                                      hint=f">={SECS_WARN}s atencao, >={SECS_CRIT}s critico"))
        for r in rows:
            secs = num(self._cell(r, ci_secs))
            sev = self._sev_secs(secs)
            if sev not in (Severity.WARN, Severity.CRIT):
                continue
            sec.findings.append(Finding(
                title=(f"Sessao {self._cell(r, ci_block) or '?'} bloqueia "
                       f"{self._cell(r, ci_wait) or '?'}"
                       + (f" em {self._cell(r, ci_obj)}"
                          if self._cell(r, ci_obj) else "")),
                detail=(f"Evento: {self._cell(r, ci_evt)}. Espera: {secs:.0f}s."
                        if secs else f"Evento: {self._cell(r, ci_evt)}"),
                severity=sev,
                impact=f"{secs:.0f}s" if secs else "",
                recommendation=(
                    "Avaliar/encerrar a sessao bloqueadora (ALTER SYSTEM KILL SESSION) "
                    "apos confirmar com a aplicacao; investigar transacao aberta sem commit."
                    if sev == Severity.CRIT else
                    "Monitorar; verificar transacao longa sem commit na sessao bloqueadora."),
            ))
        return sec

    # ----------------------------------------------------------------- roteador
    def analyze(self, files, meta):
        sections = []
        estado = {"vitimas": 0, "max_secs": 0.0, "hist_total": 0.0,
                  "raizes": set(), "top_causador": None, "cascata": 0}

        for path in files:
            for headers, rows in parse_fixed_width_tables(read_text(path)):
                if not headers or not rows:
                    continue
                arvore = find_col(headers, "arvore") >= 0 or (
                    find_col(headers, "nivel") >= 0 and find_col(headers, "blocker") >= 0)
                if arvore:
                    sections.append(self._analisa_arvore(headers, rows, estado))
                elif find_col(headers, "segs_bloqueado") >= 0:
                    sections.append(self._analisa_historico(headers, rows, estado))
                elif find_col(headers, "segs_causados") >= 0:
                    sections.append(self._analisa_top(headers, rows, estado))
                else:
                    sec = self._analisa_pares(headers, rows, estado)
                    if sec is not None:
                        sections.append(sec)

        if not sections:
            return [], ""

        overall = worst([s.severity for s in sections])
        partes = []
        if estado["vitimas"] and estado["max_secs"] < SECS_WARN:
            partes.append(
                f"{estado['vitimas']} sessao(oes) bloqueada(s) no momento da coleta, "
                f"porem de forma transitoria (maior espera {estado['max_secs']:.0f}s, "
                f"abaixo de {SECS_WARN}s) - sem impacto material")
        elif estado["vitimas"]:
            partes.append(
                f"{estado['vitimas']} sessao(oes) bloqueada(s) no momento da coleta, "
                f"maior espera de {estado['max_secs']:.0f}s"
                + (f"; bloqueador(es) raiz: {', '.join(sorted(estado['raizes']))}"
                   if estado["raizes"] else ""))
        else:
            partes.append("Nenhuma sessao bloqueada no momento da coleta")
        if estado["cascata"]:
            partes.append(f"a cadeia esta em CASCATA ({estado['cascata']} niveis): "
                          "tratar a raiz, nao as vitimas")
        if estado["hist_total"]:
            # abaixo de 1 min, "0 min" parece dado faltando: mostrar em segundos
            tot = (f"{estado['hist_total']/60:.0f} min" if estado["hist_total"] >= 60
                   else f"{estado['hist_total']:.0f}s")
            partes.append(f"{tot} de bloqueio acumulados nos ultimos 7 dias (ASH)")
        if estado["top_causador"]:
            partes.append(f"maior causador: modulo {estado['top_causador'][0]}")
        fecho = ("Ha bloqueio critico: recomenda-se acao imediata."
                 if overall == Severity.CRIT else
                 "Bloqueios sob observacao." if overall == Severity.WARN else
                 "Sem bloqueios relevantes.")
        return sections, ". ".join(partes) + ". " + fecho
