"""
JANELA - analise READ-ONLY de uma janela de tempo (o pico do incidente).

Usa DBA_HIST_ACTIVE_SESS_HISTORY (ASH historico) para responder, no intervalo
informado: AAS por hora, top wait classes, top eventos, top SQL por DB time
(com modulo/texto), top modulos e top usuarios. Ajuda a achar a hora do pico e o
que realmente consumiu DB time - independente do "agora". Requer Diagnostics Pack.

Grava em troubleshooting/<caso>/janela.txt.

Uso:
  python JANELA.py <caso> --inicio "2026-07-23 00:00" --fim "2026-07-24 00:00" \
      --host H --service S --user U [--port 1521]
  python JANELA.py <caso> --inicio ... --fim ... --conn NOME

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

_DT = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")


def _fetch(cur, sql, binds):
    cur.execute(sql, binds)
    cols = [d[0] for d in cur.description]
    return cols, cur.fetchall()


def main():
    ap = argparse.ArgumentParser(description="Analise read-only de janela (ASH historico)")
    ap.add_argument("case")
    ap.add_argument("--inicio", required=True, help="YYYY-MM-DD HH:MI")
    ap.add_argument("--fim", required=True, help="YYYY-MM-DD HH:MI")
    ap.add_argument("--dsn")
    ap.add_argument("--host")
    ap.add_argument("--port")
    ap.add_argument("--service")
    ap.add_argument("--user")
    ap.add_argument("--conn")
    args = ap.parse_args()

    if not (_DT.match(args.inicio) and _DT.match(args.fim)):
        print("[ERRO] use o formato 'YYYY-MM-DD HH:MI' em --inicio/--fim")
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

    b = {"ini": args.inicio, "fim": args.fim}
    W = ("h.sample_time BETWEEN TO_DATE(:ini,'YYYY-MM-DD HH24:MI') "
         "AND TO_DATE(:fim,'YYYY-MM-DD HH24:MI')")
    queries = [
        ("AAS POR HORA (media de sessoes ativas)",
         f"SELECT TO_CHAR(h.sample_time,'MM-DD HH24') AS hora, "
         f"ROUND(COUNT(*)/360,2) AS aas, "
         f"ROUND(SUM(CASE WHEN h.session_state='ON CPU' THEN 1 ELSE 0 END)/360,2) AS aas_cpu "
         f"FROM dba_hist_active_sess_history h WHERE {W} "
         f"GROUP BY TO_CHAR(h.sample_time,'MM-DD HH24') ORDER BY hora"),
        ("TOP WAIT CLASSES NA JANELA",
         f"SELECT NVL(h.wait_class,'CPU') AS wait_class, COUNT(*) AS samples, "
         f"ROUND(100*RATIO_TO_REPORT(COUNT(*)) OVER (),1) AS pct "
         f"FROM dba_hist_active_sess_history h WHERE {W} "
         f"GROUP BY NVL(h.wait_class,'CPU') ORDER BY samples DESC"),
        ("TOP EVENTOS NA JANELA",
         f"SELECT * FROM (SELECT NVL(h.event,'ON CPU') AS event, COUNT(*) AS samples, "
         f"ROUND(100*RATIO_TO_REPORT(COUNT(*)) OVER (),1) AS pct "
         f"FROM dba_hist_active_sess_history h WHERE {W} "
         f"GROUP BY NVL(h.event,'ON CPU') ORDER BY samples DESC) WHERE ROWNUM<=20"),
        ("TOP SQL POR DB TIME NA JANELA",
         f"SELECT * FROM (SELECT h.sql_id, COUNT(*) AS samples, "
         f"ROUND(100*RATIO_TO_REPORT(COUNT(*)) OVER (),1) AS pct, "
         f"MAX(h.module) AS module, MAX(DBMS_LOB.SUBSTR(t.sql_text,70,1)) AS sql_text "
         f"FROM dba_hist_active_sess_history h "
         f"LEFT JOIN dba_hist_sqltext t ON t.sql_id=h.sql_id AND t.dbid=h.dbid "
         f"WHERE {W} AND h.sql_id IS NOT NULL "
         f"GROUP BY h.sql_id ORDER BY samples DESC) WHERE ROWNUM<=20"),
        ("TOP MODULOS/APP NA JANELA",
         f"SELECT * FROM (SELECT NVL(h.module,'(sem modulo)') AS module, COUNT(*) AS samples, "
         f"ROUND(100*RATIO_TO_REPORT(COUNT(*)) OVER (),1) AS pct "
         f"FROM dba_hist_active_sess_history h WHERE {W} "
         f"GROUP BY NVL(h.module,'(sem modulo)') ORDER BY samples DESC) WHERE ROWNUM<=20"),
        ("TOP USUARIOS NA JANELA",
         f"SELECT * FROM (SELECT NVL(u.username,TO_CHAR(h.user_id)) AS usuario, COUNT(*) AS samples, "
         f"ROUND(100*RATIO_TO_REPORT(COUNT(*)) OVER (),1) AS pct "
         f"FROM dba_hist_active_sess_history h LEFT JOIN dba_users u ON u.user_id=h.user_id "
         f"WHERE {W} GROUP BY NVL(u.username,TO_CHAR(h.user_id)) ORDER BY samples DESC) WHERE ROWNUM<=15"),
    ]

    print(f"[conn] {user}@{dsn} (thin, read-only) | janela {args.inicio} -> {args.fim}")
    conn = oracledb.connect(user=user, password=password, dsn=dsn)
    conn.autocommit = False
    parts = [f"=== JANELA ANALISADA: {args.inicio} -> {args.fim} ==="]
    try:
        cur = conn.cursor()
        try:
            cur.callproc("dbms_application_info.set_module", ["TROUBLESHOOT-RO", "janela"])
        except Exception:
            pass
        cur.execute("SET TRANSACTION READ ONLY")
        for title, sql in queries:
            try:
                c, r = _fetch(cur, sql, b)
                parts.append(f"=== {title} ===\n" + render_table(c, r, max_rows=60))
            except Exception as e:
                parts.append(f"=== {title} ===\n-- ERRO: {str(e).splitlines()[0]}")
    finally:
        conn.close()

    case_dir = os.path.join(CASES_DIR, args.case)
    os.makedirs(case_dir, exist_ok=True)
    out = os.path.join(case_dir, "janela.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n\n".join(parts) + "\n")
    print(f"[OK] analise de janela gravada: {out}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
