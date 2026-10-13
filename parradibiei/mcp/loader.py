"""
Loader do catalogo MCP (catalog.json): fonte unica de verdade dos checks
READ-ONLY. Usado tanto pelo fluxo ONLINE (COLETAR.py / collector.py via
oracledb) quanto para gerar o script OFFLINE (coletor.sql para Citrix).

CLI:
  python -m mcp.loader --list                 # lista todos os checks
  python -m mcp.loader --list --cat waits      # filtra por categoria
  python -m mcp.loader --show ash_wait_classes # detalhe de um check
  python -m mcp.loader --gen-sql queries/coletor.sql   # gera o coletor offline
  python -m mcp.loader --categories            # lista as categorias
"""
from __future__ import annotations

import argparse
import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CATALOG_PATH = os.path.join(BASE_DIR, "catalog.json")


def load_catalog(path: str = CATALOG_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def list_checks(category: str | None = None, path: str = CATALOG_PATH) -> list[dict]:
    checks = load_catalog(path).get("checks", [])
    if category:
        checks = [c for c in checks if c.get("category") == category]
    return checks


def get_check(check_id: str, path: str = CATALOG_PATH) -> dict | None:
    for c in load_catalog(path).get("checks", []):
        if c.get("id") == check_id:
            return c
    return None


SCRIPTS_INDEX = os.path.join(BASE_DIR, "scripts", "scripts_index.json")


def list_scripts(category: str | None = None, path: str = SCRIPTS_INDEX) -> list[dict]:
    """Lista os scripts da biblioteca (mcp/scripts/scripts_index.json)."""
    try:
        scripts = json.load(open(path, encoding="utf-8")).get("scripts", [])
    except Exception:
        return []
    if category:
        scripts = [s for s in scripts if s.get("category") == category]
    return scripts


def online_items(path: str = CATALOG_PATH) -> list[dict]:
    """Retorna itens prontos para a coleta ONLINE (oracledb).
    Cada item: {id, output_file, subtitle, sql, optional, special}."""
    items = []
    for c in load_catalog(path).get("checks", []):
        items.append({
            "id": c["id"],
            "output_file": c["output_file"],
            "subtitle": c.get("subtitle"),
            "sql": c["sql"],
            "optional": bool(c.get("optional", False)),
            "special": c.get("special"),
            # NONE = so v$/dicionario (sem licenca); DIAGNOSTIC = ASH/AWR/ADDM;
            # TUNING = SQL Monitor / SQL Tuning Advisor
            "requires_pack": c.get("requires_pack", "NONE"),
            # Tags de topologia exigidas (rac/exadata/asm/goldengate/dataguard).
            # Ausente = o check vale para qualquer ambiente.
            "requires_topology": c.get("requires_topology"),
        })
    return items


def recommendations_by_file(path: str = CATALOG_PATH) -> dict:
    """Mapa output_file -> recomendacao do catalogo.

    Permite que o analisador generico use a orientacao de DBA registrada no
    catalogo (em vez de um texto vago), mantendo o catalogo como fonte unica
    de verdade. Quando um arquivo tem varios checks, concatena as distintas."""
    out: dict = {}
    try:
        checks = load_catalog(path).get("checks", [])
    except Exception:
        return out
    for c in checks:
        rec = (c.get("recommendation") or "").strip()
        if not rec:
            continue
        f = c.get("output_file")
        if not f:
            continue
        cur = out.get(f)
        if not cur:
            out[f] = rec
        elif rec not in cur:
            out[f] = f"{cur} {rec}"
    return out


def _offline_sql(check: dict) -> str:
    """SQL da versao offline (SQL*Plus). Ajusta os checks com bind (:sql_id)."""
    sql = check["sql"].strip().rstrip(";")
    if check.get("special") == "sqlmon":
        # Sem bind no SQL*Plus: usa o SQL monitorado default (mais custoso recente)
        return ("SELECT DBMS_SQLTUNE.REPORT_SQL_MONITOR(type => 'TEXT', "
                "report_level => 'ALL') FROM dual")
    return sql


def generate_coletor_sql(out_path: str, categories: list[str] | None = None,
                         include_optional: bool = True, path: str = CATALOG_PATH) -> str:
    """Gera o script SQL*Plus (Citrix) a partir do catalogo, com marcadores
    @@@FILE:nome@@@ que o TROUBLESHOOT.py divide e roteia. Retorna out_path."""
    cat = load_catalog(path)
    checks = cat.get("checks", [])
    if categories:
        checks = [c for c in checks if c.get("category") in categories]
    if not include_optional:
        checks = [c for c in checks if not c.get("optional")]

    # agrupa por arquivo de saida preservando a ordem
    order: list[str] = []
    groups: dict[str, list[dict]] = {}
    for c in checks:
        f = c["output_file"]
        if f not in groups:
            groups[f] = []
            order.append(f)
        groups[f].append(c)

    lines = [
        "-- ============================================================",
        "-- coletor.sql - GERADO A PARTIR DE mcp/catalog.json (NAO EDITAR A MAO)",
        f"-- Regerar: python -m mcp.loader --gen-sql {out_path}",
        "-- Uso no Citrix: @coletor.sql  ->  copie coletor_troubleshoot.txt",
        "--   para troubleshooting\\<caso>\\ e rode: .\\redija.bat <caso>",
        "-- SOMENTE LEITURA: sessao aberta como READ ONLY.",
        "-- LICENCA: blocos marcados 'requer DIAGNOSTIC/TUNING PACK' consomem",
        "--   Oracle Diagnostics/Tuning Pack (ASH/AWR/ADDM/SQL Monitor). Se o",
        "--   banco NAO for licenciado, remova esses blocos antes de executar.",
        "-- ============================================================",
        "SET LINESIZE 240 PAGESIZE 5000 TRIMSPOOL ON TRIMOUT ON",
        "SET FEEDBACK OFF ECHO OFF VERIFY OFF HEADING ON TAB OFF LONG 2000000 LONGCHUNKSIZE 2000000",
        "ALTER SESSION SET NLS_DATE_FORMAT = 'YYYY-MM-DD HH24:MI:SS';",
        "SET TRANSACTION READ ONLY;",
        "",
        "SPOOL coletor_troubleshoot.txt",
        "",
    ]
    for fname in order:
        lines.append(f"PROMPT @@@FILE:{fname}@@@")
        for c in groups[fname]:
            rp = c.get("requires_pack", "NONE")
            if rp != "NONE":
                lines.append(f"-- ATENCAO: requer {rp} PACK (licenca Oracle) - remova se nao licenciado")
            if c.get("subtitle"):
                lines.append(f"PROMPT === {c['subtitle']} ===")
            lines.append(_offline_sql(c) + ";")
        lines.append("")
    lines += [
        "SPOOL OFF",
        "PROMPT",
        "PROMPT === COLETA CONCLUIDA: copie coletor_troubleshoot.txt para troubleshooting\\<caso>\\ ===",
        "",
    ]
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return out_path


def _main():
    ap = argparse.ArgumentParser(description="MCP catalog loader")
    ap.add_argument("--list", action="store_true", help="lista os checks")
    ap.add_argument("--cat", help="filtra por categoria")
    ap.add_argument("--show", help="detalha um check por id")
    ap.add_argument("--categories", action="store_true", help="lista categorias")
    ap.add_argument("--scripts", action="store_true", help="lista a biblioteca de scripts")
    ap.add_argument("--gen-sql", metavar="PATH", help="gera o coletor.sql offline")
    ap.add_argument("--no-optional", action="store_true", help="exclui checks opcionais")
    args = ap.parse_args()

    cat = load_catalog()
    if args.categories:
        for k, v in cat.get("categories", {}).items():
            n = len(list_checks(k))
            print(f"  {k:10} ({n:2}) - {v}")
        return
    if args.scripts:
        scripts = list_scripts(args.cat)
        print(f"{len(scripts)} script(s):")
        for s in scripts:
            flag = "READ-ONLY" if s.get("read_only") else "ACAO/DESTRUTIVO"
            print(f"  [{flag:15}] {s['file']:32} {s.get('purpose','')}")
        return
    if args.show:
        c = get_check(args.show)
        if not c:
            print(f"[nao encontrado] {args.show}")
            return
        print(json.dumps(c, indent=2, ensure_ascii=False))
        return
    if args.gen_sql:
        out = generate_coletor_sql(args.gen_sql, include_optional=not args.no_optional)
        print(f"[OK] coletor offline gerado: {out} ({len(list_checks(args.cat))} checks)")
        return
    if args.list or args.cat:
        checks = list_checks(args.cat)
        print(f"{len(checks)} check(s)" + (f" na categoria '{args.cat}'" if args.cat else "")
              + f" (total {len(cat.get('checks', []))}):")
        for c in checks:
            opt = " [opcional]" if c.get("optional") else ""
            print(f"  [{c['category']:9}] {c['id']:22} -> {c['output_file']:14} {c['title']}{opt}")
        return
    ap.print_help()


if __name__ == "__main__":
    _main()
