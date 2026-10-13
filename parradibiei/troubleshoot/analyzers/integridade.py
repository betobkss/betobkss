"""
Analisador de INTEGRIDADE e RECUPERABILIDADE.

Cobre a familia de falhas que nao aparece em wait event nenhum e por isso
passava batido num framework focado em performance:

- `corruption.txt`      blocos corrompidos (v$database_block_corruption)
- `datafile_status.txt` datafiles fora de ONLINE / precisando de recover
- `archive_dest.txt`    destino de archive com erro (caminho para ORA-00257)
- `backup_age.txt`      idade do ultimo backup POR DATAFILE (RPO real)
- `archive_gap.txt`     sequencias que o standby nunca recebeu

Principio: aqui nao existe "atencao" para corrupcao nem para arquivo sem
recuperacao. Ou o dado esta integro e recuperavel, ou e critico.
"""
from __future__ import annotations

import os

from ..model import Finding, Metric, Section, Severity, worst
from .base import Analyzer, find_col, num, parse_fixed_width_tables, read_text
from .saude import limiar

# arquivo (sem .txt) -> marcador do subtitulo gerado pelo catalogo
_ALVOS = {
    "corruption": "BLOCOS CORROMPIDOS",
    "datafile_status": "DATAFILES FORA DE ONLINE",
    "archive_dest": "ARCHIVE DEST STATUS",
    "backup_age": "IDADE DO BACKUP POR DATAFILE",
    "archive_gap": "ARCHIVE GAP",
}


