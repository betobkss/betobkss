"""
SQL Health Check de regressao (estilo SQLHC / sqltrpt.sql da Oracle).

Para os SQL_IDs que MAIS REGREDIRAM (ranqueados por impacto absoluto em
segundos, nao por percentual - Method R), coleta em modo READ-ONLY:

1. Top N regressoes  : elapsed/exec base x recente, execucoes, impacto total,
                       e se houve troca de plano (PLANO) ou so piora (TEMPO).
2. Objetos usados    : tabelas lidas pelo SQL (inclusive as donas dos indices
                       do plano) com num_rows, last_analyzed e stale_stats.
3. Operacoes de risco: TABLE ACCESS FULL, MERGE JOIN CARTESIAN, INDEX FULL SCAN
                       e estimativas de cardinalidade suspeitas.
4. Planos            : DBMS_XPLAN.DISPLAY_AWR do plano BASE e do RECENTE, lado
                       a lado, para comparar a regressao.
5. acoes.sql         : script PRONTO para rodar - DBMS_STATS por objeto e
                       indices candidatos (comentados, exigem validacao).

O SQL Tuning Advisor fica em `collect_advisor` e NAO roda por padrao: ele
ESCREVE no dicionario (CREATE/EXECUTE_TUNING_TASK) e consome Tuning Pack.

Referencias de mercado:
- Oracle SQLHC (SQL Health Check) - Doc ID 1366133.1
- Oracle $ORACLE_HOME/rdbms/admin/sqltrpt.sql
- Carlos Sierra - SQLT / eDB360
- Cary Millsap - Method R (priorizar por tempo de resposta, nao por ratio)
"""
from __future__ import annotations

import os
import re

TOP_N = 10

# Schemas mantidos pela Oracle: o SQL deles e recursivo/manutencao (AWR, tuning
# set, jobs). Nao se tuna SQL interno nem se cria indice/coleta stats em SYS.
ORACLE_SCHEMAS = (
    "'SYS','SYSTEM','DBSNMP','SYSMAN','XDB','WMSYS','CTXSYS','MDSYS','ORDSYS',"
    "'OUTLN','APPQOSSYS','GSMADMIN_INTERNAL','AUDSYS','DVSYS','LBACSYS',"
    "'OJVMSYS','ORACLE_OCM','DBSFWUSER','REMOTE_SCHEDULER_AGENT','ORDDATA',"
    "'OLAPSYS','APEX_040000','FLOWS_FILES','SI_INFORMTN_SCHEMA','ANONYMOUS'"
)

# ── 1. Top N regressoes por IMPACTO ABSOLUTO (segundos ganhos/perdidos) ──
# Pisos de materialidade iguais ao check 'compare_period' (evita +400% de 5ms).
# Filtros que tornam o resultado ACIONAVEL:
#   - plano_rec <> 0 : PHV 0 e bloco PL/SQL - nao tem plano, objeto nem indice
#                      para sugerir (o compare_period ja cobre esses casos).
#   - parsing_schema: exclui SQL interno da Oracle (SYS/AWR/tuning set).
SQL_TOP_REGRESSIONS = f"""
SELECT * FROM (
  SELECT sql_id, schema, plano_base, plano_rec, execs_rec, avg_s_base, avg_s_rec,
         ROUND((avg_s_rec - avg_s_base) * execs_rec, 1) AS impacto_s,
         ROUND((avg_s_rec - avg_s_base) / NULLIF(avg_s_base,0) * 100) AS var_pct,
         CASE WHEN plano_mudou = 1 THEN 'PLANO' ELSE 'TEMPO' END AS tipo
    FROM (
      SELECT sql_id, schema, plano_base, plano_rec, execs_rec, avg_s_base, avg_s_rec,
             CASE WHEN NVL(plano_base,0) <> 0 AND NVL(plano_rec,0) <> 0
                       AND plano_base <> plano_rec THEN 1 ELSE 0 END AS plano_mudou
        FROM (
          SELECT sql_id,
                 MAX(schema) AS schema,
                 MAX(CASE WHEN periodo='BASE' THEN phv END) AS plano_base,
                 MAX(CASE WHEN periodo='REC'  THEN phv END) AS plano_rec,
                 SUM(CASE WHEN periodo='REC'  THEN execs END) AS execs_rec,
                 ROUND(SUM(CASE WHEN periodo='BASE' THEN elapsed END)/1000000
                       /NULLIF(SUM(CASE WHEN periodo='BASE' THEN execs END),0),3) AS avg_s_base,
                 ROUND(SUM(CASE WHEN periodo='REC' THEN elapsed END)/1000000
                       /NULLIF(SUM(CASE WHEN periodo='REC' THEN execs END),0),3) AS avg_s_rec
            FROM (SELECT ss.sql_id, ss.plan_hash_value AS phv,
                         ss.parsing_schema_name AS schema,
                         CASE WHEN sn.begin_interval_time > SYSDATE-1
                              THEN 'REC' ELSE 'BASE' END AS periodo,
                         ss.executions_delta AS execs,
                         ss.elapsed_time_delta AS elapsed
                    FROM dba_hist_sqlstat ss
                    JOIN dba_hist_snapshot sn
                      ON ss.snap_id = sn.snap_id
                     AND ss.instance_number = sn.instance_number
                   WHERE sn.begin_interval_time > SYSDATE-7
                     AND ss.executions_delta > 0
                     AND ss.parsing_schema_name NOT IN ({ORACLE_SCHEMAS}))
           GROUP BY sql_id
          HAVING SUM(CASE WHEN periodo='REC'  THEN execs END) > 0
             AND SUM(CASE WHEN periodo='BASE' THEN execs END) > 0
        )
    )
   WHERE avg_s_rec >= 0.05
     AND avg_s_rec - avg_s_base >= 0.05
     AND NVL(plano_rec,0) <> 0
   ORDER BY (avg_s_rec - avg_s_base) * execs_rec DESC
) WHERE ROWNUM <= :top_n
"""

