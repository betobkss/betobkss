"""
Analisador de OPERACAO e INFRAESTRUTURA.

Falhas que derrubam banco sem nunca aparecer num relatorio de performance:

- `accounts.txt`     contas expirando (ORA-28001 e 100% previsivel)
- `jobs.txt`         jobs do scheduler falhando em silencio
- `undo_errors.txt`  ORA-01555 e falta de espaco em undo
- `osstat.txt`       recursos do host (USE): CPU e memoria

O host entra aqui porque diagnosticar so o banco por dentro leva a tunar SQL
quando o problema e a maquina sem CPU.
"""
from __future__ import annotations

import os

from ..model import Finding, Metric, Section, Severity, worst
from .base import Analyzer, find_col, num, parse_fixed_width_tables, read_text
from .saude import limiar

_ALVOS = {
    "accounts": "CONTAS EXPIRANDO",
    "jobs": "JOBS COM FALHA",
    "undo_errors": "UNDO: ORA-01555",
    "osstat": "RECURSOS DO HOST",
}

# Contas internas do Oracle: expiram/bloqueiam por padrao na instalacao e
# acusa-las como incidente e ruido. O que importa e conta de APLICACAO.
_INTERNAS = {
    "SYS", "SYSTEM", "OUTLN", "DBSNMP", "APPQOSSYS", "ANONYMOUS", "XDB",
    "CTXSYS", "MDSYS", "ORDSYS", "ORDDATA", "ORDPLUGINS", "SI_INFORMTN_SCHEMA",
    "OLAPSYS", "WMSYS", "EXFSYS", "SPATIAL_CSW_ADMIN_USR", "LBACSYS",
    "SPATIAL_WFS_ADMIN_USR", "APEX_PUBLIC_USER", "FLOWS_FILES", "OJVMSYS",
    "GSMADMIN_INTERNAL", "GSMCATUSER", "GSMUSER", "SYSBACKUP", "SYSDG",
    "SYSKM", "SYSRAC", "SYS$UMF", "DIP", "AUDSYS", "REMOTE_SCHEDULER_AGENT",
    "ORACLE_OCM", "DVSYS", "DVF", "GGSYS", "PDBADMIN", "MDDATA",
}


def _e_interna(nome: str) -> bool:
    u = (nome or "").strip().upper()
    return u in _INTERNAS or u.startswith(("APEX_", "FLOWS_", "XS$", "C##"))


