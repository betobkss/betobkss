"""
Deteccao de topologia do ambiente (fase 1 da coleta).

Antes de decidir QUAIS checks rodar e COMO interpretar os numeros, e preciso
saber onde estamos. O mesmo dado significa coisas opostas em ambientes
diferentes:

- "1 instancia no ar" e NORMAL em standalone e em RAC One Node, e CRITICO num
  RAC de 3 nos (dois cairam).
- "avg_read_ms = 8" e aceitavel em disco comum e RUIM em Exadata (flash cache
  deveria entregar < 1 ms).
- Eventos 'gc %' (global cache) nao existem fora de RAC - coletar isso em
  standalone so gera tabela vazia no relatorio.
- Lag de apply importa em Data Guard e em GoldenGate, que sao coisas distintas.

Tudo aqui e READ-ONLY: apenas SELECT em v$/gv$/dicionario, cada probe isolado
em try/except para que uma view ausente (versao antiga, sem privilegio, sem
Exadata) nunca derrube a coleta.
"""
from __future__ import annotations

# ── Probes ────────────────────────────────────────────────────────
# (chave, sql). Cada uma retorna UMA linha/valor. Ausencia = None.
# database_type: quando EXISTE, e a unica fonte que separa RAC de RAC One Node
# ('SINGLE' | 'RAC' | 'RACONENODE'). Verificado no DBSRV039 (19.0.0.0.0): a
# coluna NAO existe em v$database - as outras colunas da mesma view retornaram
# normalmente, logo nao e falta de privilegio. Fica no catalogo de probes porque
# custa nada e desambigua onde estiver disponivel; a logica de _derivar nao
# depende dela.
PROBES_ESCALARES = [
    ("db_name", "SELECT name FROM v$database"),
    ("db_role", "SELECT database_role FROM v$database"),
    ("log_mode", "SELECT log_mode FROM v$database"),
    ("open_mode", "SELECT open_mode FROM v$database"),
    ("database_type", "SELECT database_type FROM v$database"),
    ("cdb", "SELECT cdb FROM v$database"),
    ("versao", "SELECT version FROM v$instance"),
    ("cluster_database",
     "SELECT UPPER(value) FROM v$parameter WHERE name = 'cluster_database'"),
    ("cluster_instances",
     "SELECT value FROM v$parameter WHERE name = 'cluster_database_instances'"),
    ("gg_replicacao",
     "SELECT UPPER(value) FROM v$parameter WHERE name = 'enable_goldengate_replication'"),
    ("nos_no_ar", "SELECT COUNT(*) FROM gv$instance"),
    ("pdbs", "SELECT COUNT(*) FROM v$pdbs"),
    ("asm_diskgroups", "SELECT COUNT(*) FROM v$asm_diskgroup"),
    # Exadata: as celulas de storage aparecem em v$cell_config. Fora de Exadata
    # a view existe mas nao retorna linhas - por isso contamos em vez de
    # confiar na existencia da view.
    ("celulas", "SELECT COUNT(*) FROM v$cell_config"),
    # GoldenGate integrado (e XStream) usa capture/apply do dicionario.
    ("gg_capture", "SELECT COUNT(*) FROM dba_capture"),
    ("gg_apply", "SELECT COUNT(*) FROM dba_apply"),
    # Standby DE VERDADE: destino remoto configurado e ativo. Nao confundir com
    # os processos ARCH locais, que aparecem em gv$managed_standby em qualquer
    # banco em ARCHIVELOG e nao significam Data Guard.
    ("destinos_standby",
     "SELECT COUNT(*) FROM v$archive_dest_status "
     "WHERE target = 'STANDBY' AND status NOT IN ('INACTIVE','UNKNOWN')"),
]

# Probe alternativo para Exadata em versoes onde v$cell_config nao existe.
PROBE_CELULAS_ALT = "SELECT COUNT(*) FROM v$cell"

# Tabela de instancias (evidencia e base do check de nos esperados vs no ar)
PROBE_INSTANCIAS = (
    "SELECT inst_id, instance_name, host_name, status, "
    "TO_CHAR(startup_time,'YYYY-MM-DD HH24:MI') AS startup "
    "FROM gv$instance ORDER BY inst_id"
)


def _escalar(cur, sql):
    """Executa um probe e devolve o 1o valor da 1a linha, ou None se falhar.

    Falha silenciosa e intencional: view inexistente na versao, sem privilegio
    ou sem o componente (ASM/Exadata) nao e erro - e informacao de topologia.
    """
    try:
        cur.execute(sql)
        row = cur.fetchone()
    except Exception:
        return None
    if not row:
        return None
    return row[0]