# ── 2. Tabelas usadas pelo SQL (normaliza indice -> tabela dona) + stats ──
SQL_OBJETOS = """
SELECT t.owner, t.tabela, ts.num_rows,
       TO_CHAR(ts.last_analyzed,'YYYY-MM-DD HH24:MI') AS last_analyzed,
       NVL(ts.stale_stats,'?') AS stale_stats,
       CASE WHEN ts.last_analyzed IS NULL THEN NULL
            ELSE TRUNC(SYSDATE - ts.last_analyzed) END AS dias_sem_stats
  FROM (SELECT DISTINCT p.object_owner AS owner, p.object_name AS tabela
          FROM dba_hist_sql_plan p
         WHERE p.sql_id = :sql_id
           AND p.object_owner IS NOT NULL
           AND p.object_type LIKE 'TABLE%'
        UNION
        SELECT DISTINCT i.table_owner, i.table_name
          FROM dba_hist_sql_plan p
          JOIN dba_indexes i ON i.owner = p.object_owner
                            AND i.index_name = p.object_name
         WHERE p.sql_id = :sql_id
           AND p.object_type LIKE 'INDEX%') t
  LEFT JOIN dba_tab_statistics ts
         ON ts.owner = t.owner AND ts.table_name = t.tabela
        AND ts.object_type = 'TABLE'
 WHERE t.owner NOT IN ('SYS','SYSTEM','XDB','MDSYS','CTXSYS','DBSNMP','WMSYS')
 ORDER BY ts.num_rows DESC NULLS LAST
"""

# ── 3. Operacoes de risco no plano recente ──
SQL_RISCOS = """
SELECT p.id, p.operation || ' ' || NVL(p.options,'') AS operacao,
       p.object_owner || '.' || p.object_name AS objeto,
       p.cost, p.cardinality AS card_estimada,
       SUBSTR(NVL(p.filter_predicates, p.access_predicates), 1, 120) AS predicado
  FROM dba_hist_sql_plan p
 WHERE p.sql_id = :sql_id
   AND p.plan_hash_value = :phv
   AND ((p.operation = 'TABLE ACCESS' AND p.options = 'FULL')
     OR (p.operation = 'MERGE JOIN'   AND p.options = 'CARTESIAN')
     OR (p.operation = 'INDEX'        AND p.options LIKE 'FULL SCAN%'))
 ORDER BY p.cost DESC NULLS LAST, p.id
"""

