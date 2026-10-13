"""
Inventario de frota: o que sabemos sobre CADA ambiente, acumulado.

Por que existe: a deteccao de topologia (troubleshoot/topologia.py) ja e boa e
grava `topology.json` em cada caso - mas o conhecimento morre ali. Depois de 27
coletas, ninguem consegue responder "quantos CDB eu tenho?", "quais bancos
dividem host?" ou "esse servidor e RAC?" sem abrir caso por caso. Este modulo
agrega o que ja foi coletado numa visao de frota.

DUAS CAMADAS, e a separacao e o ponto central do desenho:

1. **detectado** - vem de `topology.json` + `meta.json`. E DERIVADO: pode ser
   jogado fora e regerado a qualquer momento. Nunca edite a mao.
2. **notas** - o que so uma pessoa sabe e nenhum SELECT descobre ("este host
   nao tem Grid Infrastructure", "o listener nao sobe sozinho apos reboot",
   "estes seis sao PDBs da mesma CDB"). SOBREVIVE a regeracao.

Sem a camada 2 o inventario seria um cache descartavel; sem a camada 1 seria
mais um arquivo que envelhece porque ninguem lembra de atualizar. A mesma
divisao que `servidores.txt` faz hoje com comentarios - aqui ela e explicita.

Ambiente que NUNCA foi coletado tambem cabe aqui (origem 'manual'): saber que
um servidor existe e que ele nao tem GI e informacao util mesmo sem conexao.
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os
import re

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAMINHO = os.path.join(BASE_DIR, "ambientes.json")
CASOS_DIR = os.path.join(BASE_DIR, "troubleshooting")

DESCRICAO = (
    "Inventario de frota. O bloco 'detectado' de cada ambiente e DERIVADO dos "
    "topology.json das coletas e e regerado por AMBIENTES.py --atualizar - nao "
    "edite a mao. O bloco 'notas' e conhecimento humano e sobrevive a "
    "regeracao: use AMBIENTES.py --nota."
)


# Criticidade por convencao de nome. Existe porque a conduta muda: em producao,
# acao destrutiva exige janela, aprovacao e rollback escrito; em homologacao o
# mesmo comando e rotina. Errar esse julgamento nas duas direcoes custa - tratar
# producao como homologacao causa incidente, e o inverso trava trabalho simples.
#
# Derivado do NOME, nunca gravado: o nome e a unica fonte que existe antes da
# primeira coleta, e a frota ja e batizada assim (sigla da aplicacao + marca de
# ambiente, tipo <APP>HO* para homologacao e <APP>PR* para producao, ou host
# com 'pro' no meio). Quando o nome nao disser nada, devolve '?' em vez de
# chutar - '?' faz alguem perguntar, um chute errado nao.
#
# PRODUCAO e testada PRIMEIRO, de proposito. O erro de classificacao tem custo
# ASSIMETRICO: tratar homologacao como producao custa uma janela desnecessaria;
# tratar producao como homologacao custa um incidente. Diante de um nome que
# case com as duas marcas, a resposta segura e a mais cautelosa.
#
# As marcas sao curtas e casam por SUBSTRING, entao cada uma precisa valer o
# risco de colidir. 'TS' e 'DES' foram tentadas e descartadas: aparecem dentro
# de nomes de producao ('DBTSPROD01', 'DESPACHO') e classificariam como
# homologacao um banco critico - exatamente o erro caro.
_MARCAS_PRODUCAO = ("PROD", "PRO", "PRD", "PR")
_MARCAS_HOMOLOG = ("HOMOLOG", "HOM", "HML", "HO", "QAS", "QA", "DEV", "TST")


# Convencao de hostname do parque LATAM, mais confiavel que marca por substring
# porque le POSICAO em vez de procurar pedaco de texto:
#
#     db  aap   p v l   br   04
#     ||  |||   | | |   ||   ||
#     ||  |||   | | |   ||   +-- sequencial
#     ||  |||   | | |   +------- pais: br, cl, ar, mx, uy, py
#     ||  |||   | | +----------- SO: l = linux
#     ||  |||   | +------------- 'v' ou 'a' (nao mapeado - ver abaixo)
#     ||  |||   +--------------- AMBIENTE: p = producao, h = homologacao
#     ||  +--------------------- sigla da aplicacao
#     +------------------------- db
#
# Validado contra os 28 ambientes do inventario em 23/09/2026: dos 21 hosts cuja
# criticidade era conhecida pelo nome do banco, a 1a letra do trio acertou 21 -
# 'h' em 17 hosts terminados em hal e 4 em hvl, 'p' no host em pal.
#
# O que NAO foi mapeado de proposito, por falta de evidencia:
#   - 2a letra ('a' x 'v'): aparece nas duas criticidades (hal e hvl, pal e pvl),
#     logo NAO e o ambiente. Sem amostra suficiente para afirmar o significado.
#   - 'i' na 1a posicao: os 3 casos observados sao nao-producao, mas 3 amostras
#     nao bastam para cravar. Cai para as marcas genericas, que ja classificam
#     esses ambientes corretamente pelo nome do banco.
# Mapear letra sem evidencia seria trocar um '?' honesto por um chute com cara
# de regra.
_HOST_LATAM = re.compile(
    r"^db.+?([phi])[av]l(br|cl|ar|mx|uy|py)\d+$", re.IGNORECASE)
_AMBIENTE_POR_LETRA = {"p": "PRODUCAO", "h": "HOMOLOGACAO"}


def _por_convencao_host(hosts) -> str:
    """Criticidade pela POSICAO na convencao de hostname LATAM, ou ''.

    Cada entrada e quebrada em PEDACOS antes de casar, porque host raramente
    chega limpo. O `hostname` de um caso real e uma linha de identificacao
    montada por gente: `dbaapvlbr04:55000:BANCO (usuario)`. A expressao e
    ancorada no fim (`\\d+$`) de proposito - ler posicao exige saber onde a
    string termina - e sem quebrar em pedacos ela nao casava nada disso,
    devolvendo '?' para um host cuja convencao dizia PRODUCAO com clareza.
    Achado em 24/09/2026 testando a criticidade contra os casos ja gravados.
    """
    for h in hosts or ():
        for pedaco in re.split(r"[^A-Za-z0-9]+", str(h or "")):
            m = _HOST_LATAM.match(pedaco)
            if m:
                achado = _AMBIENTE_POR_LETRA.get(m.group(1).lower(), "")
                if achado:
                    return achado
    return ""


VALORES_CRITICIDADE = ("PRODUCAO", "HOMOLOGACAO")


def criticidade(nome: str, hosts=(), confirmada: str = "") -> str:
    """'PRODUCAO', 'HOMOLOGACAO' ou '?' a partir do nome do ambiente/host.

    A convencao de hostname vem PRIMEIRO: ler posicao e mais confiavel que
    procurar substring, que casa por acidente no meio de qualquer palavra.

    `confirmada` e resposta HUMANA e entra so quando o nome nao revela nada.
    Existe porque a derivacao pelo nome falha em parte do parque e a resposta se
    perdia no chat: entre 25 e 28/09/2026 o usuario informou verbalmente a
    criticidade de tres ambientes (SCDXX producao Chile, CDB6D3
    desenvolvimento, RRHH producao Chile) e os tres continuavam '?' no
    inventario na sessao seguinte.

    Ordem deliberada - o nome vence a confirmacao quando diz algo:
      1. convencao posicional do host;
      2. marca no nome;
      3. confirmacao humana, so para o que sobrou em '?'.

    O motivo de nao deixar a confirmacao sobrescrever o nome e envelhecimento:
    nome nao muda sozinho, anotacao sim. Se as duas discordarem, quem chama
    avisa e mantem o lado cauteloso - errar para PRODUCAO custa uma janela,
    o inverso custa incidente.
    """
    por_host = _por_convencao_host(hosts)
    if por_host:
        return por_host
    alvo = " ".join([str(nome or "")] + [str(h) for h in (hosts or [])]).upper()
    if any(m in alvo for m in _MARCAS_PRODUCAO):
        return "PRODUCAO"
    if any(m in alvo for m in _MARCAS_HOMOLOG):
        return "HOMOLOGACAO"
    conf = (confirmada or "").strip().upper()
    if conf in VALORES_CRITICIDADE:
        return conf
    return "?"


def confirmar_criticidade(nome: str, valor: str, path: str = "") -> dict:
    """Grava a criticidade que um HUMANO informou, com data e marca de origem.

    Nao mexe em `detectado`: confirmacao humana nao e dado coletado, e misturar
    as duas coisas e como o inventario perde credibilidade.
    """
    chave = (nome or "").strip().upper()
    valor = (valor or "").strip().upper()
    if not chave:
        raise ValueError("nome do ambiente vazio")
    if valor not in VALORES_CRITICIDADE:
        raise ValueError(f"criticidade invalida: {valor!r} "
                         f"(use {' ou '.join(VALORES_CRITICIDADE)})")
    inv = ler(path)
    ent = inv["ambientes"].get(chave) or {"origem": "manual", "notas": []}
    ent.setdefault("notas", [])
    ent["criticidade_confirmada"] = {"valor": valor, "em": _agora()}
    inv["ambientes"][chave] = ent
    return inv


def criticidade_confirmada(inv: dict, nome: str = "", hosts=()) -> str:
    """O valor confirmado, procurando por NOME do ambiente e por HOST.

    Os dois caminhos existem porque caso conduzido por chat nao tem `conn`: ele
    grava o hostname que o humano digitou, e esse hostname raramente e o nome do
    ambiente. O `RRHH` foi cadastrado com host `pdptrrhhbd01.corporativo.cl.corp`
    e o caso dele guarda exatamente esse host - sem a busca por host, a
    confirmacao existiria no inventario e nao alcancaria o caso.

    A comparacao ignora o dominio e QUEBRA A STRING EM PEDACOS: o inventario
    pode ter `psifdcidb11` e o caso `psifdcidb11.corporativo.cl.corp`, e sao o
    mesmo servidor. Pior, o `hostname` de um caso e escrito por gente e vem
    composto - `lxdesaora5 / CDB6D3` ou `host:porta:BANCO (usuario)`. Comparar a
    string inteira falha em todos esses, e foi exatamente o que aconteceu na
    primeira versao: o ambiente confirmado existia e o caso continuava '?'.
    """
    ambientes = inv.get("ambientes") or {}

    def _valor(ent):
        return ((ent or {}).get("criticidade_confirmada") or {}).get("valor") or ""

    def _pedacos(valor) -> set:
        bruto = re.split(r"[^A-Za-z0-9]+", str(valor or "").lower())
        return {p for p in bruto if p}

    achado = _valor(ambientes.get((nome or "").strip().upper()))
    if achado:
        return achado
    alvo = set()
    for h in (hosts or []):
        alvo |= _pedacos(h)
    alvo |= _pedacos(nome)
    if not alvo:
        return ""
    for chave, ent in ambientes.items():
        # O nome do ambiente tambem conta: o caso pode citar 'CDB6D3' dentro do
        # hostname composto sem que o host curto bata.
        if _pedacos(ent.get("host")) & alvo or _pedacos(chave) & alvo:
            achado = _valor(ent)
            if achado:
                return achado
    return ""


def _agora() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


def _json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _chave(meta: dict, topo: dict) -> str:
    """Identidade do ambiente.

    Preferimos o nome da CONEXAO, e nao o db_name, porque e por ele que o DBA
    chama o banco no dia a dia e nos demais arquivos do projeto
    (stringConexao/, servidores.txt, coletar.bat). Quando nao ha conexao - caso
    coletado no Citrix, por exemplo - cai para o db_name.
    """
    return str(meta.get("conn") or topo.get("db_name") or "").strip().upper()


def _registro(topo: dict, meta: dict, caso: str) -> dict:
    inst = topo.get("instancias") or []
    return {
        "conn": meta.get("conn") or "",
        "ambiente": meta.get("perfil") or "",
        "db_name": topo.get("db_name") or "",
        "kind": topo.get("kind") or "",
        "perfil": topo.get("perfil") or "",
        "versao": topo.get("versao") or "",
        "papel": topo.get("db_role") or "",
        "log_mode": topo.get("log_mode") or "",
        "open_mode": topo.get("open_mode") or "",
        "nos_no_ar": topo.get("nos_no_ar"),
        "nos_configurados": topo.get("nos_configurados"),
        "pdbs": topo.get("pdbs"),
        "tags": list(topo.get("tags") or []),
        "hosts": sorted({i.get("HOST_NAME", "") for i in inst if i.get("HOST_NAME")}),
        "instancias": sorted({i.get("INSTANCE_NAME", "") for i in inst
                              if i.get("INSTANCE_NAME")}),
        "visto_em": meta.get("collected_at") or "",
        "casos": [caso],
    }


def agregar(casos_dir: str = "") -> dict:
    """Le todos os topology.json e devolve {chave: registro detectado}.

    Quando o mesmo ambiente aparece em varios casos, vence a coleta MAIS
    RECENTE (a topologia muda: banco vira standby, no entra no cluster), mas a
    lista de casos acumula - e ela que permite voltar a evidencia.
    """
    raiz = casos_dir or CASOS_DIR
    out: dict = {}
    for tpath in sorted(glob.glob(os.path.join(raiz, "*", "topology.json"))):
        cdir = os.path.dirname(tpath)
        caso = os.path.basename(cdir)
        topo = _json(tpath)
        if not topo:
            continue
        meta = _json(os.path.join(cdir, "meta.json"))
        chave = _chave(meta, topo)
        if not chave:
            continue
        novo = _registro(topo, meta, caso)
        atual = out.get(chave)
        if not atual:
            out[chave] = novo
            continue
        casos = sorted(set(atual["casos"]) | set(novo["casos"]))
        if (novo["visto_em"] or "") >= (atual["visto_em"] or ""):
            atual = novo
        atual["casos"] = casos
        out[chave] = atual
    return out


def ler(path: str = "") -> dict:
    d = _json(path or CAMINHO)
    if "ambientes" not in d:
        d = {"versao": 1, "descricao": DESCRICAO, "gerado_em": "", "ambientes": {}}
    return d


def gravar(d: dict, path: str = "") -> str:
    destino = path or CAMINHO
    d["descricao"] = DESCRICAO
    d["gerado_em"] = _agora()
    d["ambientes"] = dict(sorted(d.get("ambientes", {}).items()))
    with open(destino, "w", encoding="utf-8", newline="\n") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return destino


def atualizar(path: str = "", casos_dir: str = "") -> tuple:
    """Regera o bloco 'detectado' preservando 'notas' e entradas manuais.

    Retorna (inventario, novos, atualizados).
    """
    inv = ler(path)
    antigos = inv.get("ambientes", {})
    detectado = agregar(casos_dir)
    novos, atualizados = [], []
    for chave, reg in detectado.items():
        anterior = antigos.get(chave) or {}
        if anterior.get("detectado"):
            atualizados.append(chave)
        else:
            novos.append(chave)
        antigos[chave] = {
            "origem": "coleta",
            "notas": anterior.get("notas") or [],
            "detectado": reg,
        }
    inv["ambientes"] = antigos
    return inv, novos, atualizados


def registrar_manual(nome: str, path: str = "", **campos) -> dict:
    """Ambiente conhecido sem coleta (sem acesso, ou a conclusao foi do SO).

    Nao inventa bloco 'detectado': o que se sabe entra como nota, e a origem
    fica marcada para ninguem confundir observacao humana com dado coletado.
    """
    chave = (nome or "").strip().upper()
    if not chave:
        raise ValueError("nome do ambiente vazio")
    inv = ler(path)
    ent = inv["ambientes"].get(chave) or {"origem": "manual", "notas": []}
    ent.setdefault("notas", [])
    ent.setdefault("origem", "manual")
    for k, v in campos.items():
        if v:
            ent[k] = v
    inv["ambientes"][chave] = ent
    return inv


def anotar(nome: str, texto: str, path: str = "") -> dict:
    chave = (nome or "").strip().upper()
    texto = (texto or "").strip()
    if not texto:
        raise ValueError("nota vazia")
    inv = ler(path)
    if chave not in inv["ambientes"]:
        inv["ambientes"][chave] = {"origem": "manual", "notas": []}
    ent = inv["ambientes"][chave]
    ent.setdefault("notas", []).append({"em": _agora(), "texto": texto})
    return inv


def hosts_compartilhados(inv: dict) -> dict:
    """host -> [ambientes], apenas onde ha mais de um banco no mesmo host.

    Importa para nao contar duas vezes: metrica de HOST (CPU, memoria, IO) e
    comum aos bancos que dividem o servidor. Tratar como dois sinais
    independentes de infraestrutura infla qualquer placar de frota - o mesmo
    cuidado que servidores.txt ja documenta a mao para os pares conhecidos.
    """
    por_host: dict = {}
    for chave, ent in (inv.get("ambientes") or {}).items():
        for h in ((ent.get("detectado") or {}).get("hosts") or []):
            por_host.setdefault(h, set()).add(chave)
    return {h: sorted(v) for h, v in sorted(por_host.items()) if len(v) > 1}


def resumo(inv: dict) -> dict:
    """Contagens de frota para a linha de comando e para o relatorio."""
    ambientes = inv.get("ambientes") or {}
    por_kind: dict = {}
    por_versao: dict = {}
    por_tag: dict = {}
    sem_coleta = []
    for chave, ent in ambientes.items():
        det = ent.get("detectado")
        if not det:
            sem_coleta.append(chave)
            continue
        por_kind[det.get("kind") or "?"] = por_kind.get(det.get("kind") or "?", 0) + 1
        por_versao[det.get("versao") or "?"] = por_versao.get(det.get("versao") or "?", 0) + 1
        for t in det.get("tags") or []:
            por_tag[t] = por_tag.get(t, 0) + 1
    return {
        "total": len(ambientes),
        "com_coleta": len(ambientes) - len(sem_coleta),
        "sem_coleta": sorted(sem_coleta),
        "por_kind": dict(sorted(por_kind.items(), key=lambda x: -x[1])),
        "por_versao": dict(sorted(por_versao.items(), key=lambda x: -x[1])),
        "por_tag": dict(sorted(por_tag.items(), key=lambda x: -x[1])),
        "hosts_compartilhados": hosts_compartilhados(inv),
    }
