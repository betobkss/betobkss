"""
Analisador de CONTENCAO: parse/shared pool, redo e deadlock.

Tres familias de incidente que o framework sabia nomear DEPOIS do estrago e
nao antes:

- `parse.txt`        hard parse x total e memoria livre do shared pool
- `literais.txt`     o mesmo SQL repetido so trocando o literal (sem bind)
- `redo_waits.txt`   log file sync e companhia - o "commit lento" em numero
- `deadlocks.txt`    ORA-00060 registrado no alert log
- `archive_rate.txt` volume de archive por hora (dimensiona FRA e standby)

O fio comum: nenhuma delas aparece no perfil de esperas com nome proprio para
quem le rapido. "log file sync" vira "o banco esta lento no commit"; hard parse
vira "CPU alta"; deadlock ja resolvido pelo Oracle nao deixa sessao bloqueada
para o analisador de locks encontrar - so uma linha no alert log e um erro
estranho na aplicacao.

Duas armadilhas deliberadamente evitadas aqui, porque as duas geram falso
positivo com cara de diagnostico:

1. **Shared pool com pouca memoria livre NAO e alarme.** E o estado normal de
   um banco em uso: o Oracle preenche o pool e reusa. Livre baixo so vira
   achado quando acompanhado de hard parse alto - ai sim e fragmentacao por
   literal, o caminho do ORA-04031. Sozinho, entra como contexto.
2. **Parse acumulado desde o startup nao e a foto de agora.** `gv$sysstat` conta
   desde que a instancia subiu; uma tempestade de parse de tres semanas atras
   ainda pesa na conta. Por isso o texto do achado diz "desde o startup" em vez
   de fingir que e o incidente de hoje.
"""
from __future__ import annotations

import os

from ..model import Finding, Metric, Section, Severity, worst
from .base import Analyzer, find_col, num, parse_fixed_width_tables, read_text
from .saude import limiar

_ALVOS = {
    "parse": "PARSE (ACUMULADO DESDE O STARTUP)",
    "literais": "SQL SEM BIND",
    "redo_waits": "ESPERAS DE REDO",
    "deadlocks": "DEADLOCKS",
    "archive_rate": "ARCHIVE POR HORA",
}


def _mediana(valores: list) -> float:
    vals = sorted(v for v in valores if v is not None)
    if not vals:
        return 0.0
    meio = len(vals) // 2
    if len(vals) % 2:
        return float(vals[meio])
    return (vals[meio - 1] + vals[meio]) / 2.0