# ── 4. Linha do tempo de PLANOS de um SQL (30 dias) ──────────────────────
# O que existia era a comparacao de DOIS planos: o da janela base e o da
# recente. Isso responde "mudou?" e nao responde as duas perguntas que decidem
# a acao:
#
#   - QUANDO mudou? A data e o que se cruza com deploy, coleta de estatistica
#     e mudanca de parametro. Sem ela, "o plano mudou nos ultimos 7 dias" manda
#     procurar em sete dias de historico de mudanca.
#   - EXISTE um plano melhor AINDA NO AWR? Essa e a pergunta acionavel. Se um
#     plano antigo era 8x mais rapido e ainda esta no repositorio, a correcao e
#     fixa-lo com DBMS_SPM.LOAD_PLANS_FROM_AWR - minutos, sem tocar a
#     aplicacao. Sem a linha do tempo, a recomendacao possivel era a genérica
#     "compare os planos e avalie um baseline".
#
# Mesmas views que o resto do SQLHC ja usa (dba_hist_sqlstat + snapshot), entao
# nao amplia a superficie de privilegio nem de licenca.
SQL_PLAN_TIMELINE = """
SELECT ss.plan_hash_value AS plano,
       TO_CHAR(MIN(sn.begin_interval_time),'YYYY-MM-DD') AS desde,
       TO_CHAR(MAX(sn.begin_interval_time),'YYYY-MM-DD') AS ate,
       SUM(ss.executions_delta) AS execs,
       ROUND(SUM(ss.elapsed_time_delta)/1000000
             /GREATEST(SUM(ss.executions_delta),1),3) AS avg_s,
       ROUND(SUM(ss.elapsed_time_delta)/1000000,1) AS total_s
  FROM dba_hist_sqlstat ss
  JOIN dba_hist_snapshot sn
    ON ss.snap_id = sn.snap_id
   AND ss.instance_number = sn.instance_number
 WHERE ss.sql_id = :sql_id
   AND sn.begin_interval_time > SYSDATE - 30
   AND ss.executions_delta > 0
   AND NVL(ss.plan_hash_value,0) <> 0
 GROUP BY ss.plan_hash_value
 ORDER BY MIN(sn.begin_interval_time)
"""

_IDENT = re.compile(r"[A-Z][A-Z0-9_$#]*", re.I)
_SQLID = re.compile(r"^[0-9a-z]{13}$")


def _fetch(cur, sql, params=None):
    cur.execute(sql, params or {})
    headers = [d[0] for d in cur.description]
    rows = []
    for row in cur.fetchall():
        cells = []
        for v in row:
            if hasattr(v, "read"):
                try:
                    v = v.read()
                except Exception:
                    v = "<LOB>"
            cells.append(v)
        rows.append(cells)
    return headers, rows


# Construcoes que impedem o uso de indice B-tree comum sobre a coluna:
# CASE/DECODE e funcao envolvem a coluna (exigiria indice baseado em funcao);
# NVL(:B,col) e o classico predicado "opcional" que nunca fica sargavel;
# NOT/IS NULL nao restringem por range no indice;
# OR obriga expansao em ramos (um indice unico nao atende a disjuncao).
_NAO_SARGAVEL = re.compile(
    r"\bCASE\b|\bDECODE\s*\(|\bNVL\s*\(|\bNOT\b|\bIS\s+NULL\b|\bOR\b", re.I)
_FUNC_ANTES = re.compile(r"[A-Z0-9_$#]+\s*\(\s*$", re.I)
_OP_PRED = re.compile(r'"([A-Z0-9_$#]+)"\s*(<=|>=|<>|!=|\^=|=|<|>|LIKE|BETWEEN|IN)', re.I)
_OP_SARGAVEL = {"=", ">", ">=", "<", "<=", "BETWEEN", "IN"}


def _cols_do_predicado(pred: str) -> list[str]:
    """Extrai as colunas INDEXAVEIS de um predicado do plano (heuristica).

    Deliberadamente conservador: sugerir indice errado custa espaco, tempo de
    manutencao e pode piorar DML. So aceita comparacao direta da coluna
    (=, >, >=, <, <=, IN, BETWEEN) fora de CASE/DECODE/NVL/funcao.
    Ex.: "T"."STATUS"=:B1 AND "T"."DT_MOV">=... -> ['STATUS','DT_MOV']
         "R"."TIP_DOCUMENTO"<>'X' dentro de CASE      -> []
         "A"."CD"=NVL(:B2,"A"."CD")                   -> []
    """
    if not pred or _NAO_SARGAVEL.search(pred):
        return []
    cols, vetadas = [], set()
    # colunas vem entre aspas duplas no predicado do plano
    for m in _OP_PRED.finditer(pred):
        c = m.group(1).upper()
        if m.group(2).upper() not in _OP_SARGAVEL:
            vetadas.add(c)                # <>, !=, ^=, LIKE (pode ser '%x')
            continue
        if _FUNC_ANTES.search(pred[:m.start()]):
            vetadas.add(c)                # TRUNC("T"."DT")= -> indice funcional
            continue
        if c not in cols and not c.startswith("SYS_"):
            cols.append(c)
    # a mesma coluna usada tambem de forma nao sargavel nao e candidata
    cols = [c for c in cols if c not in vetadas]
    return cols[:4]          # indice composto util raramente passa de 4 colunas


