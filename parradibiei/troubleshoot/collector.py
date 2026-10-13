"""
Coletor read-only para troubleshooting (conexao direta SQL*Net).

Conecta no Oracle via python-oracledb (modo thin, sem client), abre a sessao
como READ ONLY, executa APENAS SELECTs do conjunto de coleta e grava cada saida
como tabela de largura fixa em troubleshooting/<caso>/, no formato que os
analisadores do framework entendem.

Seguranca:
- Apenas comandos SELECT/WITH sao permitidos (validate_select).
- SET TRANSACTION READ ONLY antes de qualquer query (impede DML/DDL).
- Nenhum COMMIT; autocommit desligado.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

CASES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "troubleshooting")

_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|TRUNCATE|ALTER|CREATE|GRANT|REVOKE|"
    r"BEGIN|DECLARE|CALL|EXECUTE|COMMIT|SAVEPOINT|LOCK|FLASHBACK|PURGE|RENAME)\b",
    re.I)


def validate_select(sql: str) -> bool:
    """True apenas se a query for uma leitura pura (SELECT/WITH sem comandos DML/DDL)."""
    s = sql.strip().rstrip(";").lstrip("(").strip()
    if not re.match(r"(?is)^\s*(SELECT|WITH)\b", s):
        return False
    # remove strings literais para nao pegar palavras dentro de textos
    s_no_str = re.sub(r"'[^']*'", "''", s)
    return _FORBIDDEN.search(s_no_str) is None


def render_table(headers, rows, max_col: int = 60, max_rows: int = 50) -> str:
    """Renderiza uma tabela de largura fixa (header + separador + linhas)."""
    cols = [str(h) for h in headers]
    widths = [len(c) for c in cols]
    srows = []
    for r in rows[:max_rows]:
        cells = []
        for v in r:
            txt = "" if v is None else str(v)
            txt = txt.replace("\n", " ").replace("\r", " ")
            if len(txt) > max_col:
                txt = txt[:max_col - 1] + "\u2026"
            cells.append(txt)
        srows.append(cells)
    for r in srows:
        for i, v in enumerate(r):
            if i < len(widths):
                widths[i] = max(widths[i], len(v))
    def line(vals):
        return " ".join(str(vals[i]).ljust(widths[i]) if i < len(vals) else "".ljust(widths[i])
                        for i in range(len(cols)))
    out = [line(cols), " ".join("-" * w for w in widths)]
    out += [line(r) for r in srows]
    return "\n".join(out)


# ── Conjunto de coleta (nome_arquivo -> lista de (subtitulo, sql)) ──
# Todas as SQL sao SELECT puros; itens 'optional' nao quebram se derem ORA-.
COLLECT = [
    ("ash.txt", False, [
        ("TOP WAIT CLASSES (ASH 60min)",
         "SELECT NVL(wait_class,'CPU') AS wait_class, COUNT(*) AS samples "
         "FROM gv$active_session_history WHERE sample_time > SYSDATE - 1/24 "
         "GROUP BY NVL(wait_class,'CPU') ORDER BY samples DESC"),
        ("TOP EVENTS (ASH 60min)",
         "SELECT * FROM (SELECT NVL(event,'ON CPU') AS event, COUNT(*) AS samples "
         "FROM gv$active_session_history WHERE sample_time > SYSDATE - 1/24 "
         "GROUP BY NVL(event,'ON CPU') ORDER BY samples DESC) WHERE ROWNUM <= 15"),
    ]),
    # Arvore (CONNECT BY) e nao lista de pares: o bloqueador RAIZ aparece no topo.
    # NOCYCLE e obrigatorio - deadlock forma ciclo e daria ORA-01436.
    # TYPE='USER' exclui background: o LGWR "bloqueia" seus workers e seria falso
    # positivo permanente. WAIT_CLASS <> 'Idle' na vitima exclui parallel query
    # (PX Deq: ...), que preenche BLOCKING_SESSION entre coordenador e escravos -
    # a propria coleta paralela aparecia como bloqueio. A raiz pode ser Idle: e
    # justamente o caso da transacao aberta sem commit.
    # NVL2 no objeto porque NULL||'.'||NULL vale '.', nao NULL.
    # Manter igual ao check 'blocking_chain' do mcp/catalog.json.
    ("locks.txt", False, [
        ("ARVORE DE BLOQUEIO (AGORA)",
         "SELECT t.arvore, t.nivel, t.sid, t.serial, t.username, t.status, t.blocker, "
         "t.event, t.secs, t.idle_min, "
         "NVL2(o.object_id, o.owner||'.'||o.object_name, '-') AS objeto, "
         "t.sql_id FROM (SELECT LPAD(' ',2*(LEVEL-1))||s.inst_id||':'||s.sid AS arvore, "
         "LEVEL AS nivel, s.sid, s.serial, s.username, s.status, "
         "s.blocking_session AS blocker, s.event, s.seconds_in_wait AS secs, "
         "ROUND(s.last_call_et/60,1) AS idle_min, s.objid, s.sql_id, ROWNUM AS ord "
         "FROM (SELECT inst_id, sid, serial# AS serial, username, status, blocking_session, "
         "blocking_instance, event, wait_class, seconds_in_wait, last_call_et, "
         "row_wait_obj# AS objid, sql_id FROM gv$session WHERE type = 'USER') s "
         "START WITH s.blocking_session IS NULL "
         "AND (s.inst_id, s.sid) IN (SELECT NVL(blocking_instance, inst_id), blocking_session "
         "FROM gv$session WHERE blocking_session IS NOT NULL AND type = 'USER' "
         "AND wait_class <> 'Idle') "
         "CONNECT BY NOCYCLE PRIOR s.sid = s.blocking_session "
         "AND PRIOR s.inst_id = NVL(s.blocking_instance, s.inst_id) "
         "AND s.wait_class <> 'Idle' "
         "ORDER SIBLINGS BY s.seconds_in_wait DESC) t "
         "LEFT JOIN dba_objects o ON o.object_id = t.objid AND t.objid > 0 ORDER BY t.ord"),
    ]),
    ("iostat.txt", False, [
        # inst_id e obrigatorio: gv$filestat devolve uma linha POR INSTANCIA e,
        # sem projetar o no, em RAC o mesmo arquivo aparece repetido sem que se
        # saiba qual no esta lento.
        ("IO POR ARQUIVO",
         "SELECT * FROM (SELECT fs.inst_id AS inst, df.tablespace_name AS tablespace, "
         "SUBSTR(df.file_name,-40) AS file_name, fs.phyrds, fs.phywrts, "
         "ROUND(fs.readtim*10/GREATEST(fs.phyrds,1),2) AS avg_read_ms, "
         "ROUND(fs.writetim*10/GREATEST(fs.phywrts,1),2) AS avg_write_ms "
         "FROM gv$filestat fs JOIN dba_data_files df ON df.file_id = fs.file# "
         "ORDER BY avg_read_ms DESC) WHERE ROWNUM <= 20"),
    ]),
    # PCT_USED e medido contra o TETO do autoextend (MAX_MB), nao contra o espaco
    # ja alocado - senao um tablespace com 87% alocado e 45 GB de folga vira
    # alarme falso. Manter igual ao check 'tablespaces' do mcp/catalog.json.
    ("space.txt", False, [
        ("TABLESPACES",
         "SELECT df.tablespace_name, ROUND(SUM(df.bytes)/1048576) AS alloc_mb, "
         "ROUND(SUM(df.bytes-NVL(fs.free,0))/1048576) AS used_mb, "
         "ROUND(SUM(CASE WHEN df.autoextensible='YES' "
         "THEN GREATEST(df.maxbytes,df.bytes) ELSE df.bytes END)/1048576) AS max_mb, "
         "ROUND(100*(1-SUM(NVL(fs.free,0))/SUM(df.bytes)),1) AS pct_alloc, "
         "ROUND(100*SUM(df.bytes-NVL(fs.free,0))/SUM(CASE WHEN df.autoextensible='YES' "
         "THEN GREATEST(df.maxbytes,df.bytes) ELSE df.bytes END),1) AS pct_used, "
         "MIN(df.autoextensible) AS autoext "
         "FROM dba_data_files df LEFT JOIN (SELECT file_id, SUM(bytes) free "
         "FROM dba_free_space GROUP BY file_id) fs ON df.file_id = fs.file_id "
         "GROUP BY df.tablespace_name ORDER BY pct_used DESC"),
    ]),
    ("asm.txt", True, [
        ("ASM DISKGROUPS",
         "SELECT name, state, type, total_mb, free_mb, "
         "ROUND(100*(1-free_mb/GREATEST(total_mb,1)),1) AS pct_used "
         "FROM v$asm_diskgroup ORDER BY pct_used DESC"),
    ]),
    ("sessions.txt", False, [
        ("TOP SESSOES ATIVAS",
         "SELECT * FROM (SELECT s.inst_id AS inst, s.sid||','||s.serial# AS sid, "
         "s.username, s.status, s.event, s.seconds_in_wait AS secs, s.sql_id "
         "FROM gv$session s WHERE s.type='USER' AND s.status='ACTIVE' "
         "AND s.username IS NOT NULL ORDER BY s.seconds_in_wait DESC) WHERE ROWNUM <= 20"),
    ]),
    ("stats.txt", False, [
        ("ESTATISTICAS STALE/ANTIGAS",
         "SELECT * FROM (SELECT owner||'.'||table_name AS table_name, "
         "TO_CHAR(last_analyzed,'YYYY-MM-DD HH24:MI') AS last_analyzed, "
         "stale_stats, num_rows FROM dba_tab_statistics "
         "WHERE owner NOT IN ('SYS','SYSTEM','XDB','MDSYS','CTXSYS','DBSNMP') "
         "AND (stale_stats='YES' OR last_analyzed < SYSDATE-7 OR last_analyzed IS NULL) "
         "ORDER BY last_analyzed NULLS FIRST) WHERE ROWNUM <= 30"),
    ]),
    ("dataguard.txt", True, [
        ("DATAGUARD STATS",
         "SELECT name, value, TO_CHAR(time_computed) AS time_computed "
         "FROM gv$dataguard_stats WHERE name IN ('transport lag','apply lag')"),
        ("MANAGED STANDBY",
         "SELECT inst_id, process, status, thread#, sequence#, block# "
         "FROM gv$managed_standby WHERE process LIKE 'MRP%' OR process LIKE 'RFS%' "
         "OR status <> 'IDLE' ORDER BY inst_id, process"),
    ]),
    ("rman.txt", True, [
        ("RMAN JOBS (7 dias)",
         "SELECT input_type, status, TO_CHAR(start_time,'YYYY-MM-DD HH24:MI') AS start_time, "
         "TO_CHAR(end_time,'YYYY-MM-DD HH24:MI') AS end_time, "
         "ROUND(input_bytes/1073741824,1) AS input_gb, "
         "ROUND(output_bytes/1073741824,1) AS output_gb, time_taken_display AS elapsed "
         "FROM v$rman_backup_job_details WHERE start_time > SYSDATE-7 ORDER BY start_time DESC"),
    ]),
    ("alert.log", True, [
        ("ALERT LOG - ORA- (24h)",
         "SELECT TO_CHAR(originating_timestamp,'YYYY-MM-DD\"T\"HH24:MI:SS') AS ts, "
         "message_text FROM v$diag_alert_ext "
         "WHERE originating_timestamp > SYSTIMESTAMP - INTERVAL '24' HOUR "
         "AND message_text LIKE '%ORA-%' ORDER BY originating_timestamp"),
    ]),
]


def _build_collect(allowed_packs=None, topo=None):
    """Monta a lista de coleta a partir do catalogo MCP (fonte unica de verdade).
    Exclui os checks 'special' (sqlmon/addm), tratados separadamente. Se
    allowed_packs for informado, filtra os checks por licenca (requires_pack);
    se topo for informado, filtra tambem por topologia (requires_topology) -
    nao faz sentido coletar global cache em standalone nem cells fora de Exadata.
    Fallback para o COLLECT estatico se o catalogo nao estiver disponivel."""
    try:
        from mcp import online_items
    except Exception:
        return COLLECT
    from .topologia import check_permitido
    order: list[str] = []
    groups: dict = {}
    for it in online_items():
        if it.get("special"):
            continue
        if allowed_packs is not None and it.get("requires_pack", "NONE") not in allowed_packs:
            continue
        if not check_permitido(it, topo):
            continue
        f = it["output_file"]
        if f not in groups:
            groups[f] = {"optional": True, "blocks": []}
            order.append(f)
        groups[f]["blocks"].append((it.get("subtitle") or f, it["sql"]))
        if not it["optional"]:
            groups[f]["optional"] = False
    result = [(f, groups[f]["optional"], groups[f]["blocks"]) for f in order]
    return result or COLLECT


_HEAVY_RE = re.compile(r"\bdba_hist", re.I)


def _is_heavy(blocks) -> bool:
    """True se algum SQL do grupo le o historico do AWR (DBA_HIST_*).
    Esses checks sao os mais lentos (janelas de 7-14 dias) e o modo rapido os pula."""
    return any(_HEAVY_RE.search(sql) for _, sql in blocks)


def _fetch_table(cur, sql):
    cur.execute(sql)
    headers = [d[0] for d in cur.description]
    rows = []
    for row in cur.fetchall():
        cells = []
        for v in row:
            if hasattr(v, "read"):  # LOB
                try:
                    v = v.read()
                except Exception:
                    v = "<LOB>"
            cells.append(v)
        rows.append(cells)
    return headers, rows


_PACK_LEVEL = {
    "T": {"NONE", "DIAGNOSTIC", "TUNING"},
    "D": {"NONE", "DIAGNOSTIC"},
    "N": {"NONE"},
}


# Skip que carrega assinatura de ERRO ou de TIMEOUT - o check nao rodou.
# Casado no CODIGO do erro e nao na prosa: ORA-/DPY- sao contrato do driver e
# nao mudam de texto; a mensagem e para o humano e muda.
_SKIP_RUIM = re.compile(r"\bORA-\d{5}\b|\bDPY-\d{4}\b|timeout|timed out|"
                        r"bloqueada", re.I)
_LINHA_SKIP = re.compile(r"^\s*(?:[\d-]+ [\d:]+ )?\[skip\]\s*(.+)$", re.M)


def checks_pulados_por_erro(texto_log: str) -> list:
    """Checks que o log declara pulados POR DEFEITO (nao por ausencia de dado).

    FONTE UNICA do vocabulario: a bateria (`TESTE_HOMOLOG.py`) classifica os
    sinais da coleta e o `TROUBLESHOOT.py` avisa no dashboard que a analise
    esta incompleta. Duas listas do que conta como "falhou" acabariam
    discordando - e a que discordasse em silencio decidiria se o DBA ve ou nao
    que faltou evidencia.

    `sem task ADDM` e ausencia legitima e NAO entra. `ORA-14552`, `DPY-4024:
    call timeout` e `query bloqueada` entram: ali a query nao rodou.

    O regex aceita a linha com e sem carimbo de hora, porque o stdout nao o
    leva e o `coleta.log` leva - e os dois chamadores leem fontes diferentes.
    """
    return [s.strip() for s in _LINHA_SKIP.findall(texto_log or "")
            if _SKIP_RUIM.search(s)]


def _descartar_obsoleto(case_dir: str, fname: str, slog) -> bool:
    """Apaga a evidencia da coleta ANTERIOR quando o check falha agora.

    Achado na bateria de 13/09, e o defeito mais silencioso que a coleta pode
    ter. A pasta do caso e reaproveitada entre coletas. Quando um check falha,
    o codigo saia sem escrever - e o arquivo da coleta passada FICAVA. Medido
    em HOMOLOG_APPHO114: 63 arquivos de 13/09 e DOIS de 10/09
    (`changes_params.txt` e `sqlmon.txt`), que eram exatamente os dois checks
    que falharam. O `meta.json` dizia `collected_at: 2026-09-13`.

    Ou seja: o analisador lia evidencia de TRES DIAS ATRAS como se fosse de
    agora, e o relatorio a apresentava com o carimbo de hoje. Num incidente, o
    DBA poderia agir sobre uma mudanca de parametro que ja tinha sido revertida.
    Pior ainda para a leitura de IDADE (estatistica, mudanca recente), que mede
    contra o `collected_at` do meta - a data certa para o arquivo errado.

    Apagar so acontece quando ESTA coleta tentou o check e nao produziu nada.
    Arquivo que a coleta nem tentou (modo `--parcial`, por exemplo) nao e
    tocado: la nao houve tentativa, entao nao ha promessa quebrada.

    Ausencia e melhor que dado velho por um motivo simples: a ausencia e
    visivel (a bateria acusa em ARTEFATO, o relatorio mostra a secao faltando)
    e o dado velho se parece com dado bom.
    """
    p = os.path.join(case_dir, fname)
    if not os.path.exists(p):
        return False
    try:
        os.remove(p)
        slog(f"[skip] {fname}: evidencia da coleta anterior DESCARTADA "
             f"(o check falhou agora - dado velho com carimbo novo enganaria)")
        return True
    except OSError as ex:
        # Nao poder apagar e pior que nao tentar: avisa alto, porque a partir
        # daqui o relatorio vai misturar coletas.
        slog(f"[warn] {fname}: falhou o check E nao consegui apagar a "
             f"evidencia antiga ({ex}) - o relatorio vai mistura-la com a "
             f"coleta de agora")
        return False


def _detect_packs(cur):
    """Le CONTROL_MANAGEMENT_PACK_ACCESS para saber quais packs estao habilitados.
    ASH/AWR/ADDM exigem Diagnostics Pack; SQL Monitor exige Tuning Pack. Em caso
    de duvida assume NONE (evita consumir licenca por engano)."""
    try:
        cur.execute("SELECT value FROM v$parameter WHERE name = 'control_management_pack_access'")
        row = cur.fetchone()
        val = ((row[0] if row else "") or "").upper()
    except Exception:
        val = ""
    if "TUNING" in val:
        return _PACK_LEVEL["T"], val
    if "DIAGNOSTIC" in val:
        return _PACK_LEVEL["D"], val
    if val == "NONE":
        return _PACK_LEVEL["N"], "NONE"
    return _PACK_LEVEL["N"], "desconhecido -> NONE"


def diario(case_dir: str, tee=print):
    """Callable de log que ecoa na tela E grava em <caso>/coleta.log.

    Por que nao basta o `print`: a saida do coletor e o unico registro de o que
    foi executado, quanto demorou e o que o banco recusou - e ela morre quando o
    terminal fecha. Depois, com o caso ja reaberto num chamado, ninguem consegue
    responder "esse check falhou desde o inicio ou passou a falhar hoje?".
    Gravar ao lado da evidencia resolve, com carimbo de hora em cada linha.

    Anexa (nunca sobrescreve): recoletar o mesmo caso acrescenta ao historico,
    igual ao resto do framework. Falha de escrita nao derruba a coleta - perder
    o diario e ruim, perder a coleta contra producao e pior.
    """
    caminho = os.path.join(case_dir, "coleta.log")

    def _log(msg):
        tee(msg)
        try:
            with open(caminho, "a", encoding="utf-8") as fh:
                fh.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
        except Exception:
            pass

    return _log


def collect_direct(case: str, dsn: str, user: str, password: str,
                   pack: str = "auto", log=print, workers: int = 4,
                   fast: bool = False, timeout: int = 0,
                   advisor: bool = False,
                   amostragem_segundos: float = 0.0) -> list[str]:
    """Conecta read-only e grava as evidencias em troubleshooting/<caso>/.
    Retorna a lista de arquivos gerados. Requer python-oracledb.

    pack: 'auto' (detecta CONTROL_MANAGEMENT_PACK_ACCESS) | 'T' (Tuning+Diag) |
    'D' (Diagnostics) | 'N' (nenhum - so v$/dicionario, sem consumir licenca).
    workers: quantas conexoes read-only paralelas usar (1 = sequencial).
    fast: pula os checks de historico do AWR (DBA_HIST_*), que sao os mais lentos.
    timeout: limite em segundos por query (0 = sem limite).
    advisor: roda o SQL Tuning Advisor nos SQL mais regredidos. NAO e read-only
    (cria/executa tuning tasks) e consome Tuning Pack - use com opt-in explicito.
    amostragem_segundos: janela de amostragem de gv$session (0 = padrao de
    AMOSTRAGEM_SEGUNDOS). Janela maior da mais amostras e, com elas, resolucao
    estatistica: 15s a cada 0,5s sao 30 amostras, e com 30 amostras cada uma
    vale 3,3 pontos percentuais do perfil - um evento visto uma vez nao se
    distingue de ruido. Nao mexemos no padrao para nao alongar a coleta de
    rotina; quem esta investigando um sintoma usa a janela maior."""
    try:
        import oracledb
    except ImportError as e:
        raise RuntimeError(
            "python-oracledb nao instalado. Rode:\n"
            "  .\\.venv\\Scripts\\python.exe -m pip install oracledb") from e

    case_dir = os.path.join(CASES_DIR, case)
    os.makedirs(case_dir, exist_ok=True)

    # O diario e ligado aqui, e nao no COLETAR.py, para que TODO caminho de
    # coleta (CLI, painel, bateria de homologacao) deixe o mesmo rastro - rastro
    # de auditoria que depende do chamador lembrar de pedir nao e rastro.
    registrar = diario(case_dir, tee=log)
    lock = threading.Lock()

    def slog(msg):
        with lock:
            registrar(msg)

    def _new_conn(read_only: bool = True):
        """Abre uma conexao ja tagueada e com timeout opcional.
        read_only=False APENAS para o SQL Tuning Advisor (opt-in), que precisa
        criar/executar tuning tasks - o resto do framework nunca escreve."""
        c = oracledb.connect(user=user, password=password, dsn=dsn)
        c.autocommit = False
        if timeout:
            c.call_timeout = int(timeout) * 1000
        cr = c.cursor()
        try:
            cr.callproc("dbms_application_info.set_module",
                        ["TROUBLESHOOT-RO" if read_only else "TROUBLESHOOT-ADVISOR",
                         (case or "")[:48]])
        except Exception:
            pass
        if read_only:
            cr.execute("SET TRANSACTION READ ONLY")  # garante nenhuma escrita
        return c, cr

    t_start = time.time()
    slog(f"[conn] conectando {user}@{dsn} (thin, read-only)...")
    conn, cur = _new_conn()

    # Conexoes por thread (uma sessao read-only por worker)
    local = threading.local()
    extra_conns = []

    def _get_cur():
        cr = getattr(local, "cur", None)
        if cr is None:
            c2, cr = _new_conn()
            local.cur = cr
            with lock:
                extra_conns.append(c2)
        return cr

    def _run_group(group):
        fname, optional, blocks = group
        t0 = time.time()
        cr = _get_cur()
        parts = []
        for subtitle, sql in blocks:
            if not validate_select(sql):
                slog(f"[skip] {fname}: query bloqueada (nao-SELECT)")
                continue
            try:
                headers, rows = _fetch_table(cr, sql)
                parts.append(f"=== {subtitle} ===\n" + render_table(headers, rows))
            except Exception as ex:
                msg = str(ex).splitlines()[0]
                if optional:
                    slog(f"[skip] {fname} ({subtitle}): {msg}")
                else:
                    parts.append(f"=== {subtitle} ===\n-- ERRO: {msg}")
                    slog(f"[warn] {fname} ({subtitle}): {msg}")
        el = time.time() - t0
        if not parts:
            _descartar_obsoleto(case_dir, fname, slog)
            return None, fname, el
        with open(os.path.join(case_dir, fname), "w", encoding="utf-8") as f:
            f.write("\n\n".join(parts) + "\n")
        slog(f"[ok] {fname} ({el:.1f}s)")
        return fname, fname, el

    def _run_special(name, fn):
        t0 = time.time()
        try:
            fn(_get_cur(), case_dir, slog)
            return name, name, time.time() - t0
        except Exception as ex:
            slog(f"[skip] {name}: {str(ex).splitlines()[0]}")
            _descartar_obsoleto(case_dir, name, slog)
            return None, name, time.time() - t0

    def _run_multi(name, fn, read_only: bool = True):
        """Como _run_special, mas o coletor devolve varios arquivos.
        read_only=False abre uma conexao dedicada que permite escrita (advisor)."""
        t0 = time.time()
        c_rw = None
        try:
            if read_only:
                cr = _get_cur()
            else:
                c_rw, cr = _new_conn(read_only=False)
            files = fn(cr, case_dir, slog) or []
            return list(files), name, time.time() - t0
        except Exception as ex:
            slog(f"[skip] {name}: {str(ex).splitlines()[0]}")
            return None, name, time.time() - t0
        finally:
            if c_rw is not None:
                try:
                    c_rw.close()
                except Exception:
                    pass

    written = []
    timings = []
    try:
        # Licenca/pack: define quais checks podem rodar (compliance)
        pk = (pack or "auto").strip().lower()
        if pk in ("t", "tuning"):
            allowed, src = _PACK_LEVEL["T"], "forcado (--pack T)"
        elif pk in ("d", "diag", "diagnostic"):
            allowed, src = _PACK_LEVEL["D"], "forcado (--pack D)"
        elif pk in ("n", "none"):
            allowed, src = _PACK_LEVEL["N"], "forcado (--pack N)"
        else:
            allowed, src = _detect_packs(cur)
        extras = "+".join(sorted(allowed - {"NONE"})) or "NONE (apenas v$/dicionario)"
        slog(f"[pack] licenca considerada: {extras}  [{src}]")

        # FASE 1: topologia. Precisa vir ANTES da montagem da coleta - e ela que
        # decide quais checks fazem sentido aqui (RAC/Exadata/GG/DG) e como os
        # analisadores devem interpretar os numeros.
        topo = {}
        try:
            from .topologia import detect_topology, render_topologia
            topo = detect_topology(cur)
            with open(os.path.join(case_dir, "topology.txt"), "w", encoding="utf-8") as f:
                f.write(render_topologia(topo))
            written.append("topology.txt")
            # Versao estruturada: o TROUBLESHOOT.py carrega em meta['topologia'],
            # e assim TODO analisador recebe a topologia e pode ajustar limiares
            # (ex.: IO no Exadata) sem reparsear texto.
            with open(os.path.join(case_dir, "topology.json"), "w", encoding="utf-8") as f:
                json.dump(topo, f, ensure_ascii=False, indent=2, default=str)
            slog(f"[topo] {topo.get('perfil')}")
            if topo.get("kind_incerto"):
                slog("[topo] cluster com 1 no no ar: RAC One Node normal ou RAC "
                     "degradado - confirme com 'srvctl config database -d <db>'")
        except Exception as ex:
            slog(f"[topo] deteccao falhou ({str(ex).splitlines()[0]}) - "
                 "coletando TODOS os checks (fail-open)")
            topo = {}

        groups = _build_collect(allowed, topo)
        if topo.get("tags"):
            todos = {g[0] for g in _build_collect(allowed, None)}
            fora = sorted(todos - {g[0] for g in groups})
            if fora:
                slog(f"[topo] {len(fora)} check(s) fora desta topologia: "
                     + ", ".join(fora))
        if fast:
            heavy = [g[0] for g in groups if _is_heavy(g[2])]
            groups = [g for g in groups if not _is_heavy(g[2])]
            if heavy:
                slog(f"[rapido] {len(heavy)} check(s) de historico AWR pulado(s): "
                     + ", ".join(heavy))

        tasks = [(lambda g=g: _run_group(g)) for g in groups]
        if "TUNING" in allowed:
            tasks.append(lambda: _run_special("sqlmon.txt", _collect_sqlmon))
        else:
            slog("[pack] sqlmon.txt pulado (requer Tuning Pack)")
        # Sem Diagnostics Pack nao ha ASH - e sem perfil de esperas o
        # diagnostico vira chute. A amostragem de gv$session cobre essa lacuna
        # de forma legal (nenhum pack) e e o unico check que leva tempo de
        # proposito: precisa de janela para amostrar.
        if "DIAGNOSTIC" not in allowed and not fast:
            janela = float(amostragem_segundos or AMOSTRAGEM_SEGUNDOS)
            tasks.append(lambda j=janela: _run_special(
                "amostragem.txt",
                lambda cur, cd, lg: _collect_amostragem(cur, cd, lg,
                                                        segundos=j)))
        elif "DIAGNOSTIC" not in allowed:
            slog("[rapido] amostragem.txt pulada (precisa de janela de amostragem)")
        if "DIAGNOSTIC" in allowed and not fast:
            tasks.append(lambda: _run_special("addm.txt", _collect_addm))
        elif fast:
            slog("[rapido] addm.txt pulado (relatorio ADDM e lento)")
        else:
            slog("[pack] addm.txt pulado (requer Diagnostics Pack)")

        # SQL Health Check de regressao (read-only, precisa do historico AWR)
        if "DIAGNOSTIC" in allowed and not fast:
            from .sqlhc import collect_regressions
            tasks.append(lambda: _run_multi("regressions.txt", collect_regressions))
        elif fast:
            slog("[rapido] regressions.txt pulado (usa historico AWR)")

        # SQL Tuning Advisor: OPT-IN, escreve no dicionario e consome Tuning Pack.
        # Fica FORA do pool e roda depois (ver abaixo).
        advisor_task = None
        if advisor:
            if "TUNING" not in allowed:
                slog("[pack] --advisor ignorado: requer Tuning Pack")
            else:
                slog("[ATENCAO] --advisor: o SQL Tuning Advisor ESCREVE no "
                     "dicionario (create/execute tuning task) e consome Tuning Pack")
                from .sqlhc import collect_advisor
                advisor_task = lambda: _run_multi("advisor.txt", collect_advisor,
                                                  read_only=False)

        n = max(1, int(workers or 1))
        slog(f"[exec] {len(tasks)} check(s) | {n} conexao(oes) paralela(s)")
        if n > 1 and len(tasks) > 1:
            with ThreadPoolExecutor(max_workers=n) as pool:
                results = list(pool.map(lambda fn: fn(), tasks))
        else:
            results = [fn() for fn in tasks]

        for ok, name, el in results:
            timings.append((el, name))
            if isinstance(ok, list):
                written.extend(ok)
            elif ok:
                written.append(ok)

        # Advisor por ultimo e sequencial, por dois motivos: ele ANEXA ao
        # acoes.sql que o collect_regressions reescreve do zero (em paralelo um
        # truncaria o do outro) e e o check mais demorado - nao deve prender um
        # worker enquanto os checks rapidos esperam.
        if advisor_task is not None:
            slog("[advisor] iniciando apos os checks read-only (pode demorar)")
            ok, name, el = advisor_task()
            timings.append((el, name))
            if isinstance(ok, list):
                written.extend(ok)
            elif ok:
                written.append(ok)
    finally:
        for c in extra_conns:
            try:
                c.close()
            except Exception:
                pass
        try:
            conn.close()
        except Exception:
            pass

    total = time.time() - t_start
    slow = sorted(timings, reverse=True)[:3]
    slog(f"[tempo] coleta em {total:.1f}s | mais lentos: "
         + ", ".join(f"{n} {e:.1f}s" for e, n in slow if e >= 0.5))
    return written


# ── Amostragem de sessoes (ASH sem Diagnostics Pack) ──────────
# Sem o Diagnostics Pack o framework ficava CEGO no que mais importa: onde o
# tempo esta indo. A tecnica (consagrada pelo Snapper do Tanel Poder e pelo
# YaASHs) e amostrar gv$session em alta frequencia: ASH e, no fundo, um
# historico de v$session. gv$session NAO exige pack nenhum.
AMOSTRAGEM_SEGUNDOS = 15.0
AMOSTRAGEM_INTERVALO = 0.5

# `state` decide: WAITING em classe nao-Idle e espera de verdade; o resto da
# sessao ativa esta em CPU (o `event` de quem nao espera e o ULTIMO evento, e
# usa-lo aqui seria mentira estatistica).
_SQL_AMOSTRA = (
    "SELECT inst_id, "
    "CASE WHEN state = 'WAITING' AND wait_class <> 'Idle' "
    "THEN wait_class ELSE 'CPU' END AS wait_class, "
    "CASE WHEN state = 'WAITING' AND wait_class <> 'Idle' "
    "THEN event ELSE 'ON CPU' END AS event, "
    "sql_id, username, blocking_session "
    "FROM gv$session "
    "WHERE status = 'ACTIVE' AND type = 'USER' "
    "AND (state <> 'WAITING' OR wait_class <> 'Idle') "
    "AND audsid <> USERENV('SESSIONID')"
)


def _collect_amostragem(cur, case_dir, log, segundos: float = AMOSTRAGEM_SEGUNDOS,
                        intervalo: float = AMOSTRAGEM_INTERVALO):
    """Amostra gv$session e monta um perfil de esperas estilo ASH.

    Read-only puro (um SELECT repetido) e sem licenca de pack. A propria sessao
    de coleta e excluida para nao contaminar a amostra.
    """
    if not validate_select(_SQL_AMOSTRA):      # cinto e suspensorio
        raise RuntimeError("query de amostragem bloqueada")

    perfil, por_sql = Counter(), Counter()
    n_amostras, ativos_total, ativos_max, bloqueadas = 0, 0, 0, 0
    fim = time.time() + max(1.0, float(segundos))
    while time.time() < fim:
        cur.execute(_SQL_AMOSTRA)
        linhas = cur.fetchall()
        n_amostras += 1
        ativos_total += len(linhas)
        ativos_max = max(ativos_max, len(linhas))
        for _inst, wc, ev, sql_id, user, blk in linhas:
            perfil[(wc or "CPU", ev or "ON CPU")] += 1
            if sql_id:
                por_sql[(sql_id, user or "?")] += 1
            if blk is not None:
                bloqueadas += 1
        time.sleep(max(0.05, float(intervalo)))

    if not n_amostras:
        raise RuntimeError("nenhuma amostra coletada")

    total_samples = sum(perfil.values())
    aas_medio = ativos_total / n_amostras

    # RESOLUCAO_PP: quanto UMA amostra vale no perfil, em pontos percentuais.
    # E a leitura de precisao que nao depende de nenhuma hipotese estatistica:
    # com 30 amostras, cada uma vale 3,3 pp, e um evento visto uma unica vez
    # aparece como "3%" sem se distinguir de ruido. O relatorio mostrava o
    # percentual com a mesma cara de certeza de um ASH de 7 dias.
    resolucao_pp = 100.0 / n_amostras
    cab = ["AMOSTRAS", "SEGUNDOS", "INTERVALO_S", "RESOLUCAO_PP",
           "AAS_MEDIO", "AAS_MAX", "AMOSTRAS_BLOQUEADAS"]
    resumo = [[str(n_amostras), f"{segundos:.0f}", f"{intervalo:.2f}",
               f"{resolucao_pp:.1f}", f"{aas_medio:.2f}",
               str(ativos_max), str(bloqueadas)]]

    linhas_perfil = [
        [wc, ev[:45], str(qtd), f"{qtd / n_amostras:.2f}",
         f"{qtd / total_samples * 100:.1f}"]
        for (wc, ev), qtd in perfil.most_common(20)] or [["-", "sem sessao ativa", "0", "0.00", "0.0"]]

    linhas_sql = [
        [sid, user[:20], str(qtd), f"{qtd / n_amostras:.2f}",
         f"{qtd / total_samples * 100:.1f}"]
        for (sid, user), qtd in por_sql.most_common(15)]

    partes = [
        "=== AMOSTRAGEM DE SESSOES (SEM DIAGNOSTICS PACK) ===\n"
        + render_table(cab, resumo),
        "=== PERFIL DE ESPERAS (AMOSTRAGEM) ===\n"
        + render_table(["WAIT_CLASS", "EVENT", "AMOSTRAS", "AAS", "PCT"],
                       linhas_perfil),
    ]
    if linhas_sql:
        partes.append("=== TOP SQL POR AMOSTRAS ===\n"
                      + render_table(["SQL_ID", "USERNAME", "AMOSTRAS", "AAS",
                                      "PCT"], linhas_sql))

    with open(os.path.join(case_dir, "amostragem.txt"), "w", encoding="utf-8") as f:
        f.write("\n\n".join(partes) + "\n")
    log(f"[ok] amostragem.txt ({n_amostras} amostras em {segundos:.0f}s, "
        f"resolucao {resolucao_pp:.1f}pp, AAS medio {aas_medio:.2f})")


def _collect_sqlmon(cur, case_dir, log):
    # 1) preferir um SQL realmente monitorado (gv$sql_monitor) com maior elapsed
    sql_id = None
    try:
        cur.execute(
            "SELECT sql_id FROM (SELECT sql_id, MAX(elapsed_time) et "
            "FROM gv$sql_monitor WHERE sql_id IS NOT NULL "
            "GROUP BY sql_id ORDER BY et DESC) WHERE ROWNUM = 1")
        row = cur.fetchone()
        if row:
            sql_id = row[0]
    except Exception:
        sql_id = None
    # 2) fallback: SQL mais ativo no ASH
    if not sql_id:
        cur.execute(
            "SELECT sql_id FROM (SELECT sql_id, COUNT(*) c FROM gv$active_session_history "
            "WHERE sample_time > SYSDATE - 1/48 AND sql_id IS NOT NULL "
            "GROUP BY sql_id ORDER BY c DESC) WHERE ROWNUM = 1")
        row = cur.fetchone()
        if not row:
            raise RuntimeError("sem sql_id ativo")
        sql_id = row[0]
    cur.execute(
        "SELECT DBMS_SQLTUNE.REPORT_SQL_MONITOR(sql_id => :1, type => 'TEXT', "
        "report_level => 'ALL') FROM dual", [sql_id])
    lob = cur.fetchone()[0]
    txt = lob.read() if hasattr(lob, "read") else str(lob)
    if txt and "SQL Monitoring Report" in txt:
        with open(os.path.join(case_dir, "sqlmon.txt"), "w", encoding="utf-8") as f:
            f.write(txt)
        log(f"[ok] sqlmon.txt (sql_id={sql_id})")
    else:
        raise RuntimeError("sem relatorio de SQL Monitor")


def _collect_addm(cur, case_dir, log):
    cur.execute(
        "SELECT task_name FROM (SELECT task_name FROM dba_advisor_tasks "
        "WHERE advisor_name = 'ADDM' ORDER BY created DESC) WHERE ROWNUM = 1")
    row = cur.fetchone()
    if not row:
        raise RuntimeError("sem task ADDM")
    task = row[0]
    cur.execute("SELECT DBMS_ADVISOR.GET_TASK_REPORT(:1) FROM dual", [task])
    lob = cur.fetchone()[0]
    txt = lob.read() if hasattr(lob, "read") else str(lob)
    if txt:
        with open(os.path.join(case_dir, "addm.txt"), "w", encoding="utf-8") as f:
            f.write(txt)
        log(f"[ok] addm.txt (task={task})")
    else:
        raise RuntimeError("relatorio ADDM vazio")
