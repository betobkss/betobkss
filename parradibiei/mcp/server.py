"""
MCP server minimo (stdio, JSON-RPC 2.0) que expoe o catalogo de troubleshooting
como ferramentas READ-ONLY para clientes MCP (Claude Desktop, Cline, Copilot...).

NAO conecta em banco: expoe apenas o conhecimento curado (checks, scripts,
conexoes e o SQL de cada check) para o assistente montar/entender a coleta.
A execucao no banco continua sendo feita pelo COLETAR.py (com --pack e read-only).

Transporte: mensagens JSON-RPC delimitadas por newline em stdin/stdout (stdio).
Metodos: initialize, notifications/initialized, tools/list, tools/call, ping.

STATUS: DESATIVADO (2026-07-29). Nao esta registrado em nenhum cliente MCP.
O fluxo em uso (COLETAR/TROUBLESHOOT + regra do chat) nao depende deste modulo:
nada no projeto importa mcp.server, so os testes. Foi desligado porque nao tinha
consumidor e cada cliente aberto deixava um python segurando a pasta do projeto.

LICENCA: _tool_run_check NEGA POR PADRAO qualquer check com 'requires_pack'
(Diagnostics/Tuning). Rodar ASH/AWR em banco sem licenca e consumo indevido e
vira achado de auditoria - e quem chama aqui e um assistente, que nao tem como
saber o que o banco licencia. Para executar mesmo assim, o cliente precisa
passar allow_pack=true de forma explicita (ver _pack_bloqueado).

Uso (config do cliente MCP):
  { "mcpServers": { "oracle-troubleshoot": {
      "command": "C:/.../.venv/Scripts/python.exe",
      "args": ["-m", "mcp.server"] } } }
"""
from __future__ import annotations

import json
import sys

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "oracle-troubleshoot-mcp", "version": "0.1.0"}

# ── Ferramentas expostas (todas read-only, sem acesso a banco) ──
TOOLS = [
    {
        "name": "list_checks",
        "description": "Lista os checks de diagnostico do catalogo (id, categoria, "
                       "titulo, arquivo de saida, pack exigido). Filtro opcional por categoria.",
        "inputSchema": {"type": "object", "properties": {
            "category": {"type": "string", "description": "categoria (waits, sql, space, ha_dg, ...)"}}},
    },
    {
        "name": "get_check",
        "description": "Detalha um check pelo id (purpose, thresholds, references, SQL read-only, pack).",
        "inputSchema": {"type": "object", "properties": {
            "id": {"type": "string"}}, "required": ["id"]},
    },
    {
        "name": "list_connections",
        "description": "Lista as conexoes Oracle do repositorio (nome, host, porta, service, user).",
        "inputSchema": {"type": "object", "properties": {
            "query": {"type": "string", "description": "filtro por nome/host/service"}}},
    },
    {
        "name": "list_scripts",
        "description": "Lista a biblioteca de scripts DBA (read-only e acoes destrutivas).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "run_query",
        "description": "Executa uma consulta READ-ONLY (SELECT/WITH) numa conexao do "
                       "repositorio e retorna as linhas. Sessao READ ONLY; nenhum DML/DDL. "
                       "Requer ORA_PASSWORD no ambiente do servidor (opt-in).",
        "inputSchema": {"type": "object", "properties": {
            "conn": {"type": "string", "description": "nome da conexao no Parra.json"},
            "sql": {"type": "string", "description": "SELECT/WITH read-only"}},
            "required": ["conn", "sql"]},
    },
    {
        "name": "run_check",
        "description": "Executa um check do catalogo (por id) numa conexao, READ-ONLY, e "
                       "retorna as linhas. Requer ORA_PASSWORD no ambiente do servidor. "
                       "Checks que exigem Diagnostics/Tuning Pack sao RECUSADOS por "
                       "padrao: so rodam com allow_pack=true, e apenas se o banco "
                       "licenciar o pack.",
        "inputSchema": {"type": "object", "properties": {
            "conn": {"type": "string"}, "id": {"type": "string"},
            "allow_pack": {"type": "boolean", "description":
                "confirma que o banco licencia o pack exigido pelo check "
                "(Diagnostics/Tuning). Sem isso o check e recusado."}},
            "required": ["conn", "id"]},
    },
]


def _tool_list_checks(args):
    from . import list_checks
    cat = args.get("category")
    return [{"id": c["id"], "category": c["category"], "title": c["title"],
             "output_file": c["output_file"], "requires_pack": c.get("requires_pack", "NONE"),
             "optional": bool(c.get("optional", False))}
            for c in list_checks(cat)]


def _tool_get_check(args):
    from . import get_check
    c = get_check(args.get("id", ""))
    return c or {"error": "check nao encontrado"}


def _tool_list_connections(args):
    from troubleshoot import conn_repo
    q = args.get("query")
    conns = conn_repo.find(q) if q else conn_repo.load_connections()
    return [{"name": c["name"], "host": c["host"], "port": c["port"],
             "service": c["service"], "user": c["user"], "folder": c.get("folder", "")}
            for c in conns[:200]]