def _int(v, default=None):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


def detect_topology(cur) -> dict:
    """Descobre a topologia do banco conectado. Nunca levanta excecao.

    Retorna um dict com os fatos crus (db_name, versao, nos_no_ar...) mais os
    campos derivados usados nas decisoes: 'kind', 'tags' e 'perfil'.
    """
    fatos: dict = {}
    for chave, sql in PROBES_ESCALARES:
        fatos[chave] = _escalar(cur, sql)

    # Exadata: fallback se v$cell_config nao existir nesta versao
    if fatos.get("celulas") is None:
        fatos["celulas"] = _escalar(cur, PROBE_CELULAS_ALT)

    instancias = []
    try:
        cur.execute(PROBE_INSTANCIAS)
        cols = [d[0] for d in cur.description]
        for row in cur.fetchall():
            instancias.append(dict(zip(
                cols, ["" if v is None else str(v) for v in row], strict=True)))
    except Exception:
        pass
    fatos["instancias"] = instancias

    return _derivar(fatos)


def _derivar(fatos: dict) -> dict:
    """Transforma os fatos crus em kind/tags/perfil."""
    t = dict(fatos)

    nos_no_ar = _int(t.get("nos_no_ar")) or len(t.get("instancias") or []) or 1
    nos_cfg = _int(t.get("cluster_instances"))
    clusterizado = str(t.get("cluster_database") or "").upper() == "TRUE"

    # kind: usa database_type se a versao expuser; senao decide por
    # cluster_database + numero de instancias no ar.
    dbtype = str(t.get("database_type") or "").upper().replace(" ", "")
    if dbtype in ("SINGLE", "RAC", "RACONENODE"):
        kind = dbtype
    elif not clusterizado:
        kind = "SINGLE"
    elif nos_no_ar >= 2:
        # Duas instancias ATIVAS no mesmo banco so acontecem em RAC real. RAC
        # One Node roda uma instancia por vez (as duas coexistem apenas nos
        # poucos minutos de uma relocacao online).
        kind = "RAC"
    else:
        # Clusterizado com UMA instancia no ar e genuinamente ambiguo: pode ser
        # RAC One Node operando normalmente ou um RAC com nos caidos. Nao ha
        # como decidir por SQL - o tipo e atributo do Grid Infrastructure
        # ('srvctl config database' -> Type: RACOneNode). Assumir 'RAC' aqui
        # geraria alarme de 'no caido' em todo RAC One Node do parque; assumir
        # 'RAC One Node' esconderia um cluster degradado. Registra a duvida em
        # vez de escolher errado.
        kind = "CLUSTER_1NO"
        t["kind_incerto"] = True

    celulas = _int(t.get("celulas"), 0) or 0
    dgs = _int(t.get("asm_diskgroups"), 0) or 0
    capture = _int(t.get("gg_capture"), 0) or 0
    apply_ = _int(t.get("gg_apply"), 0) or 0
    gg_param = str(t.get("gg_replicacao") or "").upper() == "TRUE"
    standbys = _int(t.get("destinos_standby"), 0) or 0
    pdbs = _int(t.get("pdbs"), 0) or 0
    papel = str(t.get("db_role") or "").upper()

    tags = set()
    if kind in ("RAC", "RACONENODE", "CLUSTER_1NO"):
        tags.add("cluster")
    if kind in ("RAC", "CLUSTER_1NO"):
        # No caso ambiguo entra 'rac' de proposito: coletar uma tabela de global
        # cache vazia custa pouco, deixar de diagnosticar um RAC degradado custa
        # caro. A assimetria de custo decide.
        tags.add("rac")
    if kind == "RACONENODE":
        tags.add("raconenode")
    if celulas > 0:
        tags.add("exadata")
    if dgs > 0:
        tags.add("asm")
    # A tag que habilita os checks de capture/apply exige os OBJETOS existindo.
    # ENABLE_GOLDENGATE_REPLICATION=TRUE significa apenas que o banco esta
    # PREPARADO para replicar - no DBSRV039 o parametro e TRUE, ha tablespace
    # GGS_DATA, e dba_capture/dba_apply estao vazias (GG classico, ou integrado
    # dentro de uma PDB). Ligar os checks pelo parametro rendia tres tabelas
    # vazias no relatorio e zero informacao.
    if capture > 0 or apply_ > 0:
        tags.add("goldengate")
    elif gg_param:
        tags.add("goldengate_preparado")
    # Data Guard: destino standby ativo (no primary) ou o proprio banco e standby
    if standbys > 0 or "STANDBY" in papel:
        tags.add("dataguard")
    if str(t.get("cdb") or "").upper() == "YES" or pdbs > 0:
        tags.add("cdb")
    if str(t.get("log_mode") or "").upper() == "ARCHIVELOG":
        tags.add("archivelog")

    t["kind"] = kind
    t["nos_no_ar"] = nos_no_ar
    t["nos_configurados"] = nos_cfg
    t["celulas"] = celulas
    t["tags"] = sorted(tags)
    t["perfil"] = descrever(t)
    return t


