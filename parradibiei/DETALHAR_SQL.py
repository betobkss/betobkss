"""
DETALHAR_SQL - detalhamento READ-ONLY de um SQL_ID (comando 'detalhar' do DBA Master).

Coleta, para um sql_id: texto do SQL, historico de execucao por snapshot (AWR),
o(s) plano(s) de execucao (DBMS_XPLAN.DISPLAY_AWR) e as estatisticas dos objetos
referenciados no plano. Grava em troubleshooting/<caso>/sql_<sql_id>.txt.

Seguranca: sessao SET TRANSACTION READ ONLY, autocommit off, apenas leitura.

Uso:
  python DETALHAR_SQL.py <caso> <sql_id> --host H --service S --user U [--port 1521]
  python DETALHAR_SQL.py <caso> <sql_id> --conn NOME
  python DETALHAR_SQL.py <caso> <sql_id> --dsn host:porta/service --user U

Credenciais: a senha e sempre digitada na hora (ver troubleshoot/senha.py).
"""
from __future__ import annotations

import argparse
import os
import re
import sys

from troubleshoot import conn_repo
from troubleshoot import senha as mod_senha
from troubleshoot.collector import CASES_DIR, render_table


def _fetch(cur, sql, binds=None):
    cur.execute(sql, binds or [])
    cols = [d[0] for d in cur.description]
    return cols, cur.fetchall()


def _xplan(cur, sql_id, phv):
    """Plano AWR de um plan_hash_value (literais sanitizados; sessao read-only)."""
    sql = ("SELECT plan_table_output FROM TABLE(DBMS_XPLAN.DISPLAY_AWR("
           f"'{sql_id}', {int(phv)}, NULL, 'ALL'))")
    cur.execute(sql)
    return "\n".join(str(r[0]) for r in cur.fetchall())


def main():
    ap = argparse.ArgumentParser(description="Detalhamento read-only de um SQL_ID")
    ap.add_argument("case")
    ap.add_argument("sql_id")
    ap.add_argument("--dsn")
    ap.add_argument("--host")
    ap.add_argument("--port")
    ap.add_argument("--service")
    ap.add_argument("--user")
    ap.add_argument("--conn")
    args = ap.parse_args()

    sql_id = args.sql_id.strip()
    if not re.match(r"^[0-9a-z]+$", sql_id):
        print("[ERRO] sql_id invalido")
        sys.exit(2)

    dsn = args.dsn
    user = args.user or os.environ.get("ORA_USER")
    if not dsn and args.host and args.service:
        dsn = f"{args.host}:{args.port or '1529'}/{args.service}"
    if not dsn and args.conn:
        matches = conn_repo.find(args.conn)
        if matches:
            dsn = matches[0].dsn
            user = user or matches[0]["user"]
            print(f"  -> {matches[0]}")
    if not dsn:
        print("[ERRO] informe --host+--service, --dsn ou --conn")
        sys.exit(2)
    user = user or "dbaread"
    try:
        password = mod_senha.pedir(user)
    except mod_senha.SemComoPerguntar:
        for linha in mod_senha.explicar_falta(user):
            print(linha)
        sys.exit(2)

    try:
        import oracledb
    except ImportError:
        print("[ERRO] python-oracledb nao instalado")
        sys.exit(1)

    print(f"[conn] {user}@{dsn} (thin, read-only) | sql_id={sql_id}")
    conn = oracledb.connect(user=user, password=password, dsn=dsn)
    conn.autocommit = False
    parts = []
    try:
        cur = conn.cursor()
        try:
            cur.callproc("dbms_application_info.set_module",
                         ["TROUBLESHOOT-RO", (f"detalhar {sql_id}")[:48]])
        except Exception:
            pass
        cur.execute("SET TRANSACTION READ ONLY")

        # 1) Texto do SQL
        try:
            _, r = _fetch(cur, "SELECT sql_fulltext FROM gv$sql WHERE sql_id=:1 AND ROWNUM=1", [sql_id])
            txt = ""
            if r:
                lob = r[0][0]
                txt = lob.read() if hasattr(lob, "read") else str(lob)
            parts.append(f"=== SQL TEXT ({sql_id}) ===\n{(txt or '(nao esta na memoria)').strip()[:4000]}")
        except Exception as e:
            parts.append(f"=== SQL TEXT ({sql_id}) ===\n-- ERRO: {str(e).splitlines()[0]}")

        # 2) Historico de execucao por snapshot (AWR)
        try:
            c, r = _fetch(cur,
                "SELECT snap_id, plan_hash_value AS phv, executions_delta AS execs, "
                "ROUND(elapsed_time_delta/1e6/NULLIF(executions_delta,0),3) AS avg_s, "
                "ROUND(buffer_gets_delta/NULLIF(executions_delta,0)) AS gets_exec, "
                "ROUND(disk_reads_delta/NULLIF(executions_delta,0)) AS reads_exec, "
                "ROUND(rows_processed_delta/NULLIF(executions_delta,0)) AS rows_exec "
                "FROM dba_hist_sqlstat WHERE sql_id=:1 AND executions_delta>0 "
                "ORDER BY snap_id", [sql_id])
            parts.append("=== HISTORICO POR SNAPSHOT (AWR) ===\n" + render_table(c, r, max_rows=80))
        except Exception as e:
            parts.append(f"=== HISTORICO POR SNAPSHOT (AWR) ===\n-- ERRO: {str(e).splitlines()[0]}")

        # 3) Plano(s) de execucao distintos (AWR)
        try:
            _, r = _fetch(cur,
                "SELECT DISTINCT plan_hash_value FROM dba_hist_sqlstat "
                "WHERE sql_id=:1 AND plan_hash_value>0", [sql_id])
            if not r:
                parts.append("=== PLANO AWR ===\n(sem plano no AWR)")
            for (phv,) in r:
                try:
                    parts.append(f"=== PLANO AWR (plan_hash_value={phv}) ===\n{_xplan(cur, sql_id, phv)}")
                except Exception as e:
                    parts.append(f"=== PLANO AWR (plan_hash_value={phv}) ===\n-- ERRO: {str(e).splitlines()[0]}")
        except Exception as e:
            parts.append(f"=== PLANO AWR ===\n-- ERRO: {str(e).splitlines()[0]}")

        # 4) Estatisticas dos objetos do plano
        try:
            c, r = _fetch(cur,
                "SELECT o.object_owner AS owner, o.object_name AS name, s.num_rows, "
                "TO_CHAR(s.last_analyzed,'YYYY-MM-DD HH24:MI') AS last_analyzed, s.stale_stats "
                "FROM (SELECT DISTINCT object_owner, object_name FROM dba_hist_sql_plan "
                "WHERE sql_id=:1 AND object_owner IS NOT NULL) o "
                "LEFT JOIN dba_tab_statistics s ON s.owner=o.object_owner "
                "AND s.table_name=o.object_name AND s.partition_name IS NULL "
                "ORDER BY s.stale_stats DESC NULLS LAST, s.last_analyzed", [sql_id])
            parts.append("=== ESTATISTICAS DOS OBJETOS DO PLANO ===\n" + render_table(c, r, max_rows=60))
        except Exception as e:
            parts.append(f"=== ESTATISTICAS DOS OBJETOS DO PLANO ===\n-- ERRO: {str(e).splitlines()[0]}")
    finally:
        conn.close()

    case_dir = os.path.join(CASES_DIR, args.case)
    os.makedirs(case_dir, exist_ok=True)
    out = os.path.join(case_dir, f"sql_{sql_id}.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n\n".join(parts) + "\n")
    print(f"[OK] detalhamento gravado: {out}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