def _safe_ident(name: str) -> bool:
    return bool(name) and bool(_IDENT.fullmatch(str(name)))


def _col(row, idx: dict, nome: str):
    """Celula da linha pelo NOME da coluna, ou None se ela nao veio.

    Coluna ausente e rotina: a consulta muda conforme a versao do Oracle e o
    que o dicionario expoe. Indexar direto estouraria IndexError no meio da
    coleta e derrubaria o caso por causa de uma coluna.

    Existe como funcao de modulo, e nao como `def g(col)` dentro do laco,
    porque aquela forma prende a variavel do laco na closure (B023): hoje e
    inofensivo, ja que o valor e consumido na mesma iteracao, mas basta alguem
    guardar o `g` numa lista para todas as chamadas passarem a ler a ULTIMA
    linha.
    """
    i = idx.get(nome, -1)
    return row[i] if 0 <= i < len(row) else None


def collect_regressions(cur, case_dir, log, top_n: int = TOP_N) -> list[str]:
    """Coleta READ-ONLY do SQL Health Check de regressao.
    Retorna a lista de arquivos gravados."""
    from .collector import render_table

    # Limpa saidas de uma coleta anterior: evidencia obsoleta no disco levaria o
    # analisador a reportar uma regressao que ja nao existe.
    for antigo in ("regressions.txt", "planos.txt", "acoes.sql"):
        try:
            os.remove(os.path.join(case_dir, antigo))
        except FileNotFoundError:
            pass

    headers, rows = _fetch(cur, SQL_TOP_REGRESSIONS, {"top_n": top_n})
    if not rows:
        # Nenhuma regressao material = BOA NOTICIA. Registra explicitamente em
        # vez de nao gerar arquivo (o check rodou e passou).
        caminho = os.path.join(case_dir, "regressions.txt")
        with open(caminho, "w", encoding="utf-8") as f:
            f.write("=== TOP REGRESSOES POR IMPACTO (segundos) ===\n"
                    "-- Sem ocorrencias: nenhuma regressao material de SQL de\n"
                    "-- aplicacao nos ultimos 7 dias.\n"
                    "-- Criterios: >= 0.05s por execucao, piora >= 0.05s,\n"
                    "-- plano real (PHV <> 0) e schema nao mantido pela Oracle.\n")
        log("[ok] regressions.txt (sem regressoes materiais)")
        return ["regressions.txt"]

    idx = {h.upper(): i for i, h in enumerate(headers)}
    partes = ["=== TOP REGRESSOES POR IMPACTO (segundos) ===\n"
              "-- Ranqueado por impacto ABSOLUTO ((avg_rec-avg_base)*execucoes),\n"
              "-- nao por percentual. Exclui SQL interno da Oracle e PHV=0\n"
              "-- (bloco PL/SQL, sem plano/objeto para tunar - ver compare_period).\n"
              + render_table(headers, rows)]

    acoes = [
        "-- ============================================================",
        "-- acoes.sql - GERADO pelo SQL Health Check de regressao",
        "-- Ordem sugerida: (1) estatisticas, (2) revisar plano, (3) indice.",
        "-- REVISE antes de executar. Nada aqui foi executado pela coleta.",
        "-- ============================================================",
        "",
    ]
    riscos_partes = []
    objetos_partes = []
    planos_partes = []
    timeline_partes = []
    vistos_tab = set()
    vistos_idx = set()
    num_rows_por_tab = {}

    for r in rows:
        def g(col, _r=r):
            return _col(_r, idx, col)

        sql_id = str(g("SQL_ID") or "").strip()
        if not _SQLID.match(sql_id):
            continue
        phv_base, phv_rec = g("PLANO_BASE"), g("PLANO_REC")
        impacto, tipo = g("IMPACTO_S"), g("TIPO")

        acoes.append(f"-- ---------- {sql_id}  ({tipo}, impacto {impacto}s) ----------")
        n_acoes = len(acoes)

        # 2. objetos + stats
        try:
            oh, orows = _fetch(cur, SQL_OBJETOS, {"sql_id": sql_id})
        except Exception as ex:
            log(f"[skip] objetos de {sql_id}: {str(ex).splitlines()[0]}")
            oh, orows = [], []
        if orows:
            objetos_partes.append(f"--- {sql_id} ({tipo}, impacto {impacto}s) ---\n"
                                  + render_table(oh, orows))
            oi = {h.upper(): i for i, h in enumerate(oh)}
            for o in orows:
                owner = str(o[oi["OWNER"]]).strip()
                tab = str(o[oi["TABELA"]]).strip()
                if not (_safe_ident(owner) and _safe_ident(tab)):
                    continue
                stale = str(o[oi.get("STALE_STATS", -1)] or "").strip().upper()
                dias = o[oi.get("DIAS_SEM_STATS", -1)]
                nrows = o[oi.get("NUM_ROWS", -1)]
                if nrows is not None:
                    num_rows_por_tab[f"{owner}.{tab}"] = nrows
                if (owner, tab) in vistos_tab:
                    continue
                vistos_tab.add((owner, tab))
                motivo = ("stats STALE" if stale == "YES"
                          else f"{dias} dia(s) sem coleta" if dias and int(dias) > 7
                          else "sem estatisticas" if not dias and stale != "NO"
                          else None)
                prefixo = "" if motivo else "-- (stats ok) "
                acoes.append(
                    f"{prefixo}EXEC DBMS_STATS.GATHER_TABLE_STATS("
                    f"ownname=>'{owner}', tabname=>'{tab}', "
                    "estimate_percent=>DBMS_STATS.AUTO_SAMPLE_SIZE, "
                    "method_opt=>'FOR ALL COLUMNS SIZE AUTO', "
                    "cascade=>TRUE, degree=>DBMS_STATS.AUTO_DEGREE, "
                    f"no_invalidate=>FALSE);{'  -- ' + motivo if motivo else ''}")

        # 3. operacoes de risco no plano recente
        if phv_rec:
            try:
                rh, rrows = _fetch(cur, SQL_RISCOS,
                                   {"sql_id": sql_id, "phv": phv_rec})
            except Exception as ex:
                log(f"[skip] riscos de {sql_id}: {str(ex).splitlines()[0]}")
                rh, rrows = [], []
            if rrows:
                riscos_partes.append(f"--- {sql_id} (plano {phv_rec}) ---\n"
                                     + render_table(rh, rrows))
                ri = {h.upper(): i for i, h in enumerate(rh)}
                for rr in rrows:
                    op = str(rr[ri["OPERACAO"]]).strip()
                    obj = str(rr[ri["OBJETO"]]).strip()
                    pred = str(rr[ri.get("PREDICADO", -1)] or "")
                    if "TABLE ACCESS FULL" not in op.upper():
                        continue
                    if "." not in obj:
                        continue
                    cols = _cols_do_predicado(pred)
                    if not cols:
                        # Full scan sem predicado indexavel: dizer POR QUE nao ha
                        # indice a sugerir evita que o DBA crie um indice inutil.
                        if (obj, ()) not in vistos_idx:
                            vistos_idx.add((obj, ()))
                            acoes.append(
                                f"-- FULL SCAN em {obj} sem predicado indexavel "
                                "(CASE/NVL/<>/funcao ou varredura de particao).")
                            acoes.append(
                                "--   Indice nao resolve: avalie particionamento, "
                                "reescrita do predicado ou paralelismo.")
                        continue
                    chave = (obj, tuple(cols))
                    if chave in vistos_idx:
                        continue
                    vistos_idx.add(chave)
                    nrows = num_rows_por_tab.get(obj)
                    escala = f", {int(nrows):,} linhas".replace(",", ".") if nrows else ""
                    acoes.append(
                        f"-- CANDIDATO: full scan em {obj}{escala}")
                    acoes.append(
                        "--   ATENCAO: valide a seletividade das colunas antes de criar "
                        "(indice em coluna de baixa cardinalidade em tabela grande piora).")
                    acoes.append(
                        f"-- CREATE INDEX ix_{obj.split('.')[-1][:20].lower()}_"
                        f"{'_'.join(c[:6].lower() for c in cols)} ON {obj} "
                        f"({', '.join(cols)}) ONLINE;")

        # 4. linha do tempo dos planos (30d)
        # Falha aqui nao pode custar o resto do SQLHC: e informacao adicional,
        # e o `regressions.txt` com objetos, riscos e acoes vale por si.
        try:
            th, trows = _fetch(cur, SQL_PLAN_TIMELINE, {"sql_id": sql_id})
        except Exception as ex:
            log(f"[skip] linha do tempo de planos de {sql_id}: "
                f"{str(ex).splitlines()[0]}")
            th, trows = [], []
        if len(trows) > 1:
            # Um plano so nao e linha do tempo: nao houve troca em 30 dias, e
            # uma tabela de uma linha ocupa espaco sem responder nada.
            timeline_partes.append(f"--- {sql_id} ---\n"
                                   + render_table(th, trows))

        # 5. planos: BASE x RECENTE
        for rotulo, phv in (("BASE", phv_base), ("RECENTE", phv_rec)):
            if not phv:
                continue
            try:
                cur.execute(
                    "SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY_AWR(:sql_id, :phv, "
                    "NULL, 'TYPICAL'))", {"sql_id": sql_id, "phv": phv})
                linhas = [str(x[0]) for x in cur.fetchall()]
            except Exception as ex:
                log(f"[skip] plano {rotulo} de {sql_id}: {str(ex).splitlines()[0]}")
                continue
            if linhas:
                planos_partes.append(
                    f"--- {sql_id} | plano {rotulo} ({phv}) ---\n" + "\n".join(linhas))
        if len(acoes) == n_acoes:
            acoes.append("-- nada a sugerir automaticamente (estatisticas em dia e "
                         "nenhuma operacao indexavel no plano); revise o plano.")
        acoes.append("")

    if objetos_partes:
        partes.append("=== OBJETOS USADOS PELOS SQL REGREDIDOS (com estatisticas) ===\n"
                      + "\n\n".join(objetos_partes))
    if riscos_partes:
        partes.append("=== OPERACOES DE RISCO NO PLANO RECENTE ===\n"
                      + "\n\n".join(riscos_partes))

    escritos = []
    caminho = os.path.join(case_dir, "regressions.txt")
    with open(caminho, "w", encoding="utf-8") as f:
        f.write("\n\n".join(partes) + "\n")
    escritos.append("regressions.txt")
    log(f"[ok] regressions.txt ({len(rows)} SQL_ID regredido(s))")

    if timeline_partes:
        caminho = os.path.join(case_dir, "planos_timeline.txt")
        with open(caminho, "w", encoding="utf-8") as f:
            f.write("=== LINHA DO TEMPO DE PLANOS (30 DIAS) ===\n"
                    + "\n\n".join(timeline_partes) + "\n")
        escritos.append("planos_timeline.txt")
        log(f"[ok] planos_timeline.txt ({len(timeline_partes)} SQL com mais "
            "de um plano)")

    if planos_partes:
        with open(os.path.join(case_dir, "planos.txt"), "w", encoding="utf-8") as f:
            f.write("\n\n".join(planos_partes) + "\n")
        escritos.append("planos.txt")
        log(f"[ok] planos.txt ({len(planos_partes)} plano(s))")

    with open(os.path.join(case_dir, "acoes.sql"), "w", encoding="utf-8") as f:
        f.write("\n".join(acoes) + "\n")
    escritos.append("acoes.sql")
    log("[ok] acoes.sql (comandos prontos - nada foi executado)")
    return escritos