def descrever(t: dict) -> str:
    """Frase curta de identificacao do ambiente, para log e cabecalho."""
    kind = t.get("kind", "SINGLE")
    nos = t.get("nos_no_ar") or 1
    cfg = t.get("nos_configurados")
    if kind == "RAC":
        base = f"RAC {nos} no(s) no ar"
        if cfg and cfg != nos:
            base += f" de {cfg} configurado(s)"
    elif kind == "RACONENODE":
        base = "RAC One Node"
    elif kind == "CLUSTER_1NO":
        base = "Cluster com 1 no no ar (RAC One Node ou RAC degradado)"
        if cfg:
            base += f" de {cfg} configurado(s)"
    else:
        base = "Standalone"

    extras = []
    if "exadata" in (t.get("tags") or []):
        extras.append(f"Exadata {t.get('celulas')} cell(s)")
    if "asm" in (t.get("tags") or []):
        extras.append("ASM")
    if "cdb" in (t.get("tags") or []):
        pdbs = _int(t.get("pdbs"), 0) or 0
        extras.append(f"CDB {pdbs} PDB(s)" if pdbs else "CDB")
    if "dataguard" in (t.get("tags") or []):
        extras.append("Data Guard")
    if "goldengate" in (t.get("tags") or []):
        extras.append("GoldenGate")
    elif "goldengate_preparado" in (t.get("tags") or []):
        extras.append("GoldenGate habilitado (sem capture/apply na raiz)")
    papel = str(t.get("db_role") or "").upper()
    if papel and papel != "PRIMARY":
        extras.append(papel.title())

    ver = str(t.get("versao") or "")
    if ver:
        extras.append(ver)
    return base + (" (" + ", ".join(extras) + ")" if extras else "")


def check_permitido(check: dict, topo: dict | None) -> bool:
    """Decide se um check do catalogo se aplica a esta topologia.

    O campo 'requires_topology' do catalogo lista as tags necessarias (basta
    UMA casar). FAIL-OPEN deliberado: sem topologia detectada, roda tudo - e
    melhor uma tabela vazia no relatorio do que um check silenciosamente
    omitido por falha na deteccao.
    """
    exigidas = check.get("requires_topology")
    if not exigidas:
        return True
    if not topo or not topo.get("tags"):
        return True
    if isinstance(exigidas, str):
        exigidas = [exigidas]
    return bool(set(exigidas) & set(topo.get("tags") or []))


def render_topologia(t: dict) -> str:
    """Monta topology.txt no formato de largura fixa que os analisadores leem."""
    from .collector import render_table

    linhas = [
        ["Perfil", t.get("perfil", "")],
        ["Tipo", t.get("kind", "")],
        ["Banco", str(t.get("db_name") or "")],
        ["Versao", str(t.get("versao") or "")],
        ["Papel", str(t.get("db_role") or "")],
        ["Log mode", str(t.get("log_mode") or "")],
        ["Open mode", str(t.get("open_mode") or "")],
        ["Nos no ar", str(t.get("nos_no_ar") or "")],
        ["Nos configurados", str(t.get("nos_configurados") or "-")],
        ["Celulas Exadata", str(t.get("celulas") or 0)],
        ["Diskgroups ASM", str(t.get("asm_diskgroups") or 0)],
        ["PDBs", str(t.get("pdbs") or 0)],
        ["Destinos standby", str(t.get("destinos_standby") or 0)],
        ["GoldenGate capture", str(t.get("gg_capture") or 0)],
        ["GoldenGate apply", str(t.get("gg_apply") or 0)],
        ["Tags", ", ".join(t.get("tags") or [])],
    ]
    partes = ["=== TOPOLOGIA DO AMBIENTE ===\n"
              + render_table(["ITEM", "VALOR"], linhas)]

    inst = t.get("instancias") or []
    if inst:
        cols = ["INST_ID", "INSTANCE_NAME", "HOST_NAME", "STATUS", "STARTUP"]
        rows = [[i.get(c, "") for c in cols] for i in inst]
        partes.append("=== INSTANCIAS ===\n" + render_table(cols, rows))
    return "\n\n".join(partes) + "\n"
