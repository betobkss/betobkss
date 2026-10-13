r"""
VALIDAR_KB - confere a base de conhecimento (kb/*.json).

Por que existe: playbook e documentacao EXECUTAVEL. Se o SQL nao roda, o DBA
descobre no meio de um incidente, copiando o comando e levando ORA-00904 na
cara - pior do que nao ter playbook nenhum. Na primeira rodada desta validacao,
8 consultas de 110 tinham coluna inexistente ou erro de sintaxe.

Dois niveis:

1. ESTRUTURA (offline, sem banco) - o mesmo contrato que os testes cobram:
   risco declarado, pre-requisito em passo que muda algo, "o que olhar" em todo
   passo. Roda em qualquer lugar, inclusive no pipeline.

2. SQL AO VIVO (--conn) - executa cada consulta READ-ONLY dos passos de leitura
   contra um banco real e reporta o que falhou. Use SEMPRE em homologacao.
   Nada de escrita e executado: passos de risco 'mudanca'/'destrutivo' sao
   deliberadamente ignorados.

Uso:
    .\.venv\Scripts\python.exe VALIDAR_KB.py
    .\.venv\Scripts\python.exe VALIDAR_KB.py --conn DBSRV600
    .\.venv\Scripts\python.exe VALIDAR_KB.py --conn DBSRV600 --playbook archive_cheio

Senha: digitada na hora, como em todo o framework (ver troubleshoot/senha.py).
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sys

from troubleshoot import kb
from troubleshoot import senha as mod_senha

# Verbos aceitos num passo marcado como leitura, por lugar de execucao.
#
# 'sql' serve a TODAS as plataformas: SELECT/WITH sao read-only em Oracle,
# T-SQL, Db2 e PostgreSQL igualmente. O que exige tratamento proprio sao os
# CLIs com vocabulario proprio (RMAN, db2pd, redis-cli), onde o verbo inicial
# nao basta - ver as funcoes condicionais abaixo.
LEITURA_OK = {
    "sql": ("select", "with", "show"),
    "rman": ("show", "report", "list", "validate"),
    # 'so' (shell) faltava aqui ate 2026-09: o laco de checar_estrutura pulava
    # o que nao estivesse neste dicionario, entao os 28 passos de sistema
    # operacional NUNCA foram validados por esta ferramenta - so pelo teste.
    # Quem rodasse VALIDAR_KB.py e visse "tudo certo" estava recebendo uma
    # garantia que nao cobria shell.
    "so": ("uptime", "vmstat", "free", "iostat", "top", "df", "ps", "mpstat",
           "sar", "lscpu", "nproc", "cat", "grep", "tail", "head", "netstat",
           "ss", "adrci", "dbv", "lsnrctl", "tnsping", "ping", "nslookup",
           "tfactl", "crsctl", "srvctl", "olsnodes", "ls", "ggsci", "opatch",
           "dmesg", "uname",
           # Conferir se dois arquivos sao identicos e leitura pura, e e o
           # jeito de validar password file de Data Guard SEM saber a senha.
           # Faltavam aqui ate 2026-09-21.
           "md5sum", "sha256sum", "cksum", "diff", "stat", "wc",
           # Inventario de disco antes de mexer em ASM. Todos apenas informam:
           # scsi_id devolve o WWID, blkid a assinatura de filesystem, pvs/vgs/
           # lvs o inventario LVM. Conferir ANTES e o que evita rotular o disco
           # errado - por isso precisam caber num passo de leitura.
           "lsblk", "blkid", "pvs", "vgs", "lvs", "scsi_id", "lsscsi",
           # asmcmd cai no filtro de subcomando abaixo: 'asmcmd lsdg' le,
           # 'asmcmd dropdg' destroi um diskgroup inteiro.
           "asmcmd",
           # mkstore tambem cai no filtro de subcomando: '-listCredential'
           # responde "qual usuario roda o backup deste banco" sem expor
           # senha nenhuma, e e o primeiro passo de todo caso de wallet.
           "mkstore", ),
    "db2": ("db2pd", "db2level", "db2diag", "db2support", "db2 list",
            "db2 get", "db2 describe", "db2 connect"),
    "redis": ("info", "ping", "dbsize", "lastsave", "scan", "type", "ttl",
              "object", "command", "latency", "client", "config", "memory",
              "slowlog"),
}

# Ferramenta de SO com subcomando (adrci, lsnrctl, srvctl, ggsci) passa pelo
# verbo inicial mesmo fazendo 'lsnrctl stop'. Por isso o segundo filtro.
SUBCOMANDO_PROIBIDO = ("stop", "purge", "delete", "kill", "shutdown",
                       "reload", "rm ", "start")

# Subcomando que TORNA DESTRUTIVO um comando cujo verbo esta na lista branca.
# Este e o ponto mais perigoso de acrescentar plataforma: em Redis, CONFIG GET
# le e CONFIG SET escreve - mesmo verbo. Lista branca de SUBCOMANDO, e nao
# negra, pela mesma razao dos verbos: o que nao foi conferido nao passa.
SUBCOMANDO_LEITURA = {
    "client": ("list", "info", "getname", "no-evict", "id"),
    "config": ("get", "resetstat"),
    "memory": ("usage", "stats", "doctor"),
    "slowlog": ("get", "len"),
    "latency": ("latest", "history", "doctor"),
    "object": ("encoding", "freq", "idletime", "refcount", "help"),
}

# DBCC do SQL Server e o caso identico ao RESTORE do RMAN: mesmo verbo, um lado
# le e o outro mexe. DBCC SQLPERF(LOGSPACE) le o uso do log; DBCC SHRINKFILE
# encolhe o arquivo e fragmenta o indice inteiro. So o que foi conferido entra.
DBCC_LEITURA = ("sqlperf", "opentran", "inputbuffer", "show_statistics",
                "loginfo", "useroptions", "tracestatus", "sqlperf(logspace)")

# asmcmd e o caso mais perigoso do grupo 'so': o mesmo binario lista diskgroup
# ('asmcmd lsdg') e APAGA um ('asmcmd dropdg'). A lista negra de subcomando nao
# serve aqui - 'dropdg', 'mkdg', 'chdg', 'afd_label' e 'afd_unlabel' nao contem
# nenhuma das palavras de SUBCOMANDO_PROIBIDO e passariam como leitura.
# Lista branca, pela mesma razao do Redis: o que nao foi conferido nao passa.
ASMCMD_LEITURA = ("lsdg", "lsdsk", "lsattr", "lsop", "lsod", "lsct", "lsgrp",
                  "lsusr", "lspwusr", "ls", "du", "find", "pwd", "iostat",
                  "volinfo", "afd_lsdsk", "afd_dsget", "afd_state")

# mkstore e o mesmo caso do asmcmd, com um agravante: alem de ESCREVER
# credencial (-createCredential, -modifyCredential, -deleteCredential), ele
# tambem sabe IMPRIMIR o segredo em texto claro (-viewEntry). Um passo de
# leitura que exponha senha na tela e pior que um que escreve, porque a senha
# vai para o log da sessao, para o print no chamado e para o historico.
#
# Por isso a lista branca so tem o que LISTA NOME: -listCredential devolve
# alias + usuario (nunca a senha) e -listEntry devolve os nomes das entradas.
# '-wrl <dir>' e argumento obrigatorio, nao acao, e por isso e ignorado.
MKSTORE_LEITURA = ("-listcredential", "-listentry")


def _rman_read_only(stmt: str) -> bool:
    """No RMAN o verbo inicial nao basta.

    `RESTORE ... VALIDATE` le os backups e confere que a recuperacao e possivel,
    sem escrever nada - e o unico teste que prova que o backup presta. Mas o
    mesmo verbo SEM o VALIDATE (`RESTORE DATABASE`) SOBRESCREVE os datafiles.
    Por isso a excecao e condicionada a palavra, nao ao verbo.
    """
    verbo = stmt.split()[0].lower()
    if verbo in LEITURA_OK["rman"]:
        return True
    return verbo == "restore" and bool(re.search(r"\bvalidate\b", stmt, re.I))


def _redis_read_only(stmt: str) -> bool:
    """Em Redis o verbo sozinho mente: CONFIG GET le, CONFIG SET escreve."""
    partes = stmt.lower().replace("redis-cli", "", 1).split()
    if not partes:
        return False
    verbo = partes[0]
    if verbo not in LEITURA_OK["redis"]:
        return False
    permitidos = SUBCOMANDO_LEITURA.get(verbo)
    if not permitidos:
        return True
    return len(partes) > 1 and partes[1] in permitidos


def _asmcmd_read_only(stmt: str) -> bool:
    """asmcmd seguido de um subcomando conferido como somente leitura.

    Sem subcomando (`asmcmd` puro) abre o shell interativo, de onde se pode
    fazer qualquer coisa - reprova.
    """
    partes = stmt.split()
    if len(partes) < 2:
        return False
    return partes[1].lower() in ASMCMD_LEITURA


def _mkstore_read_only(stmt: str) -> bool:
    """mkstore cuja UNICA acao e listar nome de credencial ou de entrada.

    Sem acao nenhuma (`mkstore -wrl <dir>`) o comando abre modo interativo, de
    onde se cria e se apaga credencial - reprova, pela mesma razao do `asmcmd`
    puro.
    """
    acoes = [t.lower() for t in stmt.split()
             if t.startswith("-") and t.lower() != "-wrl"]
    return bool(acoes) and all(a in MKSTORE_LEITURA for a in acoes)


def _dbcc_read_only(stmt: str) -> bool:
    """DBCC seguido de um comando conferido como somente leitura."""
    m = re.match(r"\s*dbcc\s+([a-z_]+)", stmt, re.I)
    return bool(m) and m.group(1).lower() in DBCC_LEITURA


def read_only(stmt: str, onde: str, plataforma: str = "oracle") -> bool:
    """O passo marcado 'leitura' realmente so le, neste contexto?"""
    if not stmt.strip():
        return True
    if onde == "rman":
        return _rman_read_only(stmt)
    if onde == "redis":
        return _redis_read_only(stmt)
    verbo = stmt.split()[0].lower().replace("\\", "/").rsplit("/", 1)[-1]
    if onde == "sql":
        if plataforma == "mssql" and verbo == "dbcc":
            return _dbcc_read_only(stmt)
        return verbo.startswith(LEITURA_OK["sql"])
    if onde == "db2":
        alvo = stmt.lower().strip()
        if not any(alvo.startswith(v) for v in LEITURA_OK["db2"]):
            return False
        return not any(p in alvo for p in SUBCOMANDO_PROIBIDO)
    if onde == "so":
        # Ferramenta chamada com caminho ($ORACLE_HOME/OPatch/opatch, ./ggsci)
        # e a MESMA ferramenta: compare o executavel, nao o caminho.
        if not verbo.startswith(LEITURA_OK["so"]):
            return False
        if verbo == "asmcmd":
            return _asmcmd_read_only(stmt)
        if verbo == "mkstore":
            return _mkstore_read_only(stmt)
        return not any(p in stmt.lower() for p in SUBCOMANDO_PROIBIDO)
    return verbo.startswith(LEITURA_OK.get(onde, ()))


def _linhas_uteis(cmd: str) -> list:
    """Descarta comentario e o terminador '/' do SQL*Plus.

    So a linha que e EXATAMENTE '/' e terminador: descartar tudo que comeca com
    '/' engoliria continuacao de expressao ('/NULLIF(...)', '/1024/1024') e o
    statement chegaria truncado - foi assim que este validador gerou dois falsos
    positivos antes de ser corrigido.
    """
    out = []
    for ln in (cmd or "").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith(("--", "#")) or ln == "/":
            continue
        if ln.upper().startswith("RMAN>"):
            ln = ln[5:].strip()
        out.append(ln)
    return out


def separar(cmd: str, onde: str = "sql") -> list:
    """Statements de um passo, cortando por ';' FORA de string literal.

    Corte ingenuo parte `SELECT 'ALTER ... IMMEDIATE;' FROM ...` no meio do
    literal - SQL que gera comando e comum nos playbooks.

    Em CLI de LINHA (redis-cli, CLP do Db2, shell) o separador NAO e ';': cada
    linha e um comando, e juntar tudo numa string so faria o validador ler
    'INFO memory CLIENT LIST' como um comando unico chamado INFO - o CLIENT
    LIST passaria sem ninguem conferir.
    """
    if onde in ("redis", "db2", "so"):
        return _linhas_uteis(cmd)
    texto = " ".join(_linhas_uteis(cmd))
    out, atual, dentro = [], [], False
    for ch in texto:
        if ch == "'":
            dentro = not dentro
        if ch == ";" and not dentro:
            out.append("".join(atual))
            atual = []
        else:
            atual.append(ch)
    out.append("".join(atual))
    return [s.strip() for s in out if s.strip()]


def checar_estrutura(playbooks: list) -> list:
    """Problemas de conteudo que o loader nao pega sozinho."""
    problemas = list(kb.validar_tudo())
    for pb in playbooks:
        plat = kb.plataforma_de(pb)
        if not pb.get("fontes"):
            problemas.append(f"{pb['id']}: sem 'fontes' declaradas")
        passos = pb.get("passos") or []
        if passos and passos[0].get("risco") != "leitura":
            problemas.append(
                f"{pb['id']}: primeiro passo nao e de leitura "
                "(observacao read-only vem antes de agir)")
        for s in passos:
            if not s.get("olhar"):
                problemas.append(
                    f"{pb['id']} / {s.get('titulo','?')}: sem 'olhar' "
                    "(comando sem criterio de leitura vira ritual)")
            if s.get("risco") == "destrutivo" and not s.get("armadilha"):
                problemas.append(
                    f"{pb['id']} / {s.get('titulo','?')}: passo destrutivo "
                    "sem 'armadilha'")
            onde = s.get("onde", "sql")
            if s.get("risco") != "leitura" or onde not in LEITURA_OK:
                continue
            for stmt in separar(s.get("comando") or "", onde):
                if not read_only(stmt, onde, plat):
                    problemas.append(
                        f"{pb['id']} / {s.get('titulo','?')}: passo 'leitura' "
                        f"executa '{stmt.split()[0].lower()}' em {onde}"
                        + (f" ({plat})" if plat != kb.PLATAFORMA_PADRAO else ""))
    return problemas


def _substituicoes(cur) -> dict:
    """Valores plausiveis para as variaveis '&x' dos playbooks."""
    def um(sql, padrao):
        try:
            cur.execute(sql)
            linha = cur.fetchone()
            return linha[0] if linha and linha[0] is not None else padrao
        except Exception:
            return padrao

    return {
        "sql_id": um("SELECT sql_id FROM v$sql WHERE ROWNUM=1", "0000000000000"),
        "phv": str(um("SELECT plan_hash_value FROM v$sql "
                      "WHERE plan_hash_value<>0 AND ROWNUM=1", "0")),
        "sid": str(um("SELECT sid FROM v$session WHERE ROWNUM=1", "1")),
        "inst": "1",
        "tablespace": um("SELECT tablespace_name FROM dba_tablespaces "
                         "WHERE contents='PERMANENT' AND ROWNUM=1", "SYSTEM"),
        "tabela": um("SELECT table_name FROM dba_tables WHERE owner='SYS' "
                     "AND ROWNUM=1", "DUAL"),
        "file_num": "1",
        "block_num": "2",
        "undo_ts": um("SELECT UPPER(value) FROM v$parameter "
                      "WHERE name='undo_tablespace'", "UNDOTBS1"),
    }


def _usa_dblink(stmt: str) -> bool:
    """True se o comando consulta atraves de database link (`tabela@LINK`).

    Nao ha como validar isso contra um banco qualquer: o link do playbook e um
    nome de exemplo (MEU_LINK) e, mesmo com um link real, executar levantaria
    conexao com um TERCEIRO banco - o que a validacao read-only nao deve fazer
    sem o dono saber. Estes comandos ficam declarados como nao verificados, em
    vez de reprovados (o que empurraria para remover conteudo util) ou de
    silenciosamente ignorados (o que mentiria sobre a cobertura).

    Cuidado com falso positivo: '@' tambem aparece dentro de string literal e
    em e-mail. So conta '@' colado a um identificador, fora de aspas.
    """
    sem_literal = re.sub(r"'[^']*'", "''", stmt)
    return bool(re.search(r"\w@\w", sem_literal))


def marcar_verificado(pb_id: str, conn_name: str, kb_dir: str = "",
                      nao_verificados: int = 0) -> str:
    """Carimba no JSON do playbook que o SQL dele rodou contra banco real.

    Quem carimba e o teste, nao o autor. Se este campo pudesse ser escrito a
    mao, ele voltaria a ser uma promessa - e o objetivo dele e justamente ser
    a unica coisa da KB que nao e opiniao.
    """
    import json as _json

    base = kb_dir or kb.KB_DIR
    for nome in os.listdir(base):
        if not nome.endswith(".json"):
            continue
        caminho = os.path.join(base, nome)
        with open(caminho, encoding="utf-8") as f:
            pb = _json.load(f)
        if pb.get("id") != pb_id:
            continue
        # O NOME DO BANCO NAO ENTRA AQUI. A primeira versao gravava
        # `"banco": conn_name` e o teste TestNaoVaza reprovou na hora: `kb/` e
        # versionado e o BLUEPRINT, que e feito para sair da empresa, passou a
        # carregar o hostname dos 31 playbooks carimbados. E a mesma razao pela
        # qual servidores.txt e ambientes.json ficam fora do repositorio - so
        # que aqui o vazamento entrava por dentro de um arquivo que fica.
        # O nivel ja diz a CLASSE do ambiente, que e a informacao util; qual
        # servidor exatamente e inventario, e inventario nao viaja.
        pb["verificado"] = {
            "nivel": "homologacao",
            "em": dt.date.today().isoformat(),
        }
        # Um playbook pode ter passado em 12 consultas e ter 4 que o validador
        # NAO consegue executar (as que atravessam database link). Carimbar os
        # dois casos igual seria o mesmo exagero que este campo existe para
        # impedir - so que agora cometido pela ferramenta, que e pior, porque
        # ninguem desconfia de carimbo automatico.
        if nao_verificados:
            pb["verificado"]["nao_verificados"] = nao_verificados
        with open(caminho, "w", encoding="utf-8", newline="\n") as f:
            _json.dump(pb, f, ensure_ascii=False, indent=2)
            f.write("\n")
        return caminho
    return ""


def checar_sql(playbooks: list, conn_name: str) -> tuple:
    from mcp.server import _connect_readonly

    con, _ = _connect_readonly(conn_name, mod_senha.pedir())
    cur = con.cursor()
    subst = _substituicoes(cur)
    total, falhas, pulados = 0, [], []
    com_falha: set = set()
    testados: set = set()
    pulados_por_pb: dict = {}
    for pb in playbooks:
        # A conexao aqui e Oracle (oracledb). Rodar o T-SQL de um playbook de
        # SQL Server contra ela nao testaria nada - produziria uma lista de
        # ORA-00900 que parece defeito da KB e e so a plataforma errada.
        if kb.plataforma_de(pb) != kb.PLATAFORMA_PADRAO:
            continue
        for s in pb.get("passos") or []:
            if s.get("onde", "sql") != "sql" or s.get("risco") != "leitura":
                continue
            for stmt in separar(s.get("comando") or ""):
                if stmt.upper().startswith("SHOW "):
                    continue          # comando de SQL*Plus, nao SQL
                if _usa_dblink(stmt):
                    pulados.append((pb["id"], s.get("titulo", "?"), stmt[:80]))
                    pulados_por_pb[pb["id"]] = pulados_por_pb.get(pb["id"], 0) + 1
                    continue
                for k, v in subst.items():
                    stmt = stmt.replace(f"&{k}", str(v))
                total += 1
                testados.add(pb["id"])
                try:
                    cur.execute(stmt)
                    cur.fetchmany(2)
                except Exception as e:
                    com_falha.add(pb["id"])
                    falhas.append((pb["id"], s.get("titulo", "?"),
                                   str(e).splitlines()[0], stmt[:110]))
    con.close()
    limpos = [(pid, pulados_por_pb.get(pid, 0))
              for pid in sorted(testados - com_falha)]
    return total, falhas, pulados, limpos


def _cobertura() -> int:
    """Quanto da KB foi conferido contra banco real, por plataforma.

    Existe para a resposta honesta ficar visivel sem ninguem precisar abrir
    JSON: hoje nenhuma plataforma alem de Oracle tem como ser executada nesta
    maquina, e um numero na tela vale mais que um aviso no rodape.
    """
    por_plat: dict = {}
    for pb in kb.carregar():
        p = kb.plataforma_de(pb)
        v = kb.verificacao_de(pb)
        por_plat.setdefault(p, {}).setdefault(v, 0)
        por_plat[p][v] += 1
    print("=" * 74)
    print("  COBERTURA DE VERIFICACAO DA KB")
    print("=" * 74)
    print(f"\n  {'PLATAFORMA':<14} {'TOTAL':>5}  " + "  ".join(
        f"{k:>12}" for k in kb.VERIFICACAO))
    for plat, niveis in sorted(por_plat.items(), key=lambda x: -sum(x[1].values())):
        tot = sum(niveis.values())
        print(f"  {plat:<14} {tot:>5}  " + "  ".join(
            f"{niveis.get(k, 0):>12}" for k in kb.VERIFICACAO))
    print("\n  Legenda:")
    for k, d in kb.VERIFICACAO.items():
        print(f"    {k:<14} {d}")
    print("\n  Para promover de 'estrutura' para 'homologacao':")
    print("    VALIDAR_KB.py --conn <banco> --marcar")
    print("  Nao ha como promover a mao - o carimbo so sai se o SQL rodou.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Valida os playbooks da base de conhecimento (kb/)")
    ap.add_argument("--conn", help="conexao do repositorio para executar o SQL "
                                   "de leitura contra um banco REAL "
                                   "(use homologacao)")
    ap.add_argument("--playbook", help="valida so um playbook, pelo id")
    ap.add_argument("--marcar", action="store_true",
                    help="com --conn: carimba 'verificado: homologacao' nos "
                         "playbooks cujo SQL rodou sem falha (o unico jeito "
                         "de esse campo ser preenchido)")
    ap.add_argument("--cobertura", action="store_true",
                    help="quanto da KB foi conferido contra banco real")
    args = ap.parse_args()

    if args.cobertura:
        return _cobertura()

    playbooks = kb.carregar()
    if args.playbook:
        playbooks = [p for p in playbooks if p["id"] == args.playbook]
        if not playbooks:
            print(f"[ERRO] playbook '{args.playbook}' nao encontrado")
            return 2

    print("=" * 74)
    print(f"  VALIDACAO DA BASE DE CONHECIMENTO - {len(playbooks)} playbook(s)")
    print("=" * 74)

    passos = sum(len(p.get("passos") or []) for p in playbooks)
    por_risco = {r: 0 for r in kb.RISCOS}
    for p in playbooks:
        for s in p.get("passos") or []:
            por_risco[s.get("risco", "leitura")] = \
                por_risco.get(s.get("risco", "leitura"), 0) + 1
    print(f"\n  passos: {passos}  |  leitura: {por_risco['leitura']}  "
          f"|  mudanca: {por_risco['mudanca']}  "
          f"|  destrutivo: {por_risco['destrutivo']}")

    print("\n--- 1. estrutura (offline) ---")
    problemas = checar_estrutura(playbooks)
    if problemas:
        for p in problemas:
            print(f"  [FALHA] {p}")
    else:
        print("  OK - contrato respeitado em todos os passos")

    falhas_sql = []
    if args.conn:
        print(f"\n--- 2. SQL de leitura contra {args.conn} (read-only) ---")
        try:
            total, falhas_sql, pulados, limpos = checar_sql(playbooks, args.conn)
        except Exception as e:
            print(f"  [ERRO] nao foi possivel conectar: {e}")
            return 2
        if args.marcar and limpos:
            com_ressalva = 0
            for pid, n_pulados in limpos:
                marcar_verificado(pid, args.conn, nao_verificados=n_pulados)
                com_ressalva += 1 if n_pulados else 0
            print(f"  [marcado] {len(limpos)} playbook(s) agora declaram "
                  f"'verificado: homologacao' ({args.conn})")
            if com_ressalva:
                print(f"            {com_ressalva} deles com ressalva - tem "
                      "consulta que o validador nao consegue executar")
        print(f"  executados: {total}  |  OK: {total - len(falhas_sql)}  "
              f"|  falhas: {len(falhas_sql)}  "
              f"|  nao verificaveis: {len(pulados)}")
        for pid, titulo, erro, sql in falhas_sql:
            print(f"\n  [FALHA] {pid} / {titulo}")
            print(f"          {erro}")
            print(f"          {sql}...")
        # Declarados, nao escondidos: quem le o resultado precisa saber que
        # estes comandos NAO foram provados contra banco nenhum.
        if pulados:
            print("\n  Nao verificaveis (consultam via database link - exigiriam"
                  "\n  conectar num terceiro banco; revise a mao):")
            for pid, titulo, sql in pulados:
                print(f"    - {pid} / {titulo}: {sql}...")
    else:
        print("\n--- 2. SQL ao vivo: PULADO (use --conn <banco de homologacao>) ---")

    ok = not problemas and not falhas_sql
    print("\n" + "=" * 74)
    print("  RESULTADO: " + ("tudo certo" if ok else "ha o que corrigir"))
    print("=" * 74)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