class ContencaoAnalyzer(Analyzer):
    kind = "contencao"
    title = "Parse, redo e deadlock"

    def matches(self, filename: str, head: str) -> int:
        fn = os.path.basename(filename or "").lower()
        base = fn[:-4] if fn.endswith(".txt") else fn
        h = (head or "").upper()
        for chave, marcador in _ALVOS.items():
            if base == chave or marcador in h:
                return 95
        return 0

    # ── Parse e shared pool ───────────────────────────────────
    def _parse(self, tabelas) -> Section:
        """`parse.txt` traz DUAS tabelas: as estatisticas (nome/valor) e o
        shared pool. Sao lidas juntas de proposito - o veredito de uma depende
        da outra."""
        t = limiar("parse.txt", "hard_parse_pct")
        warn, crit = t.get("warn", 10.0), t.get("crit", 25.0)
        piso_livre = limiar("parse.txt", "livre_pct").get("warn", 1.0)

        stats: dict = {}       # inst -> {estatistica: valor}
        pool: dict = {}        # inst -> livre_pct
        linhas_tabela = []
        cabecalho = []
        for headers, rows in tabelas:
            ci_est = find_col(headers, "estatistica", "stat_name", "name")
            ci_livre = find_col(headers, "livre_pct")
            ci_inst = find_col(headers, "inst_id", "inst")
            if ci_est >= 0:
                ci_val = find_col(headers, "valor", "value")
                if not cabecalho:
                    cabecalho, linhas_tabela = headers, list(rows)
                for r in rows:
                    inst = r[ci_inst].strip() if 0 <= ci_inst < len(r) else "1"
                    nome = (r[ci_est].strip().lower()
                            if 0 <= ci_est < len(r) else "")
                    val = num(r[ci_val]) if 0 <= ci_val < len(r) else None
                    if nome and val is not None:
                        stats.setdefault(inst, {})[nome] = val
            elif ci_livre >= 0:
                for r in rows:
                    inst = r[ci_inst].strip() if 0 <= ci_inst < len(r) else "1"
                    v = num(r[ci_livre]) if 0 <= ci_livre < len(r) else None
                    if v is not None:
                        pool[inst] = v

        sec = Section(title="Parse e shared pool",
                      table_headers=cabecalho, table_rows=linhas_tabela[:20])
        for inst, d in sorted(stats.items()):
            total = d.get("parse count (total)") or 0.0
            hard = d.get("parse count (hard)") or 0.0
            if not total:
                continue
            pct = hard / total * 100.0
            sufixo = f" (inst {inst})" if len(stats) > 1 else ""
            sev = (Severity.CRIT if pct >= crit else
                   Severity.WARN if pct >= warn else Severity.OK)
            sec.metrics.append(Metric(f"Hard parse{sufixo}", f"{pct:.1f}", "%",
                                      severity=sev))
            livre = pool.get(inst)
            if livre is not None:
                # Contexto, nunca alarme sozinho: pool cheio e banco em uso.
                sec.metrics.append(Metric(f"Shared pool livre{sufixo}",
                                          f"{livre:.1f}", "%"))
            if sev == Severity.OK:
                continue
            juntos = (livre is not None and livre <= piso_livre)
            detalhe = (
                f"{hard:.0f} hard parses em {total:.0f} parses, acumulado "
                "DESDE O STARTUP da instancia - nao e a foto do incidente de "
                "hoje, e a media do periodo inteiro.")
            if juntos:
                detalhe += (f" O shared pool esta com {livre:.1f}% livre: os "
                            "dois juntos sao a assinatura de fragmentacao por "
                            "literal, o caminho conhecido do ORA-04031.")
            sec.findings.append(Finding(
                title=f"Hard parse em {pct:.1f}% dos parses{sufixo}",
                detail=detalhe, severity=sev, impact=f"{pct:.1f}% hard parse",
                recommendation=(
                    "Confirme a origem em `literais.txt` (SQL agrupado por "
                    "assinatura) antes de mexer em memoria: aumentar o "
                    "SHARED_POOL_SIZE acomoda o desperdicio, nao o corrige. A "
                    "correcao e bind variable na aplicacao."),
            ))
        return sec

    # ── SQL sem bind ──────────────────────────────────────────
    def _literais(self, headers, rows) -> Section:
        t = limiar("literais.txt", "versoes")
        warn, crit = t.get("warn", 50.0), t.get("crit", 200.0)
        ci_ver = find_col(headers, "versoes")
        ci_mb = find_col(headers, "mb_shared_pool", "mb")
        ci_ex = find_col(headers, "exemplo", "sql_text")

        sec = Section(title="SQL repetido por literal (sem bind)",
                      table_headers=headers, table_rows=rows[:15])
        piores = []
        mb_total = 0.0
        for r in rows:
            v = num(r[ci_ver]) if 0 <= ci_ver < len(r) else 0.0
            mb = num(r[ci_mb]) if 0 <= ci_mb < len(r) else 0.0
            ex = r[ci_ex].strip() if 0 <= ci_ex < len(r) else ""
            mb_total += mb or 0.0
            piores.append((v or 0.0, mb or 0.0, ex))
        if not piores:
            return sec
        piores.sort(key=lambda x: -x[0])
        pior = piores[0]
        sev = (Severity.CRIT if pior[0] >= crit else
               Severity.WARN if pior[0] >= warn else Severity.OK)
        sec.metrics.append(Metric("Familias sem bind", str(len(piores)),
                                  severity=sev))
        sec.metrics.append(Metric("Pior familia", f"{pior[0]:.0f}", "versoes",
                                  severity=sev))
        if mb_total:
            sec.metrics.append(Metric("Shared pool ocupado", f"{mb_total:.1f}",
                                      "MB"))
        if sev != Severity.OK:
            sec.findings.append(Finding(
                title=(f"SQL sem bind: {pior[0]:.0f} versoes do mesmo comando"),
                detail=(f"Exemplo: {pior[2]}. " if pior[2] else "")
                + (f"As {len(piores)} familias ocupam {mb_total:.1f} MB do "
                   "shared pool. Cada literal diferente vira um cursor novo: "
                   "hard parse, CPU e memoria gastos para executar o mesmo "
                   "comando."),
                severity=sev, impact=f"{pior[0]:.0f} versoes",
                recommendation=(
                    "A correcao definitiva e bind variable na aplicacao - o "
                    "SQL_ID do exemplo identifica o modulo. CURSOR_SHARING="
                    "FORCE resolve rapido, mas muda plano de execucao: use "
                    "como paliativo declarado e com janela para observar "
                    "regressao, nunca como solucao final."),
            ))
        return sec

    # ── Redo ──────────────────────────────────────────────────
    def _redo(self, headers, rows) -> Section:
        t = limiar("redo_waits.txt", "log_file_sync_ms")
        warn, crit = t.get("warn", 10.0), t.get("crit", 20.0)
        ci_evt = find_col(headers, "event", "evento")
        ci_ms = find_col(headers, "ms_medio", "ms")
        ci_w = find_col(headers, "total_waits", "waits")

        sec = Section(title="Esperas de redo", table_headers=headers,
                      table_rows=rows[:12])
        eventos: dict = {}
        for r in rows:
            ev = (r[ci_evt].strip().lower() if 0 <= ci_evt < len(r) else "")
            ms = num(r[ci_ms]) if 0 <= ci_ms < len(r) else None
            waits = num(r[ci_w]) if 0 <= ci_w < len(r) else 0.0
            if not ev:
                continue
            # Em RAC a mesma espera vem por instancia: fica o pior caso, que e
            # o que o usuario sente.
            atual = eventos.get(ev)
            if atual is None or (ms or 0) > (atual[0] or 0):
                eventos[ev] = (ms, waits or 0.0)

        sync = eventos.get("log file sync")
        pwrite = eventos.get("log file parallel write")
        if sync and sync[0] is not None:
            ms = sync[0]
            sev = (Severity.CRIT if ms >= crit else
                   Severity.WARN if ms >= warn else Severity.OK)
            sec.metrics.append(Metric("log file sync", f"{ms:.2f}", "ms",
                                      severity=sev))
            if pwrite and pwrite[0] is not None:
                sec.metrics.append(Metric("log file parallel write",
                                          f"{pwrite[0]:.2f}", "ms"))
            if sev != Severity.OK:
                # A pergunta que separa disco de aplicacao: o LGWR escreve
                # rapido e mesmo assim a sessao espera? Entao sao commits
                # demais, nao storage lento.
                if pwrite and pwrite[0] is not None and pwrite[0] < ms / 2:
                    rec = (f"O LGWR grava em {pwrite[0]:.2f} ms mas a sessao "
                           f"espera {ms:.2f} ms: o disco de redo NAO e o "
                           "gargalo. Procure commit por linha em laco na "
                           "aplicacao (o classico 'COMMIT dentro do FOR') ou "
                           "CPU insuficiente para o LGWR.")
                else:
                    rec = ("A espera acompanha o tempo de gravacao: trate o "
                           "storage do redo (latencia por escrita), separe os "
                           "redo logs de disco disputado e evite RAID5 para "
                           "redo. Confirme com o check de I/O por arquivo.")
                sec.findings.append(Finding(
                    title=f"Commit lento: log file sync medio de {ms:.2f} ms",
                    detail=("Toda transacao que faz COMMIT espera este tempo. "
                            "E o numero que a aplicacao sente como 'o banco "
                            "esta lento' mesmo com o SQL bem escrito."),
                    severity=sev, impact=f"{ms:.2f} ms por commit",
                    recommendation=rec))

        ckpt = eventos.get("log file switch (checkpoint incomplete)")
        if ckpt and (ckpt[1] or 0) > 0:
            sec.findings.append(Finding(
                title=("Redo pequeno para a carga: "
                       f"{ckpt[1]:.0f} espera(s) por checkpoint incompleto"),
                detail=("'log file switch (checkpoint incomplete)' significa "
                        "que o banco deu a volta em todos os grupos de redo "
                        "antes de terminar o checkpoint do primeiro: TODA a "
                        "atividade de escrita para ate liberar."),
                severity=Severity.CRIT, impact=f"{ckpt[1]:.0f} paradas",
                recommendation=(
                    "Aumente o TAMANHO dos grupos de redo (nao a quantidade) "
                    "para chegar a ~4-6 trocas por hora no pico. Confirme a "
                    "frequencia atual em `redo.txt` antes de dimensionar."),
            ))
        arch = eventos.get("log file switch (archiving needed)")
        if arch and (arch[1] or 0) > 0:
            sec.findings.append(Finding(
                title=("Troca de redo parada esperando archive "
                       f"({arch[1]:.0f}x)"),
                detail=("O destino de archive nao consumiu no ritmo da "
                        "geracao. Este e o estado imediatamente anterior ao "
                        "banco travar com ORA-00257."),
                severity=Severity.CRIT, impact="risco de parada total",
                recommendation=(
                    "Verifique espaco do destino/FRA e se o backup de archive "
                    "esta rodando. Trate como incidente: com o destino cheio, "
                    "o banco para de aceitar transacao."),
            ))
        return sec

    # ── Deadlocks ─────────────────────────────────────────────
    def _deadlocks(self, headers, rows) -> Section:
        t = limiar("deadlocks.txt", "ocorrencias")
        warn, crit = t.get("warn", 1.0), t.get("crit", 5.0)
        sec = Section(title="Deadlocks (ORA-00060)",
                      table_headers=headers, table_rows=rows[:20])
        n = float(len(rows))
        sev = (Severity.CRIT if n >= crit else
               Severity.WARN if n >= warn else Severity.OK)
        sec.metrics.append(Metric("Deadlocks em 7 dias", f"{n:.0f}",
                                  severity=sev))
        if sev == Severity.OK:
            return sec
        ci_q = find_col(headers, "quando")
        quando = [r[ci_q].strip() for r in rows[:3]
                  if 0 <= ci_q < len(r) and r[ci_q].strip()]
        sec.findings.append(Finding(
            title=f"{n:.0f} deadlock(s) registrado(s) em 7 dias",
            detail=("Ultimos: " + "; ".join(quando) + ". " if quando else "")
            + ("O Oracle resolve o deadlock matando uma das transacoes, entao "
               "no banco nao sobra sessao bloqueada para ver depois - so o "
               "erro que a aplicacao recebeu."),
            severity=sev, impact=f"{n:.0f} transacao(oes) abortada(s)",
            recommendation=(
                "Deadlock e ordem de acesso na aplicacao, nao defeito do "
                "banco: dois caminhos travam os mesmos registros em ordens "
                "opostas. O trace citado no alert log traz as duas sessoes e "
                "o SQL de cada lado - e ele que fecha o caso. Padronizar a "
                "ordem de UPDATE resolve de vez."),
        ))
        return sec

    # ── Archive por hora ──────────────────────────────────────
    def _archive(self, headers, rows) -> Section:
        fator = limiar("archive_rate.txt", "pico_sobre_mediana").get("warn", 3.0)
        ci_mb = find_col(headers, "mb")
        ci_hora = find_col(headers, "hora")
        sec = Section(title="Archive gerado por hora (24h)",
                      table_headers=headers, table_rows=rows[:26])
        pares = []
        for r in rows:
            mb = num(r[ci_mb]) if 0 <= ci_mb < len(r) else None
            hora = r[ci_hora].strip() if 0 <= ci_hora < len(r) else "?"
            if mb is not None:
                pares.append((hora, mb))
        if not pares:
            return sec
        valores = [v for _, v in pares]
        med = _mediana(valores)
        hora_pico, pico = max(pares, key=lambda x: x[1])
        sec.metrics.append(Metric("Pico por hora", f"{pico:.0f}", "MB"))
        sec.metrics.append(Metric("Mediana por hora", f"{med:.0f}", "MB"))
        sec.metrics.append(Metric("Total em 24h", f"{sum(valores):.0f}", "MB"))
        # Media nao serve para dimensionar destino: e no pico que a FRA enche.
        if med > 0 and pico >= med * fator:
            sec.findings.append(Finding(
                title=(f"Rajada de archive: {pico:.0f} MB em uma hora contra "
                       f"{med:.0f} MB de mediana"),
                detail=(f"Concentrada em {hora_pico}. Dimensionar FRA, janela "
                        "de backup e banda do standby pela MEDIA subestima "
                        "justamente a hora que enche o destino."),
                severity=Severity.WARN,
                impact=f"{pico / med:.1f}x a mediana",
                recommendation=(
                    "Descubra o que roda nessa hora (carga em lote, reorg, "
                    "rebuild de indice) - se for previsivel, o dimensionamento "
                    "e do pico. Confirme se a FRA suporta o pico somado ao "
                    "backup, e no Data Guard verifique se o transporte "
                    "acompanha sem acumular lag."),
            ))
        return sec

    def analyze(self, files, meta):
        sections = []
        for path in files:
            fn = os.path.basename(path).lower()
            base = fn[:-4] if fn.endswith(".txt") else fn
            text = read_text(path)
            tabelas = parse_fixed_width_tables(text)
            headers, rows = (tabelas[0] if tabelas else ([], []))
            up = text.upper()
            if base == "parse" or "PARSE (ACUMULADO" in up:
                sections.append(self._parse(tabelas))
            elif base == "literais" or "SQL SEM BIND" in up:
                sections.append(self._literais(headers, rows))
            elif base == "redo_waits" or "ESPERAS DE REDO" in up:
                sections.append(self._redo(headers, rows))
            elif base == "deadlocks" or "DEADLOCKS" in up:
                sections.append(self._deadlocks(headers, rows))
            elif base == "archive_rate" or "ARCHIVE POR HORA" in up:
                sections.append(self._archive(headers, rows))

        if not sections:
            return [], ""
        overall = worst([s.severity for s in sections])
        if overall == Severity.CRIT:
            resumo = ("Contencao em curso (parse, redo ou deadlock): trata-se "
                      "de causa, nao de sintoma - atacar antes de tunar SQL "
                      "individual.")
        elif overall == Severity.WARN:
            resumo = ("Sinal de contencao de parse/redo: acionavel, com "
                      "correcao na aplicacao ou no dimensionamento.")
        else:
            resumo = ("Parse, redo e concorrencia sem sinal: commit, shared "
                      "pool e archive dentro do esperado.")
        return sections, resumo