class IntegridadeAnalyzer(Analyzer):
    kind = "integridade"
    title = "Integridade e recuperabilidade"

    def matches(self, filename: str, head: str) -> int:
        fn = os.path.basename(filename or "").lower()
        base = fn[:-4] if fn.endswith(".txt") else fn
        h = (head or "").upper()
        for chave, marcador in _ALVOS.items():
            # score alto de proposito: `backup_age.txt` tambem casa com o
            # RmanAnalyzer (regex "rman|backup"), e quem manda aqui e este.
            if base == chave or marcador in h:
                return 95
        return 0

    # ── Corrupcao de bloco ────────────────────────────────────
    def _corrupcao(self, headers, rows) -> Section:
        sec = Section(title="Blocos corrompidos",
                      table_headers=headers, table_rows=rows[:20])
        if not rows:
            sec.metrics.append(Metric("Blocos corrompidos", "0",
                                      severity=Severity.OK))
            sec.note = "Nenhuma corrupcao conhecida registrada pelo banco."
            return sec

        ci_tipo = find_col(headers, "corruption_type", "type")
        ci_file = find_col(headers, "file_id", "file")
        ci_blk = find_col(headers, "block_id", "block")
        tipos = sorted({(r[ci_tipo].strip() if 0 <= ci_tipo < len(r) else "?")
                        for r in rows})
        blocos = sum((num(r[find_col(headers, "blocks")]) or 1.0)
                     for r in rows) if find_col(headers, "blocks") >= 0 else len(rows)

        sec.metrics.append(Metric("Blocos corrompidos", f"{blocos:.0f}",
                                  severity=Severity.CRIT))
        sec.metrics.append(Metric("Tipos", ", ".join(tipos)[:40],
                                  severity=Severity.CRIT))
        primeiro = rows[0]
        onde = (f"file {primeiro[ci_file]} block {primeiro[ci_blk]}"
                if 0 <= ci_file < len(primeiro) and 0 <= ci_blk < len(primeiro)
                else "ver tabela")
        sec.findings.append(Finding(
            title=f"{len(rows)} bloco(s) corrompido(s) no banco",
            detail=(f"Tipos: {', '.join(tipos)}. Primeira ocorrencia em {onde}. "
                    "Corrupcao nao se resolve sozinha e nao aparece em wait "
                    "event: so e vista por quem procura."),
            severity=Severity.CRIT, impact=f"{blocos:.0f} bloco(s)",
            recommendation=(
                "1) Identifique o objeto: SELECT owner, segment_name, "
                "segment_type FROM dba_extents WHERE file_id=<F> AND <B> "
                "BETWEEN block_id AND block_id+blocks-1. "
                "2) Recupere com RMAN: BLOCKRECOVER DATAFILE <F> BLOCK <B> "
                "(precisa de backup valido). "
                "3) Se for indice, recriar resolve; se for tabela, avalie "
                "extracao com DBMS_REPAIR/SKIP_CORRUPT. "
                "4) LOGICAL corruption sem falha de disco = suspeite de bug: "
                "leve a versao exata ao MOS."),
        ))
        return sec

    # ── Datafiles fora de ONLINE ──────────────────────────────
    def _datafiles(self, headers, rows) -> Section:
        sec = Section(title="Datafiles fora de ONLINE",
                      table_headers=headers, table_rows=rows[:20])
        if not rows:
            sec.metrics.append(Metric("Datafiles com problema", "0",
                                      severity=Severity.OK))
            sec.note = "Todos os datafiles ONLINE e sem pendencia de recover."
            return sec

        ci_st = find_col(headers, "file_status", "status")
        ci_err = find_col(headers, "erro", "error")
        sec.metrics.append(Metric("Datafiles com problema", str(len(rows)),
                                  severity=Severity.CRIT))
        for r in rows[:5]:
            st = r[ci_st].strip() if 0 <= ci_st < len(r) else "?"
            err = r[ci_err].strip() if 0 <= ci_err < len(r) else ""
            fid = r[0].strip() if r else "?"
            sec.findings.append(Finding(
                title=f"Datafile {fid} em status {st}",
                detail=(f"Erro reportado: {err}. " if err and err != "-" else "")
                + "Enquanto o arquivo nao voltar, os objetos dele estao "
                  "inacessiveis para a aplicacao.",
                severity=Severity.CRIT, impact=st,
                recommendation=(
                    "Confirme se o archivelog necessario existe "
                    "(SELECT * FROM v$archived_log WHERE sequence# >= ...). "
                    "Com ele: RECOVER DATAFILE <n>; ALTER DATABASE DATAFILE "
                    "<n> ONLINE. Sem ele, restore do backup. NUNCA use "
                    "OFFLINE DROP sem entender a consequencia."),
            ))
        return sec

    # ── Destino de archive ────────────────────────────────────
    def _archive_dest(self, headers, rows) -> Section:
        """ORA-00257 e a falha mais previsivel do Oracle: o destino da erro,
        ninguem ve, e o banco congela quando os grupos de redo dao a volta."""
        sec = Section(title="Destinos de archive",
                      table_headers=headers, table_rows=rows[:20])
        ci_st = find_col(headers, "status")
        ci_err = find_col(headers, "erro", "error")
        ci_nome = find_col(headers, "dest_name", "dest")
        ruins = []
        for r in rows:
            st = (r[ci_st].strip().upper() if 0 <= ci_st < len(r) else "")
            if st and st not in ("VALID", "INACTIVE", "DEFERRED"):
                ruins.append(r)

        sec.metrics.append(Metric("Destinos ativos", str(len(rows))))
        sec.metrics.append(Metric("Com erro", str(len(ruins)),
                                  severity=Severity.CRIT if ruins else Severity.OK))
        for r in ruins[:4]:
            nome = r[ci_nome].strip() if 0 <= ci_nome < len(r) else "?"
            st = r[ci_st].strip() if 0 <= ci_st < len(r) else "?"
            err = r[ci_err].strip() if 0 <= ci_err < len(r) else ""
            sec.findings.append(Finding(
                title=f"Destino de archive {nome} em {st}",
                detail=(f"{err}. " if err and err != "-" else "")
                + "Com o destino em erro o redo nao arquiva; quando os grupos "
                  "derem a volta, TODA transacao para com ORA-00257.",
                severity=Severity.CRIT, impact=st,
                recommendation=(
                    "Trate como incidente em curso: libere espaco no destino "
                    "(ou na FRA), confirme o caminho em LOG_ARCHIVE_DEST_n e "
                    "reabilite com ALTER SYSTEM SET LOG_ARCHIVE_DEST_STATE_n="
                    "ENABLE; depois force com ALTER SYSTEM ARCHIVE LOG ALL e "
                    "confirme que o STATUS voltou a VALID."),
            ))
        return sec

    # ── Idade do backup por datafile ──────────────────────────
    def _backup_age(self, headers, rows) -> Section:
        """Job COMPLETED nao significa banco recuperavel: o RPO real e o do
        datafile mais atrasado. Um tablespace novo fora do script de backup
        passa meses invisivel enquanto o relatorio diz 'backup OK'."""
        t = limiar("backup_age.txt", "dias_sem_backup")
        warn, crit = t.get("warn", 2.0), t.get("crit", 7.0)
        ci_dias = find_col(headers, "dias")
        ci_ts = find_col(headers, "tablespace")
        ci_ult = find_col(headers, "ultimo_backup", "ultimo")

        sec = Section(title="Idade do backup por datafile (RPO real)",
                      table_headers=headers, table_rows=rows[:20])
        nunca, atrasados, pior = [], [], 0.0
        for r in rows:
            ts = r[ci_ts].strip() if 0 <= ci_ts < len(r) else "?"
            ult = (r[ci_ult].strip().upper() if 0 <= ci_ult < len(r) else "")
            d = num(r[ci_dias]) if 0 <= ci_dias < len(r) else None
            if ult == "NUNCA" or d is None:
                nunca.append(ts)
            else:
                pior = max(pior, d)
                if d >= warn:
                    atrasados.append((ts, d))

        sev = (Severity.CRIT if nunca or pior >= crit else
               Severity.WARN if pior >= warn else Severity.OK)
        sec.metrics.append(Metric("Pior RPO", f"{pior:.1f} dia(s)", severity=sev))
        sec.metrics.append(Metric("Nunca copiados", str(len(nunca)),
                                  severity=Severity.CRIT if nunca else Severity.OK))

        if nunca:
            sec.findings.append(Finding(
                title=f"{len(nunca)} datafile(s) NUNCA copiado(s) pelo RMAN",
                detail="Tablespaces: " + ", ".join(sorted(set(nunca))[:8])
                + ". Sem backup, esses dados nao voltam - nao ha o que "
                  "restaurar.",
                severity=Severity.CRIT, impact=f"{len(nunca)} datafile(s)",
                recommendation=(
                    "Verifique se o tablespace e READ ONLY e ja tem copia "
                    "antiga valida (ai pode ser politica). Se nao for, rode "
                    "BACKUP DATABASE hoje e corrija o script para incluir "
                    "todos os tablespaces - o padrao seguro e backup do "
                    "DATABASE, nao lista fixa de tablespace."),
            ))
        elif atrasados:
            piores = sorted(atrasados, key=lambda x: -x[1])[:5]
            sec.findings.append(Finding(
                title=f"Backup mais antigo com {pior:.1f} dia(s)",
                detail="; ".join(f"{ts}: {d:.1f}d" for ts, d in piores),
                severity=sev, impact=f"RPO de {pior:.1f} dia(s)",
                recommendation=(
                    "Compare com o RPO acordado para o servico. Se o ciclo "
                    "full+incremental nao esta fechando, o problema costuma "
                    "ser janela ou espaco: confira falhas em "
                    "v$rman_backup_job_details e o uso da FRA."),
            ))
        return sec

    # ── Gap para o standby ────────────────────────────────────
    def _archive_gap(self, headers, rows) -> Section:
        sec = Section(title="Gap de archivelog (standby)",
                      table_headers=headers, table_rows=rows[:20])
        if not rows:
            sec.metrics.append(Metric("Gaps", "0", severity=Severity.OK))
            sec.note = "Standby sem gap de sequencia."
            return sec
        sec.metrics.append(Metric("Gaps", str(len(rows)), severity=Severity.CRIT))
        sec.findings.append(Finding(
            title=f"{len(rows)} gap(s) de archivelog para o standby",
            detail=("Enquanto houver gap, o standby nao aplica adiante e o "
                    "RPO anunciado nao existe. Um failover neste estado perde "
                    "tudo o que esta no intervalo."),
            severity=Severity.CRIT, impact=f"{len(rows)} intervalo(s)",
            recommendation=(
                "1) Verifique o transporte em v$archive_dest_status na "
                "primaria. 2) Se os archives ainda existem, reenvie "
                "(ALTER SYSTEM ARCHIVE LOG ALL) ou copie e registre no "
                "standby com ALTER DATABASE REGISTER LOGFILE. 3) Se ja foram "
                "apagados, a saida e backup incremental a partir da SCN do "
                "standby (MOS 836986.1)."),
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

            if base == "corruption" or "BLOCOS CORROMPIDOS" in up:
                sections.append(self._corrupcao(headers, rows))
            elif base == "datafile_status" or "DATAFILES FORA DE ONLINE" in up:
                sections.append(self._datafiles(headers, rows))
            elif base == "archive_dest" or "ARCHIVE DEST STATUS" in up:
                sections.append(self._archive_dest(headers, rows))
            elif base == "backup_age" or "IDADE DO BACKUP" in up:
                sections.append(self._backup_age(headers, rows))
            elif base == "archive_gap" or "ARCHIVE GAP" in up:
                sections.append(self._archive_gap(headers, rows))

        if not sections:
            return [], ""
        overall = worst([s.severity for s in sections])
        n_crit = sum(1 for s in sections for f in s.findings
                     if f.severity == Severity.CRIT)
        if overall == Severity.CRIT:
            resumo = (f"{n_crit} achado(s) critico(s) de integridade ou "
                      "recuperabilidade: tratar antes de qualquer tuning.")
        elif overall == Severity.WARN:
            resumo = "Recuperabilidade em atencao: RPO real acima do esperado."
        else:
            resumo = ("Dado integro e recuperavel: sem corrupcao, datafiles "
                      "ONLINE, archive fluindo e backup em dia.")
        return sections, resumo