# ── SQL Tuning Advisor: OPT-IN (escreve no dicionario + Tuning Pack) ──
# Limiar igual ao IMPACTO_CRIT_S de analyzers/regression.py: se os dois
# divergirem, o relatorio marca um SQL como CRITICO e nao oferece o advisor
# (ou oferece o advisor num SQL que ele nem destacou).
ADVISOR_IMPACTO_MIN_S = 300.0
ADVISOR_MAX_SQLS = 5

# Linhas EXECUTAVEIS do REPORT_TUNING_TASK. O relatorio traz centenas de linhas
# de texto corrido que ninguem le ate o fim; o que o DBA precisa e do comando.
_ADV_ACAO = re.compile(
    r"^\s*(execute\s+dbms_sqltune\.accept_sql_profile\b.*"
    r"|create\s+(?:unique\s+|bitmap\s+)?index\s+.*"
    r"|alter\s+index\s+.*)", re.I)
_ADV_BENEFICIO = re.compile(r"estimated\s+benefit\s*:?\s*([\d.,]+)\s*%", re.I)
_ADV_PROFILE = re.compile(r"accept_sql_profile", re.I)


def _advisor_elegiveis(headers, rows, limite_s: float = ADVISOR_IMPACTO_MIN_S,
                       max_sqls: int = ADVISOR_MAX_SQLS) -> list[tuple]:
    """Filtra os SQL que justificam gastar Tuning Pack e tempo de execucao.

    Rodar no top N cego desperdicava licenca: numa regressao do tipo TEMPO
    (mesmo plano, mais tempo) a causa e crescimento de dados ou contencao, e o
    advisor responde "colete estatisticas" - que o acoes.sql ja gera de graca e
    read-only. O advisor rende onde houve TROCA DE PLANO (pode propor um SQL
    Profile que recupera o plano bom) ou onde o prejuizo sozinho ja paga o custo.
    """
    idx = {h.upper(): i for i, h in enumerate(headers)}
    out = []
    for r in rows:
        def g(col, _r=r):
            return _col(_r, idx, col)
        sql_id = str(g("SQL_ID") or "").strip()
        if not _SQLID.match(sql_id):
            continue
        tipo = str(g("TIPO") or "").strip().upper()
        try:
            imp = float(g("IMPACTO_S") or 0)
        except (TypeError, ValueError):
            imp = 0.0
        if tipo == "PLANO" or imp >= limite_s:
            out.append((sql_id, tipo, imp))
    # rows ja vem ordenado por impacto desc: o teto corta a cauda, nao o topo
    return out[:max_sqls]