def _tool_list_scripts(args):
    from . import list_scripts
    return list_scripts()


def _connect_readonly(conn_name, senha: str = ""):
    """Abre conexao read-only a partir de uma conexao do repositorio.

    `senha` e o caminho normal para quem TEM como perguntar (CLI com terminal).
    O fallback por `ORA_PASSWORD` existe so para este servidor MCP: ele roda em
    segundo plano, sem terminal e sem painel, entao nao ha a quem perguntar - e
    a unica excecao consciente a regra de "senha sempre digitada"
    (ver troubleshoot/senha.py). Sem a variavel, a consulta fica desabilitada.
    Retorna (conn, Conn).
    """
    import os

    from troubleshoot import conn_repo
    matches = conn_repo.find(conn_name)
    if not matches:
        raise RuntimeError(f"conexao '{conn_name}' nao encontrada no repositorio")
    c = matches[0]
    pwd = senha or os.environ.get("ORA_PASSWORD")
    if not pwd:
        raise RuntimeError("consulta desabilitada: defina ORA_PASSWORD no ambiente do "
                           "MCP server para habilitar run_query/run_check")
    import oracledb
    return oracledb.connect(user=c["user"], password=pwd, dsn=c.dsn), c


def _run_select(conn_name, sql):
    from troubleshoot.collector import _fetch_table, validate_select
    if not validate_select(sql):
        return {"error": "apenas SELECT/WITH read-only sao permitidos (nenhum DML/DDL)"}
    conn, c = _connect_readonly(conn_name)
    try:
        cur = conn.cursor()
        cur.execute("SET TRANSACTION READ ONLY")
        try:
            cur.callproc("dbms_application_info.set_module", ["TROUBLESHOOT-MCP", "run_query"])
        except Exception:
            pass
        headers, rows = _fetch_table(cur, sql)
        return {"conn": c["name"], "dsn": c.dsn, "columns": headers,
                "rows": [[("" if v is None else str(v)) for v in r] for r in rows[:100]],
                "row_count": len(rows), "truncated": len(rows) > 100}
    finally:
        conn.close()


def _tool_run_query(args):
    return _run_select(args.get("conn", ""), args.get("sql", ""))


def _pack_bloqueado(check: dict, allow_pack: bool) -> dict | None:
    """Erro a devolver se o check exige pack e ninguem confirmou a licenca.

    Negar por padrao e deliberado. O COLETAR.py detecta os packs do banco
    (`_detect_packs`) antes de escolher os checks; aqui quem chama e um cliente
    MCP - tipicamente um assistente - que nao tem esse contexto e pediria
    `ash_wait_classes` sem saber que aquele banco nao tem Diagnostics Pack.
    Consumo indevido de pack e cobranca e achado de auditoria, nao um detalhe.
    Devolve None quando pode executar.
    """
    pack = (check.get("requires_pack") or "NONE").upper()
    if pack == "NONE" or allow_pack:
        return None
    return {
        "error": f"check '{check['id']}' exige {pack} Pack e foi recusado.",
        "requires_pack": pack,
        "como_prosseguir": (
            "confirme que este banco licencia o pack e repita com "
            "allow_pack=true; ou use COLETAR.py --pack N, que so coleta o "
            "que nao consome licenca."),
    }


def _tool_run_check(args):
    from . import get_check
    c = get_check(args.get("id", ""))
    if not c:
        return {"error": "check nao encontrado"}
    if c.get("special"):
        return {"error": f"check '{c['id']}' e especial (use COLETAR.py)"}
    bloqueio = _pack_bloqueado(c, bool(args.get("allow_pack")))
    if bloqueio:
        return bloqueio
    return _run_select(args.get("conn", ""), c["sql"])


_DISPATCH = {
    "list_checks": _tool_list_checks,
    "get_check": _tool_get_check,
    "list_connections": _tool_list_connections,
    "list_scripts": _tool_list_scripts,
    "run_query": _tool_run_query,
    "run_check": _tool_run_check,
}


def handle(req: dict):
    """Processa uma mensagem JSON-RPC. Retorna a resposta (dict) ou None
    (para notificacoes, que nao respondem)."""
    method = req.get("method")
    rid = req.get("id")

    if method == "initialize":
        return _ok(rid, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        })
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return _ok(rid, {})
    if method == "tools/list":
        return _ok(rid, {"tools": TOOLS})
    if method == "tools/call":
        params = req.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        fn = _DISPATCH.get(name)
        if not fn:
            return _err(rid, -32601, f"ferramenta desconhecida: {name}")
        try:
            data = fn(args)
            text = json.dumps(data, ensure_ascii=False, indent=2)
            return _ok(rid, {"content": [{"type": "text", "text": text}]})
        except Exception as e:
            return _err(rid, -32000, f"erro ao executar {name}: {e}")
    if rid is not None:
        return _err(rid, -32601, f"metodo nao suportado: {method}")
    return None


def _ok(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _err(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def serve(stdin=None, stdout=None):
    """Loop stdio: le JSON por linha, responde JSON por linha."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        resp = handle(req)
        if resp is not None:
            stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            stdout.flush()


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    serve()