class OperacaoAnalyzer(Analyzer):
    kind = "operacao"
    title = "Operacao e infraestrutura"

    def matches(self, filename: str, head: str) -> int:
        fn = os.path.basename(filename or "").lower()
        base = fn[:-4] if fn.endswith(".txt") else fn
        h = (head or "").upper()
        for chave, marcador in _ALVOS.items():
            if base == chave or marcador in h:
                return 95
        return 0

    # ── Contas expirando ──────────────────────────────────────
    def _contas(self, headers, rows) -> Section:
        t = limiar("accounts.txt", "dias_para_expirar")
        warn, crit = t.get("warn", 30.0), t.get("crit", 7.0)
        ci_user = find_col(headers, "username", "user")
        ci_dias = find_col(headers, "dias")
        ci_st = find_col(headers, "account_status", "status")
        ci_prof = find_col(headers, "profile")

        sec = Section(title="Contas expirando ou bloqueadas",
                      table_headers=headers, table_rows=rows[:25])
        urgentes, proximas, expiradas, vencidas = [], [], [], []
        for r in rows:
            user = r[ci_user].strip() if 0 <= ci_user < len(r) else "?"
            if _e_interna(user):
                continue
            st = (r[ci_st].strip().upper() if 0 <= ci_st < len(r) else "")
            prof = r[ci_prof].strip() if 0 <= ci_prof < len(r) else ""
            d = num(r[ci_dias]) if 0 <= ci_dias < len(r) else None
            if "EXPIRED" in st:
                expiradas.append((user, st, prof))
            elif d is not None and d < 0:
                # Data no passado com status ainda OPEN: o Oracle so marca
                # EXPIRED na proxima autenticacao (ou durante o grace period).
                # Chamar isto de "expira em -2419 dias" seria constrangedor
                # num relatorio - a conta JA venceu.
                vencidas.append((user, -d, prof))
            elif d is not None and d <= crit:
                urgentes.append((user, d, prof))
            elif d is not None and d <= warn:
                proximas.append((user, d, prof))

        sev = (Severity.CRIT if (expiradas or vencidas or urgentes) else
               Severity.WARN if proximas else Severity.OK)
        sec.metrics.append(Metric(
            "Contas de aplicacao em risco",
            str(len(expiradas) + len(vencidas) + len(urgentes) + len(proximas)),
            severity=sev))
        if expiradas:
            sec.findings.append(Finding(
                title=f"{len(expiradas)} conta(s) ja com senha EXPIRADA",
                detail="; ".join(f"{u} ({s})" for u, s, _ in expiradas[:6]),
                severity=Severity.CRIT, impact="login negado (ORA-28001)",
                recommendation=(
                    "Se a aplicacao ainda conecta, e porque a sessao e antiga: "
                    "a proxima reconexao falha. Troque a senha "
                    "(ALTER USER <u> IDENTIFIED BY <nova>) em janela combinada "
                    "com o time da aplicacao - a senha tambem muda no lado "
                    "dela - e depois mova para profile sem expiracao."),
            ))
        if vencidas:
            piores = sorted(vencidas, key=lambda x: -x[1])[:6]
            sec.findings.append(Finding(
                title=f"{len(vencidas)} conta(s) com a senha JA vencida",
                detail="; ".join(f"{u}: venceu ha {d:.0f} dia(s)"
                                 for u, d, _ in piores)
                + ". O status ainda nao mostra EXPIRED porque o Oracle so "
                  "marca na proxima autenticacao (ou durante o grace period).",
                severity=Severity.CRIT,
                impact="login pode falhar a qualquer reconexao",
                recommendation=(
                    "Nao espere o incidente: a proxima reconexao da aplicacao "
                    "pode receber ORA-28001. Confirme em DBA_USERS o "
                    "PASSWORD_GRACE_TIME do profile, troque a senha em janela "
                    "combinada e mova contas de servico para profile sem "
                    "expiracao."),
            ))
        if urgentes:
            piores = sorted(urgentes, key=lambda x: x[1])[:6]
            sec.findings.append(Finding(
                title=f"{len(urgentes)} conta(s) expiram em ate {crit:.0f} dia(s)",
                detail="; ".join(f"{u}: {d:.0f}d (profile {p})"
                                 for u, d, p in piores),
                severity=Severity.CRIT,
                impact=f"queda em {min(d for _, d, _ in urgentes):.0f} dia(s)",
                recommendation=(
                    "Trocar antes e manutencao; trocar depois e incidente. "
                    "Agende a troca AGORA ou mova a conta para um profile com "
                    "PASSWORD_LIFE_TIME UNLIMITED (registre a excecao de "
                    "seguranca). Verifique tambem PASSWORD_GRACE_TIME."),
            ))
        if proximas and not urgentes:
            sec.findings.append(Finding(
                title=f"{len(proximas)} conta(s) expiram em ate {warn:.0f} dias",
                detail="; ".join(f"{u}: {d:.0f}d" for u, d, _ in proximas[:8]),
                severity=Severity.WARN, impact="expiracao proxima",
                recommendation=("Planeje a troca na proxima janela. Confirme "
                                "com a aplicacao onde a senha esta configurada "
                                "(wallet, datasource, arquivo de propriedades)."),
            ))
        return sec

    # ── Jobs do scheduler ─────────────────────────────────────
    def _jobs(self, headers, rows) -> Section:
        t = limiar("jobs.txt", "falhas")
        warn, crit = t.get("warn", 1.0), t.get("crit", 10.0)
        ci_job = find_col(headers, "job_name", "job")
        ci_falhas = find_col(headers, "falhas")
        ci_erro = find_col(headers, "erro", "additional_info")
        ci_owner = find_col(headers, "owner")

        sec = Section(title="Jobs do scheduler com falha (7 dias)",
                      table_headers=headers, table_rows=rows[:20])
        total = 0
        piores = []
        for r in rows:
            n = num(r[ci_falhas]) if 0 <= ci_falhas < len(r) else 1.0
            n = n or 1.0
            total += n
            job = r[ci_job].strip() if 0 <= ci_job < len(r) else "?"
            owner = r[ci_owner].strip() if 0 <= ci_owner < len(r) else ""
            erro = r[ci_erro].strip() if 0 <= ci_erro < len(r) else ""
            piores.append((f"{owner}.{job}" if owner else job, n, erro))

        sev = (Severity.CRIT if total >= crit else
               Severity.WARN if total >= warn else Severity.OK)
        sec.metrics.append(Metric("Execucoes com falha", f"{total:.0f}",
                                  severity=sev))
        if piores:
            piores.sort(key=lambda x: -x[1])
            # Job de manutencao automatica falhando explica sintoma que o DBA
            # tenta resolver no lugar errado (plano ruim, espaco que nao volta).
            auto = [p for p in piores
                    if any(k in p[0].upper() for k in
                           ("STATS", "AUTOTASK", "SPACE", "SQL_TUNING",
                            "PURGE", "GATHER"))]
            if auto:
                nome, n, erro = auto[0]
                sec.findings.append(Finding(
                    title=f"Tarefa automatica {nome} falhando ({n:.0f}x)",
                    detail=(f"Erro: {erro}. " if erro else "")
                    + "Manutencao automatica parada e causa raiz silenciosa: "
                      "estatistica velha vira plano ruim, purga parada vira "
                      "espaco que nunca volta.",
                    severity=Severity.CRIT, impact=f"{n:.0f} falha(s)",
                    recommendation=(
                        "Leia o erro real em DBA_SCHEDULER_JOB_RUN_DETAILS "
                        "(ADDITIONAL_INFO) e confirme a janela de manutencao "
                        "em DBA_SCHEDULER_WINDOWS. Corrija a causa antes de "
                        "tratar os sintomas (plano/espaco) - senao eles "
                        "voltam."),
                ))
            if sev in (Severity.WARN, Severity.CRIT) and not auto:
                nome, n, erro = piores[0]
                sec.findings.append(Finding(
                    title=f"{len(piores)} job(s) com falha; pior: {nome}",
                    detail=(f"{n:.0f} falha(s). " + (f"Erro: {erro}" if erro else "")),
                    severity=sev, impact=f"{total:.0f} execucao(oes)",
                    recommendation=(
                        "Priorize por consequencia: job de negocio parado e "
                        "incidente; job de manutencao parado e causa raiz "
                        "futura. Veja ADDITIONAL_INFO para o erro exato."),
                ))
        return sec

    # ── Undo / ORA-01555 ──────────────────────────────────────
    def _undo(self, headers, rows) -> Section:
        t = limiar("undo_errors.txt", "ora_01555")
        warn, crit = t.get("warn", 1.0), t.get("crit", 5.0)
        sec = Section(title="Undo: ORA-01555 e espaco",
                      table_headers=headers, table_rows=rows[:5])
        if not rows:
            return sec
        r = rows[0]
        g = lambda *a: (num(r[find_col(headers, *a)])
                        if 0 <= find_col(headers, *a) < len(r) else None)
        ora1555 = g("ora_01555", "ssolderr") or 0.0
        sem_esp = g("sem_espaco", "nospace") or 0.0
        maior_q = g("maior_query", "maxquery") or 0.0
        retencao = g("retencao_efetiva", "tuned_undo") or 0.0

        sev = (Severity.CRIT if ora1555 >= crit or sem_esp > 0 else
               Severity.WARN if ora1555 >= warn else Severity.OK)
        sec.metrics.append(Metric("ORA-01555 (24h)", f"{ora1555:.0f}", severity=sev))
        sec.metrics.append(Metric("Sem espaco em undo", f"{sem_esp:.0f}",
                                  severity=Severity.CRIT if sem_esp else Severity.OK))
        sec.metrics.append(Metric("Maior consulta", f"{maior_q:.0f}s"))
        sec.metrics.append(Metric("Retencao efetiva", f"{retencao:.0f}s"))

        if sem_esp > 0:
            sec.findings.append(Finding(
                title=f"{sem_esp:.0f} erro(s) de falta de espaco em undo",
                detail=("A tablespace de undo nao coube a carga: transacao "
                        "abortada por espaco, nao por retencao."),
                severity=Severity.CRIT, impact=f"{sem_esp:.0f} erro(s)",
                recommendation=(
                    "Amplie a tablespace de undo (ou habilite AUTOEXTEND com "
                    "MAXSIZE) e so depois discuta retencao. Verifique tambem "
                    "transacao longa esquecida em v$transaction."),
            ))
        if ora1555 >= warn:
            # A pergunta que separa causa de sintoma: a consulta durou mais do
            # que a retencao efetiva? Entao o problema e a consulta.
            if maior_q > retencao > 0:
                rec = (f"A maior consulta durou {maior_q:.0f}s contra "
                       f"{retencao:.0f}s de retencao efetiva: aumentar undo "
                       "adia o erro, nao resolve. Ataque a consulta longa "
                       "(indice, particao, paralelismo) ou isole o relatorio "
                       "num standby/janela.")
            else:
                rec = ("Retencao efetiva menor que o necessario. Confirme se "
                       "a tablespace de undo tem espaco (o tuned_undoretention "
                       "encolhe sob pressao de espaco) e considere "
                       "RETENTION GUARANTEE se o relatorio for critico - "
                       "ciente de que DML pode falhar por espaco.")
            sec.findings.append(Finding(
                title=f"{ora1555:.0f} ocorrencia(s) de ORA-01555 em 24h",
                detail=(f"Maior consulta: {maior_q:.0f}s. Retencao efetiva: "
                        f"{retencao:.0f}s."),
                severity=sev, impact=f"{ora1555:.0f} consulta(s) abortada(s)",
                recommendation=rec))
        return sec

    # ── Host (USE) ────────────────────────────────────────────
    def _osstat(self, headers, rows) -> Section:
        """v$osstat vem em formato nome/valor: consolida por instancia."""
        tl = limiar("osstat.txt", "load_por_cpu")
        tm = limiar("osstat.txt", "memoria_livre_pct")
        warn_l, crit_l = tl.get("warn", 1.0), tl.get("crit", 2.0)
        warn_m, crit_m = tm.get("warn", 10.0), tm.get("crit", 5.0)

        ci_inst = find_col(headers, "inst_id", "inst")
        ci_nome = find_col(headers, "stat_name", "stat")
        ci_val = find_col(headers, "value", "valor")
        por_inst: dict = {}
        for r in rows:
            inst = r[ci_inst].strip() if 0 <= ci_inst < len(r) else "1"
            nome = (r[ci_nome].strip().upper() if 0 <= ci_nome < len(r) else "")
            val = num(r[ci_val]) if 0 <= ci_val < len(r) else None
            if nome and val is not None:
                por_inst.setdefault(inst, {})[nome] = val

        sec = Section(title="Recursos do host (CPU e memoria)",
                      table_headers=headers, table_rows=rows[:20])
        for inst, d in sorted(por_inst.items()):
            cpus = d.get("NUM_CPUS") or d.get("NUM_CPU_CORES") or 0
            load = d.get("LOAD")
            total = d.get("PHYSICAL_MEMORY_BYTES") or 0
            livre = d.get("FREE_MEMORY_BYTES")
            sufixo = f" (inst {inst})" if len(por_inst) > 1 else ""

            if load is not None and cpus:
                por_cpu = load / cpus
                sev = (Severity.CRIT if por_cpu >= crit_l else
                       Severity.WARN if por_cpu >= warn_l else Severity.OK)
                sec.metrics.append(Metric(f"Load por CPU{sufixo}",
                                          f"{por_cpu:.2f}", severity=sev))
                if sev in (Severity.WARN, Severity.CRIT):
                    sec.findings.append(Finding(
                        title=(f"Host com load {load:.1f} para {cpus:.0f} CPU(s)"
                               f"{sufixo}"),
                        detail=(f"{por_cpu:.2f} processo(s) por CPU na fila. "
                                "Acima de 1 a sessao espera CPU antes de "
                                "executar: todo tempo de resposta piora junto, "
                                "inclusive o de SQL bem escrito."),
                        severity=sev, impact=f"load/CPU {por_cpu:.2f}",
                        recommendation=(
                            "Confirme quem consome o host antes de tunar SQL: "
                            "outra instancia, backup, agente de monitoracao ou "
                            "processo fora do banco. Se a carga e do proprio "
                            "banco, reduza paralelismo/concorrencia ou trate o "
                            "top SQL por DB time (Method R)."),
                    ))
            if livre is not None and total:
                pct = livre / total * 100.0
                sev = (Severity.CRIT if pct <= crit_m else
                       Severity.WARN if pct <= warn_m else Severity.OK)
                # ── SATURACAO, e nao so utilizacao ────────────
                # No Linux memoria livre baixa e o comportamento NORMAL: o SO
                # usa a RAM sobrando como page cache e a devolve sob pressao.
                # O que separa "usando bem a RAM" de "sem RAM" e paginacao.
                #
                # O metodo USE - que as regras deste projeto citam - separa
                # utilizacao de saturacao exatamente por isso. E a evidencia de
                # saturacao ja estava COLETADA no mesmo arquivo: a propria
                # recomendacao deste achado mandava o DBA "verificar
                # VM_IN/VM_OUT_BYTES", que o analisador tinha no dict ao lado.
                #
                # Medido na frota: 11 servidores com menos de 10% livre, e o
                # APPHO114 - o pior score de todos - com 22,8% livre e
                # paginacao ZERO. Utilizacao alta sem saturacao nao e o mesmo
                # problema e nao merece a mesma cor.
                #
                # Cuidado: VM_IN/VM_OUT sao CUMULATIVOS desde o startup da
                # instancia, entao valor maior que zero prova que o host JA
                # paginou nessa vida - nao que esteja paginando agora. Por isso
                # o texto diz "ja paginou", e nao "esta paginando".
                #
                # Isto NAO contradiz o leitor de `saida.py`, que registra que
                # swap zerado nao refuta falta de memoria: la o assunto e o
                # shared pool (ORA-04031 acontece com swap limpo); aqui e RAM
                # do host.
                vm = (d.get("VM_IN_BYTES") or 0) + (d.get("VM_OUT_BYTES") or 0)
                paginou = vm > 0
                if sev == Severity.CRIT and not paginou:
                    sev = Severity.WARN
                sec.metrics.append(Metric(f"Memoria livre{sufixo}",
                                          f"{pct:.1f}%", severity=sev))
                if sev in (Severity.WARN, Severity.CRIT):
                    if paginou:
                        detalhe = (
                            f"O host ja paginou nesta vida da instancia "
                            f"({vm / 1024 ** 3:.1f} GB entre VM_IN e VM_OUT): "
                            "ha utilizacao alta E evidencia de saturacao. Com "
                            "swap, o tempo de resposta degrada de forma "
                            "erratica e sem causa visivel dentro do Oracle.")
                    else:
                        detalhe = (
                            "Paginacao ZERO (VM_IN e VM_OUT em 0) desde o "
                            "startup: no Linux, memoria livre baixa sem swap e "
                            "o SO usando a RAM como page cache, que ele "
                            "devolve sob pressao. Vale acompanhar o "
                            "dimensionamento, nao tratar como incidente - por "
                            "isso ATENCAO e nao critico.")
                    sec.findings.append(Finding(
                        title=(f"Host com {pct:.1f}% de memoria livre{sufixo}"
                               + ("" if paginou else " (sem paginacao)")),
                        detail=detalhe,
                        severity=sev, impact=f"{pct:.1f}% livre",
                        recommendation=(
                            "Some SGA+PGA de TODAS as instancias do host e "
                            "compare com a memoria fisica. Confira HugePages: "
                            "sem elas, a page table de um SGA grande consome "
                            "memoria por conexao."
                            + ("" if paginou else
                               " Sem paginacao, confirme no SO se a memoria "
                               "'livre' nao esta apenas em cache "
                               "(`free -m`: coluna available)."))
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
            if base == "accounts" or "CONTAS EXPIRANDO" in up:
                sections.append(self._contas(headers, rows))
            elif base == "jobs" or "JOBS COM FALHA" in up:
                sections.append(self._jobs(headers, rows))
            elif base == "undo_errors" or "UNDO: ORA-01555" in up:
                sections.append(self._undo(headers, rows))
            elif base == "osstat" or "RECURSOS DO HOST" in up:
                sections.append(self._osstat(headers, rows))

        if not sections:
            return [], ""
        overall = worst([s.severity for s in sections])
        if overall == Severity.CRIT:
            resumo = ("Falha operacional ou de infraestrutura em curso: "
                      "trate antes de investigar SQL.")
        elif overall == Severity.WARN:
            resumo = "Operacao com pendencia previsivel (agendavel)."
        else:
            resumo = "Operacao e host sem sinal: contas, jobs, undo e recursos ok."
        return sections, resumo