def _extrai_acoes_advisor(texto: str) -> list[tuple[str, str]]:
    """Extrai (comando, beneficio estimado) do relatorio do advisor.

    O REPORT_TUNING_TASK quebra comandos longos em varias linhas, entao a
    leitura segue ate o ';' em vez de assumir que cabe numa linha so.
    """
    linhas = texto.splitlines()
    acoes = []
    beneficio = ""
    for i, ln in enumerate(linhas):
        mb = _ADV_BENEFICIO.search(ln)
        if mb:
            beneficio = mb.group(1) + "%"
            continue
        m = _ADV_ACAO.match(ln)
        if not m:
            continue
        cmd = m.group(1).strip()
        j = i + 1
        while ";" not in cmd and j < len(linhas) and j - i <= 20:
            cmd += " " + linhas[j].strip()
            j += 1
        cmd = " ".join(cmd.split())
        if cmd and all(cmd != c for c, _ in acoes):
            acoes.append((cmd, beneficio))
    return acoes


def _grava_acoes_advisor(case_dir, blocos, log) -> None:
    """Anexa as recomendacoes do advisor ao acoes.sql do caso.

    Anexa em vez de criar um segundo script para o DBA ter UM plano de acao, na
    ordem que o cabecalho do arquivo ja promete: (1) estatisticas, (2) plano,
    (3) indice. Tudo comentado - nada aqui roda sozinho.
    """
    if not blocos:
        return
    linhas = ["", "-- ============================================================",
              "-- SQL TUNING ADVISOR - recomendacoes (revise ANTES de executar)",
              "-- Origem: advisor.txt. O beneficio e a ESTIMATIVA do otimizador,",
              "-- medida no ambiente dele - valide em homologacao.",
              "-- ============================================================", ""]
    for sql_id, tipo, imp, acoes in blocos:
        linhas.append(f"-- ---------- {sql_id}  ({tipo}, impacto {imp:.0f}s) ----------")
        if not acoes:
            linhas.append("-- advisor nao propos acao executavel para este SQL.")
            linhas.append("")
            continue
        for cmd, beneficio in acoes:
            ben = f"  -- beneficio estimado {beneficio}" if beneficio else ""
            if _ADV_PROFILE.search(cmd):
                linhas.append("-- SQL Profile: fixa o plano bom sem tocar na "
                              "aplicacao. A tuning task foi MANTIDA no banco")
                linhas.append("-- para que este comando funcione; rode o drop no "
                              "fim depois de decidir.")
            linhas.append(f"-- {cmd}{ben}")
        linhas.append("")
    with open(os.path.join(case_dir, "acoes.sql"), "a", encoding="utf-8") as f:
        f.write("\n".join(linhas) + "\n")
    log("[ok] acoes.sql atualizado com as recomendacoes do advisor")


def collect_advisor(cur, case_dir, log, top_n: int = TOP_N,
                    limite_min: int = 5) -> list[str]:
    """Roda o SQL Tuning Advisor nos SQL que justificam o custo.

    ATENCAO - NAO e read-only: cria e executa tuning tasks (DBMS_SQLTUNE) e
    consome **Tuning Pack**. So roda com opt-in explicito (COLETAR.py --advisor).
    Ver `_advisor_elegiveis` para o criterio de selecao.
    """
    headers, rows = _fetch(cur, SQL_TOP_REGRESSIONS, {"top_n": top_n})
    alvos = _advisor_elegiveis(headers, rows)
    caminho = os.path.join(case_dir, "advisor.txt")

    if not alvos:
        # Registra que o check rodou e nao se aplicava, em vez de nao gerar
        # arquivo: sem isso o relatorio nao consegue distinguir "nao elegivel"
        # de "voce esqueceu de passar --advisor".
        with open(caminho, "w", encoding="utf-8") as f:
            f.write("=== SQL TUNING ADVISOR ===\n"
                    "-- Nao executado: nenhum SQL elegivel na janela.\n"
                    "-- Criterio: troca de plano OU impacto >= "
                    f"{ADVISOR_IMPACTO_MIN_S:.0f}s.\n"
                    "-- Nenhuma tuning task criada; Tuning Pack NAO consumido.\n")
        log("[ok] advisor.txt (nenhum SQL elegivel - nada executado)")
        return ["advisor.txt"]

    partes = ["=== SQL TUNING ADVISOR (executado com --advisor) ===",
              f"-- {len(alvos)} SQL elegivel(is) de {len(rows)} regredido(s): "
              "troca de plano ou impacto >= "
              f"{ADVISOR_IMPACTO_MIN_S:.0f}s.",
              f"-- Limite de {limite_min} min por SQL. Tuning Pack consumido.", ""]
    blocos = []
    for sql_id, tipo, imp in alvos:
        task = None
        manter = False
        try:
            task_var = cur.var(str, 128)
            cur.execute(
                "BEGIN :task := DBMS_SQLTUNE.CREATE_TUNING_TASK("
                "sql_id => :sql_id, scope => 'COMPREHENSIVE', "
                "time_limit => :lim, task_name => NULL); END;",
                {"task": task_var, "sql_id": sql_id,
                 "lim": int(limite_min) * 60})
            task = task_var.getvalue()
            cur.callproc("dbms_sqltune.execute_tuning_task", [task])
            cur.execute("SELECT DBMS_SQLTUNE.REPORT_TUNING_TASK(:t, 'TEXT', "
                        "'ALL', 'ALL') FROM dual", {"t": task})
            lob = cur.fetchone()[0]
            txt = lob.read() if hasattr(lob, "read") else str(lob)
            partes.append(f"--- {sql_id} ({tipo}, impacto {imp:.0f}s) ---\n{txt}")
            acoes = _extrai_acoes_advisor(txt)
            blocos.append((sql_id, tipo, imp, acoes))
            # accept_sql_profile referencia a task pelo nome: se ela for
            # removida aqui, a recomendacao no relatorio fica inexecutavel.
            manter = any(_ADV_PROFILE.search(c) for c, _ in acoes)
            if manter:
                partes.append(f"-- tuning task {task} MANTIDA (ha SQL Profile a "
                              "aceitar). Remova apos decidir:\n"
                              f"--   EXEC DBMS_SQLTUNE.DROP_TUNING_TASK('{task}');")
            log(f"[ok] advisor {sql_id} ({len(acoes)} acao(oes))")
        except Exception as ex:
            msg = str(ex).splitlines()[0]
            partes.append(f"--- {sql_id} ---\n-- ERRO: {msg}")
            log(f"[warn] advisor {sql_id}: {msg}")
        finally:
            if task and not manter:
                try:
                    cur.callproc("dbms_sqltune.drop_tuning_task", [task])
                except Exception:
                    pass

    with open(caminho, "w", encoding="utf-8") as f:
        f.write("\n\n".join(partes) + "\n")
    log("[ok] advisor.txt")
    _grava_acoes_advisor(case_dir, blocos, log)
    return ["advisor.txt"]
