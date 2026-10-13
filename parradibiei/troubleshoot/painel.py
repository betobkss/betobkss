"""
Painel local (GUI) do framework de troubleshooting.

Por que existe: hoje todo o valor do framework esta atras de um terminal com
flags (--conn, --pack, --rapido). Isso e barreira de entrada para outro DBA e
fonte de erro de digitacao. O painel abre uma tela local e dispara os MESMOS
comandos.

Decisao de desenho - CAMADA FINA, sem regra de negocio: o painel monta a linha
de comando e chama `COLETAR.py`/`TROUBLESHOOT.py` como subprocesso, ou chama as
funcoes de `troubleshoot.caso`. Nada de coleta, severidade ou ciclo de vida e
reimplementado aqui. Se fosse reimplementado, existiriam duas verdades e os
testes do nucleo deixariam de proteger o caminho que o usuario percorre.

Seguranca:
- escuta so em 127.0.0.1 (loopback), nunca na rede;
- a senha e digitada na caixa mascarada QUANDO o comando pede, e vai para o
  subprocesso pelo STDIN (protocolo `dialogo`): nunca em argv (que aparece na
  lista de processos), nunca em variavel de ambiente e nunca em disco. O painel
  nao guarda a senha entre execucoes - ver `troubleshoot/senha.py`;
- `--advisor` e proibido: e o unico comando que escreve no banco, continua
  exigindo terminal e confirmacao explicita (ver `_PROIBIDOS`);
- nome de caso e validado por regex - impede path traversal e injecao de flag;
- uma coleta por vez, para nao castigar o banco com execucoes paralelas.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import caso as caso_lifecycle
from . import (
    conn_repo,
    copiloto,
    destaques,
    dialogo,
    guia,
    kb,
    marca,
    raciocinio,
    reincidencia,
    saida,
)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(BASE_DIR, "output", "troubleshooting")

# Aceita INC000987654, HC_DBSRV600, teste-1. Barra, espaco e '-' inicial ficam
# de fora: seriam path traversal ou uma flag disfarcada de nome de caso.
_CASO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,59}$")

# Nomes de dispositivo do DOS, que o Windows reserva ate hoje em QUALQUER pasta.
# Um caso chamado NUL passa em toda validacao, deixa a coleta rodar inteira - com
# senha digitada e banco de producao lido - e some na hora de gravar: o SO trata
# 'NUL\meta.json' como o dispositivo nulo, nao como arquivo. A evidencia nao
# volta e nao ha erro obvio para explicar o porque. Nenhum deles e nome legitimo
# de chamado, entao a recusa e barata e vale para a familia inteira.
_RESERVADOS_WINDOWS = frozenset(
    ["con", "prn", "aux", "nul"]
    + [f"com{i}" for i in range(1, 10)]
    + [f"lpt{i}" for i in range(1, 10)])

# Modos oferecidos na tela. O painel NAO expoe --pack T: subir licenca de pack e
# decisao consciente, feita no terminal.
MODOS = {
    "completo": ["--completo"],
    "rapido": ["--rapido", "--completo"],
    "parcial": ["--parcial", "--completo"],
}

# Nunca podem entrar num comando disparado pela tela.
_PROIBIDOS = ("--advisor",)

MAX_LINHAS = 4000
MAX_JOBS = 10  # historico de execucoes desta sessao

# Historico que SOBREVIVE ao fechar o painel. So metadados: comando, caso, hora
# e codigo de saida. O log fica de fora de proposito - ele carrega saida de
# banco de producao, e um arquivo desses acumulando no disco por meses e um
# vazamento esperando acontecer. Quem quer a saida tem a pasta do caso.
HIST_PATH = os.path.join(OUTPUT_DIR, "painel_execucoes.jsonl")
MAX_HIST_PERSISTIDO = 200
MAX_SUGESTOES_KB = 5  # teto da lista de playbooks sugeridos (crit sempre passa)

# SQL_ID do Oracle: 13 caracteres base32 (digitos + minusculas).
_SQLID_RE = re.compile(r"^[0-9a-z]{13}$")
# 'YYYY-MM-DD HH:MI', o formato que JANELA.py espera.
_QUANDO_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
# &parametro de um passo guiado: nome de variavel de substituicao do SQL*Plus.
_PARAM_NOME_RE = re.compile(r"^[A-Za-z_][A-Za-z_0-9]{0,29}$")
# Valor de &parametro. Nao pode comecar com '-' (o argparse leria como flag) e
# nao aceita espaco nem aspa - o mesmo conjunto do `executor._VALOR_OK`, que o
# confere de novo antes de interpolar no SQL. Duas checagens porque sao dois
# riscos diferentes: aqui, injecao de FLAG; la, injecao na QUERY.
_PARAM_VALOR_RE = re.compile(r"^[A-Za-z0-9_$#.@][A-Za-z0-9_$#.@-]{0,59}$")

# O que a analise produz por caso. Lista FECHADA de proposito: o navegador
# manda a chave, nunca um caminho - assim nao existe entrada por onde pedir
# 'abra C:\...\senhas.txt'.
ARTEFATOS = [
    ("dashboard", "Dashboard", "{caso}.html",
     "achados, evidencia e Health Score - abre sem internet e vai anexado no chamado"),
    ("historico", "Historico", "{caso}_historico.html",
     "evolucao do score, o que mudou desde a ultima coleta e a linha do tempo"),
    ("pptx", "Apresentacao", "{caso}.pptx",
     "o caso em slides, para quando o incidente sobe de nivel"),
    ("relatorio", "Relatorio", "{caso}_relatorio.md",
     "o texto corrido da analise, em Markdown"),
    ("achados", "Achados (JSON)", "{caso}_findings.json",
     "os achados como dado, para planilha ou outra ferramenta"),
    ("pasta", "Pasta do caso", "",
     "a evidencia bruta como saiu do banco"),
]


def _abrir_no_sistema(caminho: str) -> None:
    """Duplo clique do Explorer. Injetavel no Painel para o teste nao abrir
    PowerPoint na cara de quem roda a suite."""
    if hasattr(os, "startfile"):
        os.startfile(caminho)
    else:  # Linux/macOS: o painel e Windows, mas nao vale quebrar o import
        subprocess.Popen(["xdg-open", caminho])


# Cadastro de conexao pela tela. A porta e 1529 e nao a 1521 do Oracle: e a
# deste parque, e errar isso rende um timeout de rede que parece VPN caida.
PORTA_PADRAO = "1529"
USER_PADRAO = "dbaread"
FOLDER_PADRAO = "PRODUCAO"


def nome_caso_valido(caso) -> bool:
    """O nome serve de PASTA no disco - por isso a regra e do sistema de
    arquivos, nao do gosto de quem digita.

    Aceita nao-str sem estourar: o corpo vem de JSON, onde `caso` pode chegar
    como numero ou lista. Devolver False ali e o que transforma um 500 com
    stacktrace num 400 com instrucao.
    """
    if not isinstance(caso, str):
        return False
    caso = caso.strip()
    if not _CASO_RE.match(caso):
        return False
    # A extensao nao salva: 'NUL.txt' tambem e o dispositivo nulo no Windows.
    return caso.split(".")[0].lower() not in _RESERVADOS_WINDOWS


def _kb_lista(termo: str) -> list:
    """Resumo dos playbooks, filtrado pela busca.

    Devolve resumo (sem o corpo dos passos) porque a lista da tela nao precisa
    dos comandos - eles vao numa segunda chamada, so do playbook aberto.
    """
    termo = (termo or "").strip()
    if not termo:
        return kb.resumo()
    ids = [p["id"] for p in kb.buscar(termo)]
    por_id = {r["id"]: r for r in kb.resumo()}
    return [por_id[i] for i in ids if i in por_id]


def sugestoes_kb(caso: str, out_dir: str = "") -> list:
    """Playbooks sugeridos a partir dos achados JA analisados do caso.

    E o pulo de "esta ruim" para "faca isto": o relatorio diz que a secao
    'Idade do backup por datafile (RPO real)' tem 21 achados criticos, e a tela
    oferece o playbook de backup sem o DBA precisar saber que ele existe.

    Le o `<caso>_findings.json` que o TROUBLESHOOT.py ja exporta - nao reanalisa
    nada e nao toca em banco. Caso sem relatorio simplesmente nao sugere.
    """
    base = out_dir or OUTPUT_DIR
    path = os.path.join(base, caso, f"{caso}_findings.json")
    try:
        with open(path, encoding="utf-8") as fh:
            dados = json.load(fh)
    except Exception:
        return []

    # peso por severidade: o que dispara o playbook e o achado que doi.
    peso = {"crit": 3, "warn": 1}
    acc: dict = {}
    for f in dados.get("findings") or []:
        p = peso.get(f.get("severity"), 0)
        if not p:
            continue
        secao = f.get("section") or ""
        for pb in kb.sugerir_para_secao(secao):
            reg = acc.setdefault(pb["id"], {
                "id": pb["id"], "titulo": pb.get("titulo", ""),
                "passos": len(pb.get("passos") or []),
                "score": 0, "secoes": {}, "crit": 0})
            reg["score"] += p
            reg["secoes"][secao] = reg["secoes"].get(secao, 0) + 1
            if f.get("severity") == "crit":
                reg["crit"] += 1

    out = []
    for reg in acc.values():
        # o motivo aparece na tela: sugestao sem justificativa nao gera confianca
        secoes = sorted(reg["secoes"].items(), key=lambda x: -x[1])[:2]
        reg["motivo"] = "; ".join(f"{s} ({n})" for s, n in secoes)
        reg.pop("secoes")
        out.append(reg)
    out.sort(key=lambda r: (-r["score"], r["titulo"]))
    # Teto para a lista nao virar ruido: num caso carregado a cauda entra com um
    # unico achado de atencao e dilui o topo. Mas playbook com achado CRITICO
    # nunca e cortado - esconder o que doi para caber na tela seria o pior erro
    # que esta funcao pode cometer.
    return [r for i, r in enumerate(out) if i < MAX_SUGESTOES_KB or r["crit"]]


def indice_casos(out_dir: str = "") -> dict:
    """`index.json` de output/troubleshooting: severidade e Health Score por caso.

    O arquivo e escrito pelo `TROUBLESHOOT.py` a cada relatorio. Ler aqui e o
    oposto de duplicar regra: o painel nao recalcula severidade nem score, so
    mostra o que o nucleo ja decidiu. Indice ausente (nenhum relatorio gerado
    ainda) devolve {} e a tela simplesmente nao exibe o selo.
    """
    path = os.path.join(out_dir or OUTPUT_DIR, "index.json")
    try:
        with open(path, encoding="utf-8") as fh:
            dados = json.load(fh)
    except Exception:
        return {}
    return dados if isinstance(dados, dict) else {}


def _txt(dados: dict, chave: str) -> str:
    """Campo de texto do corpo JSON, sempre str e sempre sem sobra nas pontas.

    `(dados.get(x) or "").strip()` parece a mesma coisa e nao e: com uma lista
    no lugar do texto ele estoura `AttributeError`, e o painel responde 500 com
    stacktrace em vez de dizer o que faltou. Aqui o tipo errado vira campo
    vazio, e a acao segue para a sua propria mensagem de campo obrigatorio.
    """
    v = dados.get(chave)
    return v.strip() if isinstance(v, str) else ""


def _validar_caso(caso) -> str:
    if not nome_caso_valido(caso):
        if isinstance(caso, str) and caso.strip().split(".")[0].lower() \
                in _RESERVADOS_WINDOWS:
            raise ValueError(
                f"'{caso.strip()}' e um nome reservado do Windows (CON, NUL, "
                "PRN, AUX, COM1-9, LPT1-9): a pasta ate seria criada, mas a "
                "evidencia nao chegaria ao disco. Use outro nome de caso.")
        raise ValueError(
            "nome de caso invalido: use letras, numeros, '_', '.' ou '-' "
            "(ex.: INC000987654, HC_DBSRV600)")
    return caso.strip()


def _sem_proibidos(cmd: list, advisor_ok: bool = False) -> list:
    """Barra o que a tela nunca pode disparar sozinha.

    `advisor_ok` e o unico portao, e existe so para a coleta em que o DBA marcou
    a caixa e confirmou o popup. Todo outro caminho (janela, drill-down,
    regerar) continua recusando: o advisor escreve no dicionario e consome
    Tuning Pack, e abrir a excecao por comando - e nao por processo - e o que
    impede um `--advisor` de entrar de carona onde ninguem confirmou nada.
    """
    for arg in cmd:
        a = str(arg).lower()
        if a in _PROIBIDOS and not (advisor_ok and a == "--advisor"):
            raise ValueError(
                f"{arg} nao pode ser executado pelo painel: escreve no banco "
                "(cria tuning tasks) e consome Tuning Pack. Rode no terminal, "
                "com confirmacao explicita.")
    return cmd


def montar_cmd_coletar(caso: str, conn: str, modo: str = "completo",
                       python: str = "", advisor: bool = False) -> list:
    """Linha de comando do COLETAR.py. Nao executa nada.

    `advisor` e opt-in e SO no modo completo: o advisor parte das regressoes
    apuradas no historico do AWR, que e exatamente o que o rapido e o parcial
    nao coletam - oferecer ali entregaria uma coleta que escreve no banco e nao
    tem como escolher SQL nenhum.
    """
    caso = _validar_caso(caso)
    conn = (conn or "").strip()
    if not conn:
        raise ValueError("escolha a conexao (servidor)")
    if modo not in MODOS:
        raise ValueError(f"modo invalido: {modo} (use {', '.join(MODOS)})")
    if advisor and modo != "completo":
        raise ValueError(
            "o SQL Tuning Advisor so roda no modo completo: ele parte da "
            "regressao apurada no historico do AWR, que o modo "
            f"'{modo}' nao coleta")
    cmd = [python or sys.executable, "COLETAR.py", caso, "--conn", conn]
    cmd += MODOS[modo]
    if advisor:
        cmd.append("--advisor")
    return _sem_proibidos(cmd, advisor_ok=bool(advisor))


def montar_cmd_redija(caso: str, python: str = "") -> list:
    """Linha de comando do TROUBLESHOOT.py (regera o relatorio, sem coletar)."""
    caso = _validar_caso(caso)
    return _sem_proibidos(
        [python or sys.executable, "TROUBLESHOOT.py", caso, "--completo"])


def montar_cmd_detalhar(caso: str, sql_id: str, conn: str,
                        python: str = "") -> list:
    """Drill-down read-only de um SQL_ID (DETALHAR_SQL.py)."""
    caso = _validar_caso(caso)
    sql_id = (sql_id or "").strip().lower()
    if not _SQLID_RE.match(sql_id):
        raise ValueError("sql_id invalido: sao 13 caracteres (ex.: 9xy1abcd2ef3g)")
    conn = (conn or "").strip()
    if not conn:
        raise ValueError("sem conexao: o caso nao tem 'conn' no meta.json, "
                         "informe o servidor")
    return _sem_proibidos([python or sys.executable, "DETALHAR_SQL.py", caso,
                           sql_id, "--conn", conn])


def montar_cmd_janela(caso: str, inicio: str, fim: str, conn: str,
                      python: str = "") -> list:
    """Analise read-only de uma janela pelo ASH historico (JANELA.py)."""
    caso = _validar_caso(caso)
    inicio, fim = (inicio or "").strip(), (fim or "").strip()
    for r in (inicio, fim):
        if not _QUANDO_RE.match(r):
            raise ValueError(f"data/hora invalida: '{r}' (use YYYY-MM-DD HH:MI)")
    if inicio >= fim:
        raise ValueError("o inicio da janela tem de ser antes do fim")
    conn = (conn or "").strip()
    if not conn:
        raise ValueError("sem conexao: o caso nao tem 'conn' no meta.json, "
                         "informe o servidor")
    return _sem_proibidos([python or sys.executable, "JANELA.py", caso,
                           "--inicio", inicio, "--fim", fim, "--conn", conn])


def montar_cmd_executar(caso: str, conn: str = "", params: dict | None = None,
                        tudo: bool = False, python: str = "") -> list:
    """Executa a VERIFICACAO do passo guiado (GUIA.py --executar), read-only.

    Nao aceita "qual SQL rodar": o comando sai do PLAYBOOK, escolhido pelo
    indice do passo em que o caso esta. Se a tela pudesse mandar SQL, o painel
    viraria um console de banco aberto em 127.0.0.1 - e qualquer aba do
    navegador poderia usar (e o motivo do porteiro de token existir).

    O que a tela manda e so o VALOR de parametro (`&sql_id`), validado no
    `executor.aplicar_parametros` antes de entrar na query.
    """
    caso = _validar_caso(caso)
    cmd = [python or sys.executable, "GUIA.py", caso,
           "--executar-tudo" if tudo else "--executar"]
    for nome, valor in sorted((params or {}).items()):
        nome = str(nome).strip()
        valor = str(valor or "").strip()
        if not valor:
            continue
        if not _PARAM_NOME_RE.match(nome):
            raise ValueError(f"nome de parametro invalido: {nome!r}")
        # O valor volta a ser checado no executor; aqui a barreira e contra
        # injecao de FLAG (um valor comecando com '-' seria lido pelo argparse
        # como opcao, nao como dado).
        if not _PARAM_VALOR_RE.match(valor):
            raise ValueError(
                f"valor invalido para {nome}: use so letras, numeros, "
                "'_', '$', '#', '.', '@' ou '-' (sem espaco)")
        cmd += ["--param", f"{nome}={valor}"]
    if conn.strip():
        cmd += ["--conn", conn.strip()]
    return _sem_proibidos(cmd)


def caminho_relatorio(caso: str) -> str:
    """HTML do caso, validado contra path traversal. '' se nao existir."""
    caso = _validar_caso(caso)
    p = os.path.abspath(os.path.join(OUTPUT_DIR, caso, f"{caso}.html"))
    if not p.startswith(os.path.abspath(OUTPUT_DIR) + os.sep):
        raise ValueError("caminho fora de output/troubleshooting")
    return p if os.path.exists(p) else ""


# ── execucao ──────────────────────────────────────────────────
def _popen(cmd: list, env: dict, cwd: str, interativo: bool = False):
    kwargs = {}
    if os.name == "nt":  # nao piscar janela de console a cada comando
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    # O stdin JAMAIS pode ser herdado: se o filho enxergar o terminal em que o
    # painel foi aberto, `isatty()` da True e qualquer pergunta do CLI fica
    # esperando uma resposta que ninguem na tela ve - o job trava para sempre
    # (bug de 02/08). Ou e um pipe pelo qual a tela responde (interativo), ou e
    # DEVNULL e o CLI falha rapido dizendo o que faltou.
    entrada = subprocess.PIPE if interativo else subprocess.DEVNULL
    return subprocess.Popen(cmd, cwd=cwd, env=env, stdin=entrada,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1,
                            encoding="utf-8", errors="replace", **kwargs)


# Comandos que falam o protocolo do `troubleshoot.dialogo`: sao os que conectam
# no banco e portanto pedem senha. Lista explicita, e nao "todo mundo", porque
# abrir um stdin de dialogo para quem nao sabe responder recria o travamento -
# o processo pararia numa pergunta que ninguem faz.
_INTERATIVOS = ("COLETAR.PY", "JANELA.PY", "DETALHAR_SQL.PY", "GUIA.PY")


def aceita_dialogo(cmd: list) -> bool:
    """O comando sabe conversar pelo protocolo do `troubleshoot.dialogo`?"""
    return any(str(a).upper().endswith(_INTERATIVOS) for a in cmd)


def _caso_do_cmd(cmd: list) -> str:
    """Caso a que a execucao se refere, para o historico persistido.

    Todos os comandos da tela tem a forma `python SCRIPT.py <caso> ...`, entao e
    o primeiro argumento depois do script que nao comeca com '-'. Sem isto o
    historico entre sessoes viraria uma lista de linhas de comando parecidas,
    onde ninguem acha a coleta do INC que interessa.
    """
    for i, arg in enumerate(cmd):
        if str(arg).upper().endswith(".PY") and i + 1 < len(cmd):
            alvo = str(cmd[i + 1])
            return alvo if _CASO_RE.match(alvo) else ""
    return ""


class Job:
    """Uma execucao em andamento (ou terminada) e seu log."""

    def __init__(self, jid: str, cmd: list):
        self.id = jid
        self.cmd = list(cmd)
        self.linhas: list = []
        self.rc = None
        self.iniciado_em = time.strftime("%H:%M:%S")
        self.pergunta = None    # {label, default, secreto} enquanto espera resposta
        self._proc = None
        self._lock = threading.Lock()

    @property
    def rodando(self) -> bool:
        return self.rc is None

    def perguntar(self, p: dict) -> None:
        with self._lock:
            self.pergunta = p
        # No log fica a pergunta, nunca o marcador cru: quem le o historico
        # depois precisa entender por que a coleta parou ali.
        self.escrever(f"[?] {p['label']}" + (f" [{p['default']}]" if p["default"] else ""))

    def responder(self, valor: str) -> bool:
        """Manda a resposta para o processo. False se nao havia pergunta."""
        with self._lock:
            p = self.pergunta
            self.pergunta = None
        if p is None or self._proc is None or self._proc.stdin is None:
            return False
        # Senha NAO entra no log: a tela e o historico da sessao ficam abertos.
        self.escrever(f"[>] {'*' * 8 if p['secreto'] else (valor or p['default'])}")
        try:
            self._proc.stdin.write((valor or "") + "\n")
            self._proc.stdin.flush()
        except Exception as e:
            self.escrever(f"[ERRO] nao foi possivel responder: {e}")
            return False
        return True

    def cancelar(self) -> bool:
        """Encerra o processo. E a saida para uma pergunta que o DBA nao quer
        responder - sem isso, dialogo vira uma forma nova de deixar job preso."""
        if self._proc is None or not self.rodando:
            return False
        with self._lock:
            self.pergunta = None
        self.escrever("[x] cancelado pelo usuario")
        try:
            self._proc.terminate()
        except Exception:
            return False
        return True

    def escrever(self, linha: str) -> None:
        with self._lock:
            self.linhas.append(linha.rstrip("\n"))
            if len(self.linhas) > MAX_LINHAS:  # log longo nao pode virar vazamento
                del self.linhas[:len(self.linhas) - MAX_LINHAS]

    def log(self, desde: int = 0) -> dict:
        with self._lock:
            return {"id": self.id, "desde": desde,
                    "linhas": self.linhas[desde:], "total": len(self.linhas),
                    "rodando": self.rodando, "rc": self.rc,
                    "pergunta": self.pergunta,
                    "cmd": " ".join(self.cmd[1:])}


class Painel:
    """Estado do painel: conexoes, casos e o job atual."""

    def __init__(self, base_dir: str = BASE_DIR, python: str = "", runner=None,
                 abridor=None, hist_path: str = "", token: str = ""):
        self.base_dir = base_dir
        self.python = python or sys.executable
        self._runner = runner or _popen
        # Injetavel para o teste nao abrir PowerPoint na cara de quem roda a
        # suite - e para provar que o caminho certo chegou ate aqui.
        self.abridor = abridor or _abrir_no_sistema
        self.jobs = []          # historico desta sessao, mais recente por ultimo
        self._seq = 0
        self.hist_path = hist_path or HIST_PATH
        # Identifica a SESSAO no arquivo: os ids de job (j1, j2...) reiniciam a
        # cada abertura do painel e colidiriam entre si no historico. O sufixo
        # aleatorio existe porque dois paineis abertos no MESMO segundo (duas
        # janelas, ou o reabrir depois de trocar de porta) teriam a mesma marca
        # de tempo - e o j1 de um sobrescreveria o j1 do outro na leitura.
        self.sessao = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
        # Segredo da sessao. Sem ele, qualquer pagina aberta no mesmo navegador
        # consegue disparar uma coleta em producao com um fetch() para
        # 127.0.0.1 - o loopback protege contra a REDE, nao contra o proprio
        # navegador. Nasce por sessao e nunca vai para disco.
        self.token = token or secrets.token_urlsafe(24)

    @property
    def job(self):
        """Execucao mais recente (None se nada rodou ainda)."""
        return self.jobs[-1] if self.jobs else None

    @job.setter
    def job(self, valor):
        # Usado pelos testes para zerar o estado; manter o historico coerente.
        self.jobs = [] if valor is None else [*self.jobs, valor]

    def historico(self) -> list:
        return [{"id": j.id, "cmd": " ".join(j.cmd[1:]), "rc": j.rc,
                 "rodando": j.rodando, "iniciado_em": j.iniciado_em,
                 "linhas": len(j.linhas)} for j in reversed(self.jobs)]

    def acha_job(self, jid: str):
        for j in self.jobs:
            if j.id == jid:
                return j
        return None

    # -- historico que sobrevive ao fechar o painel
    def _registrar(self, job: Job, rc=None) -> None:
        """Anota a execucao no arquivo (uma linha por evento, JSONL).

        Duas linhas por job (inicio e fim) em vez de reescrever o arquivo: o
        painel pode ser fechado no meio de uma coleta, e o formato de append e o
        unico que nao perde o registro nem corrompe o que ja estava la. A
        consolidacao (ultimo evento vence) fica na leitura.

        Falha de escrita e engolida de proposito: historico e conveniencia, e
        disco cheio nao pode impedir o DBA de rodar a coleta do incidente.
        """
        reg = {"sessao": self.sessao, "id": job.id, "cmd": " ".join(job.cmd[1:]),
               "caso": _caso_do_cmd(job.cmd),
               "rc": job.rc if rc is None else rc,
               "iniciado_em": job.iniciado_em,
               "em": time.strftime("%Y-%m-%d %H:%M:%S")}
        try:
            os.makedirs(os.path.dirname(self.hist_path), exist_ok=True)
            with open(self.hist_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(reg, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def execucoes(self, limite: int = 30) -> list:
        """Execucoes de TODAS as sessoes, da mais recente para a mais antiga.

        Responde "o que ja rodou nesta maquina", que e a pergunta de quem chega
        no plantao seguinte. Linha corrompida e pulada em silencio - um arquivo
        truncado por queda de energia nao pode deixar o painel sem abrir.
        """
        vistos: dict = {}
        try:
            with open(self.hist_path, encoding="utf-8") as fh:
                for linha in fh:
                    linha = linha.strip()
                    if not linha:
                        continue
                    try:
                        r = json.loads(linha)
                    except ValueError:
                        continue
                    if isinstance(r, dict) and r.get("id"):
                        vistos[(r.get("sessao", ""), r["id"])] = r
        except OSError:
            return []
        regs = list(vistos.values())
        # A hora do evento e 'YYYY-MM-DD HH:MM:SS': ordena como texto.
        regs.sort(key=lambda r: r.get("em") or "", reverse=True)
        for r in regs:
            # Job desta sessao que ainda roda: o arquivo tem o registro de
            # inicio (rc=None) e a memoria tem a verdade.
            j = self.acha_job(r["id"]) if r.get("sessao") == self.sessao else None
            r["rodando"] = bool(j and j.rodando)
            r["desta_sessao"] = r.get("sessao") == self.sessao
        return regs[:limite]

    def limpar_execucoes(self) -> dict:
        """Apaga o historico persistido (nao mexe nos casos nem na evidencia)."""
        try:
            if os.path.exists(self.hist_path):
                os.remove(self.hist_path)
        except OSError as e:
            raise ValueError(f"nao foi possivel apagar o historico: {e}") from e
        return {"ok": True, "msg": "historico de execucoes apagado"}

    def _podar_historico(self) -> None:
        """Mantem o arquivo nos ultimos MAX_HIST_PERSISTIDO registros.

        Sem poda, um arquivo de append cresce para sempre - e este roda em
        maquina de DBA, nao em servidor com rotacao de log.
        """
        try:
            with open(self.hist_path, encoding="utf-8") as fh:
                linhas = fh.readlines()
        except OSError:
            return
        # 2 eventos por execucao (inicio e fim): o teto de linhas e o dobro.
        teto = MAX_HIST_PERSISTIDO * 2
        if len(linhas) <= teto:
            return
        try:
            with open(self.hist_path, "w", encoding="utf-8") as fh:
                fh.writelines(linhas[-teto:])
        except OSError:
            pass

    # -- comandos
    def iniciar(self, cmd: list, advisor_ok: bool = False) -> Job:
        if self.job is not None and self.job.rodando:
            raise RuntimeError("ja existe uma execucao em andamento "
                               "(uma coleta por vez, para nao castigar o banco)")
        _sem_proibidos(cmd, advisor_ok=advisor_ok)
        self._seq += 1
        job = Job(f"j{self._seq}", cmd)
        self.jobs.append(job)
        del self.jobs[:-MAX_JOBS]
        self._registrar(job)        # inicio: sobrevive a queda no meio da coleta
        self._podar_historico()

        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        # Nem a variavel do dialogo nem a senha entram por heranca: o painel
        # pode ter sido aberto num terminal que tinha as duas definidas, e ai o
        # filho deixaria de perguntar (ou perguntaria sem ninguem ouvindo).
        env.pop(dialogo.VAR, None)
        env.pop("ORA_PASSWORD", None)

        interativo = aceita_dialogo(cmd)
        if interativo:
            env[dialogo.VAR] = "1"      # "tem alguem na tela para responder"

        try:
            proc = self._runner(cmd, env, self.base_dir, interativo)
        except Exception as e:
            job.escrever(f"[ERRO] nao foi possivel executar: {e}")
            job.rc = -1
            # Sem isto o arquivo guarda so o registro de inicio e a execucao
            # fica "rodando" para sempre no historico da proxima sessao.
            self._registrar(job)
            return job
        job._proc = proc

        def _bombear():
            try:
                if proc.stdout:
                    for linha in proc.stdout:
                        p = dialogo.ler(linha)
                        if p:
                            job.perguntar(p)
                        else:
                            job.escrever(linha)
                proc.wait()
            except Exception as e:  # pragma: no cover - defensivo
                job.escrever(f"[ERRO] {e}")
            finally:
                job.pergunta = None     # processo morreu: nao ha o que responder
                rc = proc.returncode if proc.returncode is not None else -1
                # Gravar ANTES de publicar o rc: `job.rodando` virando False e
                # o sinal que todo mundo espera para ler o resultado, e quem
                # lesse o arquivo nesse instante veria a execucao sem desfecho.
                self._registrar(job, rc)
                job.rc = rc

        threading.Thread(target=_bombear, daemon=True).start()
        return job

    # -- leitura
    def conexoes(self) -> list:
        return [{"name": c["name"], "dsn": c.dsn, "user": c["user"],
                 "folder": c.get("folder", "")}
                for c in conn_repo.load_connections()]

    def casos(self, limite: int = 40) -> list:
        """Casos com o estado do ciclo de vida MAIS o veredito do ultimo relatorio.

        O ciclo de vida (`resumo`) responde "o que estamos fazendo com este
        caso"; o indice responde "quao ruim esta este banco". Sem o segundo, a
        lista mostra 37 linhas visualmente iguais e o DBA precisa abrir uma a
        uma para achar a que esta pegando fogo - justamente a pergunta que a
        tela existe para responder de relance.

        Caso ainda sem relatorio entra com score None e a tela nao exibe selo:
        e diferente de ter score zero.
        """
        idx = indice_casos()
        out = []
        # O índice e a fonte da verdade do relatório gerado. Casos apenas
        # coletados ainda podem nao ter ciclo de vida, mas o HTML/PPTX/CSV
        # ja existem e precisam aparecer no painel para dar continuidade.
        nomes = caso_lifecycle.listar_casos(so_troubleshooting=True)
        for nome in idx:
            try:
                if caminho_relatorio(nome):
                    nomes.append(nome)
            except (TypeError, ValueError):
                continue
        nomes = sorted(set(nomes), key=lambda n: caso_lifecycle.resumo(n).get(
            "criado_em", "") or "", reverse=True)[:limite]
        for nome in nomes:
            r = caso_lifecycle.resumo(nome)
            r["tem_relatorio"] = bool(caminho_relatorio(nome))
            i = idx.get(nome) or {}
            r["score"] = i.get("score")
            r["severidade"] = i.get("severity") or ""
            r["achados"] = i.get("n_findings")
            r["gerado_em"] = i.get("generated_at") or ""
            out.append(r)
        return out

    def conn_do_caso(self, caso: str) -> str:
        """Banco de origem das evidencias, gravado pelo COLETAR.py no meta.json.
        Evita perguntar de novo o servidor no drill-down."""
        return caso_lifecycle.ler(_validar_caso(caso)).get("conn", "")

    def detalhe_caso(self, caso: str) -> dict:
        caso = _validar_caso(caso)
        return {"caso": caso,
                "resumo": caso_lifecycle.resumo(caso),
                "conn": self.conn_do_caso(caso),
                "timeline": caso_lifecycle.linha_do_tempo(caso),
                "tem_relatorio": bool(caminho_relatorio(caso)),
                "reincidencia": self.reincidencia_do_caso(caso),
                "kb": sugestoes_kb(caso)}

    # ── historico: a serie que atravessa as coletas ───────────
    # O `HISTORICO.py` ja respondia "desde quando isso sobe, e quando estoura?"
    # - e so no terminal. O painel e onde o DBA esta, e e onde o salto de carga
    # aparece; deixar a serie fora dele significava que ninguem ia rodar.
    #
    # Duas perguntas, cada uma com um dono:
    #   quando o patamar mudou  -> o analisador de carga responde
    #   O QUE mudou naquele dia -> so uma pessoa sabe, e e o MARCO
    #
    # Sem um lugar para registrar o segundo, a mesma investigacao e refeita do
    # zero no mes seguinte.

    LIMITE_PREVISAO_PCT = 90.0

    def historico_ambiente(self, caso: str) -> dict:
        """Series, tendencia e marcos do ambiente de onde o caso veio.

        Recebe o CASO e nao o ambiente porque e o que a tela tem em maos no
        drill-down; a conversao usa o mesmo `conn` do meta.json que o resto do
        painel usa, para a tela e o `HISTORICO.py` nunca falarem de ambientes
        diferentes.

        `previsao` so vem preenchida onde a amostra autoriza (ver
        `historico.tendencia`): o campo ausente significa "nao sei", e a tela
        diz isso em vez de desenhar uma reta sobre dois pontos.
        """
        from . import historico as H
        amb = (self.conn_do_caso(_validar_caso(caso)) or "").upper()
        hist = H.ler()
        ent = (hist.get("ambientes") or {}).get(amb) or {}
        series = []
        for familia, nomes in sorted((ent.get("series") or {}).items()):
            for nome, pontos in sorted(nomes.items()):
                pts = sorted(pontos.items())
                t = H.tendencia(pts)
                item = {"familia": familia, "nome": nome,
                        "pontos": [{"dia": d, "valor": v} for d, v in pts],
                        "atual": pts[-1][1] if pts else None,
                        "n": len(pts)}
                if t:
                    item["por_dia"] = round(t["por_dia"], 4)
                    item["r2"] = round(t["r2"], 2)
                    item["dias"] = t["dias"]
                    if familia != "carga":
                        p = H.dias_para(pts, self.LIMITE_PREVISAO_PCT)
                        if p:
                            item["dias_para"] = round(p["dias_para"])
                            item["limite"] = self.LIMITE_PREVISAO_PCT
                series.append(item)
        return {
            "caso": caso, "ambiente": amb,
            "series": series,
            "marcos": ent.get("marcos") or [],
            "minimo": {"pontos": H.MIN_PONTOS, "dias": H.MIN_DIAS_JANELA},
            # Sem ambiente identificado nao ha serie: e o caso coletado no
            # Citrix, sem meta.json com `conn`. Dizer isso e melhor que
            # devolver lista vazia, que a tela leria como "nada cresceu".
            "sem_ambiente": not amb,
        }

    def marcar_historico(self, d: dict) -> dict:
        """Registra O QUE aconteceu num dia - a metade que o SELECT nao alcanca.

        Escreve no MESMO `historico.json` que o `HISTORICO.py --marco` usa: se a
        tela tivesse um arquivo proprio, o terminal e o painel passariam a
        contar historias diferentes do mesmo ambiente.
        """
        from . import historico as H
        amb = str(d.get("ambiente") or "").strip()
        dia = str(d.get("dia") or "").strip()
        texto = str(d.get("texto") or "").strip()
        if not amb:
            return {"erro": "sem ambiente: nao ha onde registrar o marco"}
        try:
            hist = H.marcar(amb, dia, texto)
        except ValueError as ex:
            return {"erro": str(ex)}
        H.gravar(hist)
        return {"ok": True, "ambiente": amb.upper(), "dia": dia,
                "marcos": (hist["ambientes"][amb.upper()].get("marcos") or [])}

    def redija_caso(self, caso: str) -> dict:
        """O caso no Template Padrao, pronto para colar no chamado.

        Fica aqui e nao no relatorio porque redigir e o ultimo passo do fluxo
        na propria tela: quem acabou de resolver o caso nao deveria ter de
        rodar `redija.bat` e abrir um `.md` so para copiar dez linhas.
        """
        caso = _validar_caso(caso)
        texto = caso_lifecycle.texto_padrao(caso)
        return {"caso": caso, "texto": texto,
                "faltando": texto.count(caso_lifecycle.FALTA),
                "propostas": self.propostas_do_caso(caso)}

    def propostas_do_caso(self, caso: str) -> list:
        """Caixinhas de preenchimento: o fato observado + o contorno do playbook.

        Le o `<caso>_findings.json` que a analise ja exportou - nao reanalisa e
        nao toca em banco. Caso sem relatorio nao oferece proposta, o que e
        correto: nao ha achado de onde tirar fato.
        """
        caso = _validar_caso(caso)
        p = os.path.join(OUTPUT_DIR, caso, f"{caso}_findings.json")
        if not os.path.exists(p):
            return []
        try:
            with open(p, encoding="utf-8") as fh:
                dados = json.load(fh)
        except Exception:
            # Proposta e ajuda, nao diagnostico: JSON corrompido nao derruba a
            # tela de redigir, que e o ultimo passo do fluxo.
            return []
        return destaques.propostas(dados.get("findings") or [])

    def aplicar_propostas(self, caso: str, ids: list) -> dict:
        """Grava causa raiz e solucao a partir das caixinhas marcadas.

        Grava no `meta.json` em vez de so injetar texto na tela porque o proprio
        painel avisa que registrar apenas no chamado deixa o historico do caso
        menor que o texto entregue. Marcar a caixinha e decisao do DBA: por isso
        o texto vai limpo, sem a marca de "analise automatica" que o relatorio
        usa quando ninguem escolheu nada. A origem fica em `causa_origem`, para a
        auditoria saber que veio de proposta.
        """
        caso = _validar_caso(caso)
        todas = self.propostas_do_caso(caso)
        escolhidas = [p for p in todas if p["id"] in (ids or [])]
        if not escolhidas:
            raise ValueError("nenhuma proposta marcada")
        campos = destaques.texto_da_selecao(escolhidas)
        # `definir`, nao `resolver`: aceitar a proposta preenche o RCA, mas nao
        # declara o caso resolvido - quem fecha o caso e o DBA, no botao proprio.
        caso_lifecycle.definir(
            caso, causa_raiz=campos["causa_raiz"], solucao=campos["solucao"],
            causa_origem="proposta:" + ",".join(p["id"] for p in escolhidas))
        texto = caso_lifecycle.texto_padrao(caso)
        return {"caso": caso, "texto": texto,
                "faltando": texto.count(caso_lifecycle.FALTA),
                "aplicadas": len(escolhidas)}

    def artefatos(self, caso: str) -> list:
        """O que a analise ja produziu para este caso, com o que existe de fato.

        O painel dizia so 'Abrir relatorio'. O caso rende cinco arquivos e os
        outros quatro so eram alcancaveis pelo Explorer - inclusive o PPTX, que
        e o que sobe quando o incidente escala.
        """
        caso = _validar_caso(caso)
        d = os.path.join(OUTPUT_DIR, caso)
        itens = []
        for chave, rot, nome, dica in ARTEFATOS:
            alvo = d if chave == "pasta" else os.path.join(d, nome.format(caso=caso))
            itens.append({"chave": chave, "rotulo": rot, "dica": dica,
                          "arquivo": os.path.basename(alvo),
                          "existe": os.path.exists(alvo)})
        return itens

    def abrir_artefato(self, caso: str, chave: str) -> dict:
        """Abre o arquivo no programa padrao do Windows.

        Vale porque o painel roda NA maquina do DBA - e o mesmo duplo clique
        que ele daria no Explorer, sem sair da tela. Duas travas: so as chaves
        de `ARTEFATOS` (nao um caminho vindo do navegador) e o resultado tem de
        cair dentro da pasta do caso.
        """
        caso = _validar_caso(caso)
        mapa = {c: n for c, _, n, _ in ARTEFATOS}
        if chave not in mapa:
            raise ValueError(f"artefato desconhecido: {chave}")
        base = os.path.abspath(os.path.join(OUTPUT_DIR, caso))
        alvo = base if chave == "pasta" else os.path.abspath(
            os.path.join(base, mapa[chave].format(caso=caso)))
        if os.path.commonpath([base, alvo]) != base:
            raise ValueError("caminho fora da pasta do caso")
        if not os.path.exists(alvo):
            raise ValueError(
                f"{os.path.basename(alvo)} ainda nao existe - rode "
                "'Regerar relatorio'")
        self.abridor(alvo)
        return {"ok": True, "msg": f"abrindo {os.path.basename(alvo)}"}

    def checar_conexao(self, nome: str) -> dict:
        """O servidor existe no repositorio? Responde ANTES de disparar a coleta.

        Sem isto, nome errado so aparecia como `rc=2` no fim do log - depois de
        abrir processo, imprimir cabecalho e o DBA esperar. E o erro nao levava
        a lugar nenhum: para cadastrar era preciso sair do painel e rodar
        `COLETAR.py --add` no terminal.
        """
        nome = (nome or "").strip()
        if not nome:
            raise ValueError("escolha a conexao (servidor)")
        # `repo_dir` explicito: o default e resolvido na DEFINICAO da funcao,
        # entao apontar o repositorio para outro lugar (teste, pacote portatil)
        # nao teria efeito nenhum - e a checagem responderia sobre o inventario
        # errado.
        repo = conn_repo.REPO_DIR
        if conn_repo.find(nome, repo):
            return {"existe": True, "nome": nome, "parecidas": []}
        nomes = [c["name"] for c in conn_repo.load_connections(repo)]
        return {"existe": False, "nome": nome,
                "parecidas": conn_repo.parecidas(nome, nomes),
                # O que o cadastro vai pedir, ja com o palpite razoavel. Porta
                # 1529 e o padrao deste parque, nao a 1521 do Oracle.
                "sugestao": {"host": "", "port": PORTA_PADRAO,
                             "service": nome.upper(), "user": USER_PADRAO,
                             "folder": FOLDER_PADRAO}}

    def cadastrar_conexao(self, d: dict) -> dict:
        """Grava a conexao nova no repositorio, no formato do SQL Developer."""
        nome = (d.get("nome") or "").strip()
        host = (d.get("host") or "").strip()
        service = (d.get("service") or "").strip()
        if not nome or not host or not service:
            raise ValueError("nome, host e service name sao obrigatorios")
        porta = str(d.get("port") or PORTA_PADRAO).strip()
        if not porta.isdigit() or not (1 <= int(porta) <= 65535):
            raise ValueError(f"porta invalida: {porta}")
        path = conn_repo.add_or_update(
            nome, host, service,
            (d.get("user") or USER_PADRAO).strip(), porta,
            (d.get("folder") or FOLDER_PADRAO).strip(),
            repo_dir=conn_repo.REPO_DIR)
        return {"ok": True, "nome": nome,
                "dsn": f"{host}:{porta}/{service}",
                "arquivo": os.path.basename(path),
                "msg": f"conexao '{nome}' cadastrada ({host}:{porta}/{service})"}

    def reincidencia_do_caso(self, caso: str) -> list:
        """Achados deste caso que ja apareceram antes no MESMO servidor.

        Le o `history.json` que o relatorio ja grava - nada e recoletado e o
        banco nao e tocado. Historico corrompido devolve lista vazia: um card
        informativo nao pode derrubar a tela.
        """
        caso = _validar_caso(caso)
        p = os.path.join(OUTPUT_DIR, caso, f"{caso}_findings.json")
        if not os.path.exists(p):
            return []
        try:
            with open(p, encoding="utf-8") as fh:
                dados = json.load(fh) or {}
            titulos = [f.get("title", "") for f in dados.get("findings") or []]
            return reincidencia.analisar(caso, titulos,
                                         ref=dados.get("generated_at", ""))
        except Exception:
            return []

    def estado(self) -> dict:
        j = self.job
        return {
            "conexoes": self.conexoes(),
            "casos": self.casos(),
            "historico": self.historico(),
            # Execucoes de sessoes ANTERIORES tambem: quem assume o plantao
            # precisa saber o que ja rodou nesta maquina, e o painel costuma
            # ser reaberto varias vezes ao longo do mesmo incidente.
            "execucoes": self.execucoes(),
            "job": None if j is None else {
                "id": j.id, "rodando": j.rodando, "rc": j.rc,
                "total": len(j.linhas), "iniciado_em": j.iniciado_em,
                "cmd": " ".join(j.cmd[1:])},
        }

    # -- ciclo de vida: chama troubleshoot.caso, o mesmo modulo do CASO.py
    def acao_caso(self, acao: str, dados: dict) -> dict:
        if acao == "fim-do-dia":
            destino, linhas = caso_lifecycle.consolidar_dia(_txt(dados, "dia"))
            return {"ok": True, "msg": f"{len(linhas)} caso(s) em "
                                       f"{os.path.basename(destino)}",
                    "arquivo": destino}

        caso = _validar_caso(dados.get("caso", ""))
        if acao == "hipotese":
            # `origem` chega preenchida quando o DBA clicou "foi esta" numa
            # hipotese que o MOTOR propos, e vazia quando ele digitou a
            # propria. Guardar a diferenca e o que torna possivel, um dia,
            # medir se o motor acerta.
            d = caso_lifecycle.nova_hipotese(caso, dados.get("texto", ""),
                                             dados.get("motivo", ""),
                                             origem=_txt(dados, "origem"))
            return {"ok": True, "msg": f"hipotese registrada em {caso}",
                    "status": d.get("status", "")}
        if acao == "acao":
            caso_lifecycle.registrar_acao(caso, dados.get("texto", ""),
                                          dados.get("risco", "nenhum"))
            return {"ok": True, "msg": f"acao registrada em {caso}"}
        if acao == "status":
            caso_lifecycle.definir(caso, status=dados.get("status", ""))
            return {"ok": True, "msg": f"status de {caso} atualizado"}
        if acao == "escalar":
            caso_lifecycle.escalar(caso, dados.get("para", ""))
            return {"ok": True, "msg": f"{caso} marcado como escalado"}
        if acao == "resolver":
            caso_lifecycle.resolver(caso, dados.get("causa", ""),
                                    dados.get("solucao", ""),
                                    dados.get("prevencao", ""))
            return {"ok": True, "msg": f"{caso} resolvido"}
        raise ValueError(f"acao desconhecida: {acao}")

    # -- troubleshooting guiado: chama troubleshoot.guia, o mesmo do GUIA.py
    def guia_estado(self, caso: str) -> dict:
        """Tudo que a aba Guiado precisa numa chamada so.

        Uma chamada e nao tres porque as tres partes tem de contar a MESMA
        historia: passo, progresso e pendencias lidos em momentos diferentes
        mostrariam um passo que ja avancou ao lado de um progresso velho.
        """
        caso = _validar_caso(caso)
        # A evidencia e a coleta MAIS o que o DBA colou conduzindo. Avaliar
        # aqui (em vez de reler o findings.json) e o que faz a tela responder a
        # ultima saida observada, e nao a foto de quando o caso foi aberto.
        achados = guia.achados_do_caso(caso)
        hipoteses = raciocinio.avaliar(achados)
        return {
            "caso": caso,
            "progresso": guia.progresso(caso),
            # `or None` nao e defensividade solta: `passo_atual` devolve `{}`
            # quando o roteiro acabou, e `{}` e FALSY em Python e TRUTHY em
            # JavaScript. O CLI concluia certo (`if not p`) e a tela desenhava
            # um passo inexistente - "NaN/undefined", "Nao roda aqui: nenhum
            # passo em curso" e a caixa de colar, num roteiro 8/8 concluido.
            #
            # O conserto e aqui, no CONTRATO: `null` significa "nao ha passo"
            # nas duas linguagens. Tudo o que atravessa esta fronteira e
            # testado como valor, nunca como verdade.
            "passo": guia.passo_atual(caso) or None,
            "pendencias": guia.pendencias_abertas(caso),
            "sugestoes": guia.playbooks_sugeridos(caso),
            "ramo": guia.ramo_por_achados(achados),
            # O raciocínio inteiro, nao so a conclusao: a tela mostra o que
            # sustenta, o que foi descartado e o que ainda decide. Diagnostico
            # sem o porque e palpite com cara de resposta.
            "hipoteses": hipoteses,
            "narrativa": raciocinio.narrar(hipoteses),
            "saidas": guia.saidas(caso),
            # O passo atual roda no framework? Vem junto para a tela poder
            # mostrar o botao (ou o motivo da recusa) no MESMO desenho do
            # passo: pedir isso numa segunda chamada abriria a janela em que o
            # botao aparece para um passo que ja avancou.
            "execucao": guia.preparar_execucao(caso),
            "lote": guia.passos_executaveis(caso),
        }

    def guia_acao(self, acao: str, dados: dict) -> dict:
        caso = _validar_caso(dados.get("caso", ""))
        texto = _txt(dados, "texto")
        if acao == "iniciar":
            g = guia.iniciar(caso, _txt(dados, "playbook"))
            msg = f"roteiro iniciado: {g.get('titulo', '')}"
        elif acao == "avancar":
            guia.avancar(caso, texto)
            msg = "passo concluido"
        elif acao == "pular":
            guia.pular(caso, texto)
            msg = "passo pulado (motivo registrado)"
        elif acao == "confirmar":
            d = guia.confirmar_acao(caso, texto)
            msg = f"acao registrada no caso (risco {d['risco']})"
            if d["pendencias"]:
                msg += f" - {len(d['pendencias'])} pendencia(s) pos-acao"
        elif acao == "saida":
            # A saida colada e o retorno do comando que o guia mandou rodar.
            # Ela vira evidencia e o motor reavalia tudo - por isso a mensagem
            # devolve O QUE MUDOU, e nao um "ok" que nao ajuda quem conduz.
            d = guia.registrar_saida(caso, _txt(dados, "saida"))
            if d["aviso"]:
                msg = d["aviso"]
            elif d["mudou"]:
                msg = ("saida lida: " +
                       "; ".join(m["texto"] for m in d["mudou"][:2]))
            else:
                msg = (f"saida lida: {len(d['achados'])} achado(s) - "
                       "nenhuma hipotese mudou de veredito")
            estado = self.guia_estado(caso)
            estado["msg"] = msg
            estado["leitura"] = d
            return estado
        elif acao == "pendencia":
            guia.concluir_pendencia(caso, _txt(dados, "id"), texto)
            msg = "pendencia concluida"
        elif acao == "encerrar":
            guia.encerrar(caso)
            msg = "roteiro encerrado"
        else:
            raise ValueError(f"acao de guia desconhecida: {acao}")
        estado = self.guia_estado(caso)
        estado["msg"] = msg
        return estado

    def copiloto_texto(self, caso: str, anonimizar: bool = False) -> dict:
        """O caso empacotado para colar num chat de LLM. Nao chama rede."""
        caso = _validar_caso(caso)
        return {"caso": caso, "anonimizado": bool(anonimizar),
                "texto": copiloto.montar(caso, anonimizar)}


# ── Pagina ────────────────────────────────────────────────────
# Sem CDN de proposito: a maquina do DBA pode nao ter saida para a internet, e o
# painel tem de abrir igual num ambiente fechado ou rodando de pasta portatil.
PAGINA = r"""<!DOCTYPE html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Painel de Troubleshooting Oracle</title><style>
/* ── Tema ───────────────────────────────────────────────────
   Um unico conjunto de variaveis serve aos dois temas. Nenhuma cor literal
   fora daqui, senao o modo escuro fica com buraco branco em algum canto.
   Superficie: --bg (fundo) < --bg2 (cartao) < --bg3 (realce).
   O escuro nao e o claro invertido: usa cinza-azulado com contraste AA e
   reduz a saturacao dos acentos, que "vibram" sobre fundo escuro. */
:root{
--bg:#f1f5f9;--bg2:#fff;--bg3:#e8edf3;--line:#e2e8f0;--line2:#cbd5e1;
--txt:#0f172a;--mut:#64748b;--acc:#2563eb;--brand:#ec0000;--green:#15803d;
--orange:#b45309;--red:#b91c1c;
--acc-fraco:#eff6ff;--verde-fraco:#dcfce7;--laranja-fraco:#fef3c7;
--vermelho-fraco:#fee2e2;--roxo:#7e22ce;--roxo-fraco:#f3e8ff;
--azul-forte:#1d4ed8;--verde-txt:#166534;--codigo-bg:#0f172a;--codigo-txt:#cbd5e1;
--sombra:rgba(15,23,42,.07);--sombra-forte:rgba(15,23,42,.16);
--vidro:rgba(255,255,255,.88)}
[data-tema="escuro"]{
--bg:#0b1120;--bg2:#151d2e;--bg3:#1e293b;--line:#243044;--line2:#33415a;
--txt:#e8eef8;--mut:#94a3b8;--acc:#60a5fa;--brand:#ff4d4d;--green:#4ade80;
--orange:#fbbf24;--red:#f87171;
--acc-fraco:#172554;--verde-fraco:#052e16;--laranja-fraco:#3f2d0a;
--vermelho-fraco:#3f1414;--roxo:#c084fc;--roxo-fraco:#2e1065;
--azul-forte:#93c5fd;--verde-txt:#86efac;--codigo-bg:#080d18;--codigo-txt:#c7d3e6;
--sombra:rgba(0,0,0,.4);--sombra-forte:rgba(0,0,0,.6);
--vidro:rgba(21,29,46,.9)}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',Roboto,system-ui,sans-serif;background:var(--bg);
color:var(--txt);font-size:14px;
/* a troca de tema nao pode "piscar" a tela inteira */
transition:background-color .2s ease,color .2s ease}
.header{position:sticky;top:0;z-index:40;display:flex;align-items:center;gap:16px;
padding:11px 24px;background:var(--vidro);backdrop-filter:blur(10px);
-webkit-backdrop-filter:blur(10px);border-bottom:3px solid var(--brand);
box-shadow:0 1px 3px var(--sombra)}
/*CSS-MARCA*/
.header h1{font-size:17px;font-weight:600}.header .sub{color:var(--mut);font-size:12px}
.hdr-right{margin-left:auto;display:flex;gap:8px;align-items:center}
/* Faixa de indicadores: o estado do trabalho antes de qualquer clique. */
.kpis{display:flex;gap:10px;align-items:center;margin-right:6px}
.kpi{display:flex;flex-direction:column;align-items:center;min-width:56px;
padding:3px 10px;border-radius:9px;background:var(--bg);border:1px solid var(--line);
cursor:default}
.kpi .n{font-size:16px;font-weight:800;line-height:1.15}
.kpi .l{font-size:9px;color:var(--mut);text-transform:uppercase;letter-spacing:.5px}
.kpi.crit .n{color:var(--red)}.kpi.viv .n{color:var(--acc)}
@media(max-width:1000px){.kpis{display:none}}
/* ── Modo foco: uma etapa por vez ────────────────────────────
   A tela seguia a ordem dos cards, nao a ordem do trabalho: "Nova coleta"
   (usada uma vez por caso) dominava a pagina e "Conduzir" (usado dezenas de
   vezes) nascia abaixo da dobra. Agora a barra de etapas manda, a lista de
   casos fica sempre a vista na lateral e a execucao nunca sai da tela. */
.etapas{position:sticky;top:57px;z-index:35;display:flex;gap:6px;align-items:center;
padding:9px 24px;background:var(--vidro);backdrop-filter:blur(10px);
-webkit-backdrop-filter:blur(10px);border-bottom:1px solid var(--line);
overflow-x:auto}
.et{display:flex;align-items:baseline;gap:7px;padding:7px 15px;border-radius:22px;
border:1px solid var(--line2);background:var(--bg2);color:var(--mut);cursor:pointer;
font-family:inherit;font-size:12.5px;white-space:nowrap;
transition:background .15s,color .15s,border-color .15s}
.et:hover{border-color:var(--acc);color:var(--acc)}
.et .n{font-weight:800;font-size:11px;opacity:.75}
.et.on{background:var(--acc);border-color:var(--acc);color:#fff;font-weight:600}
.et.on .n{opacity:1}
/* Etapa ja cumprida NESTE caso. A barra deixa de ser so "onde estou" e passa a
   responder "o que falta": o check aparece no lugar do numero. */
.et .ck{display:none;font-weight:800;font-size:11px}
.et.feita .n{display:none}
.et.feita .ck{display:inline;color:var(--green)}
.et.feita{border-color:var(--green)}
.et.on.feita{border-color:var(--acc)}
.et.on.feita .ck{color:#fff}
/* Seta entre as etapas: deixa obvio que ha uma ordem, e nao tres secoes soltas */
.etapas .seta{color:var(--line2);flex:none;font-size:12px}
/* Fim de etapa: a ponte para a seguinte. Sem isto, terminar a coleta deixa o
   DBA olhando para uma tela pronta sem dizer para onde ir - e o fluxo vira
   tres secoes soltas que ele precisa costurar de cabeca. */
.prox{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-top:14px;
padding:12px 14px;border:1px solid var(--green);border-radius:12px;
background:color-mix(in srgb,var(--green) 8%,transparent)}
.prox .t{flex:1;min-width:220px;font-size:13px;line-height:1.5}
.prox .t b{color:var(--green)}
.etapas .dica{margin-left:auto;font-size:11px;color:var(--mut);white-space:nowrap}
@media(max-width:820px){.etapas .dica{display:none}}
.app{display:grid;grid-template-columns:330px minmax(0,1fr);gap:16px;
max-width:1680px;margin:0 auto;padding:16px 24px 40px;align-items:start}
@media(max-width:1080px){.app{grid-template-columns:minmax(0,1fr)}}
/* A lista acompanha a rolagem: trocar de caso e a acao mais frequente do dia */
@media(min-width:1081px){.lateral{position:sticky;top:112px}}
.etapa{display:none}.etapa.on{display:block}
/* Etapa que depende de caso selecionado, sem caso: diz o que fazer */
.pedecaso{padding:26px 20px;text-align:center;color:var(--mut);font-size:12.5px;
line-height:1.6}
.card{background:var(--bg2);border:1px solid var(--line);border-radius:14px;
padding:16px 18px;margin-bottom:16px;box-shadow:0 1px 2px var(--sombra);
transition:box-shadow .18s,border-color .18s}
.card:hover{box-shadow:0 4px 16px var(--sombra);border-color:var(--line2)}
.card h2{font-size:12px;color:var(--mut);text-transform:uppercase;letter-spacing:1px;
margin-bottom:12px;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
/* Ponto colorido no titulo do card: ancora visual para achar o bloco de relance */
.card h2::before{content:"";width:7px;height:7px;border-radius:50%;
background:var(--acc);flex:none}
.card.c-nova h2::before{background:var(--orange)}
.card.c-caso h2::before{background:var(--green)}
.card.c-kb h2::before{background:var(--roxo)}
.card.c-lista h2::before{background:var(--brand)}
.opts{display:flex;flex-wrap:wrap;gap:14px;margin:10px 0 2px;font-size:12.5px}
.opts label{display:flex;align-items:center;gap:6px;cursor:pointer}
/* Reincidencia do caso */
.reib{border:1px solid var(--line2);border-left:3px solid var(--red);
border-radius:10px;padding:10px 12px;margin-bottom:12px;font-size:12.5px}
.reib>b{display:block;margin-bottom:6px}
.reib .rl{display:flex;gap:9px;align-items:baseline;padding:4px 0}
.reib .nx{font-weight:800;color:var(--red);background:var(--vermelho-fraco);
border-radius:20px;padding:1px 8px;font-size:11px;white-space:nowrap}
.reib .q{display:block;color:var(--mut);font-size:11px}
/* Executar a verificacao no proprio framework. Fica logo abaixo do comando, com
   borda de acento: e a diferenca entre ler o passo e ter o passo respondido. */
.gexec{border:1px solid var(--line2);border-left:3px solid var(--acc);
border-radius:9px;padding:9px 11px;margin:9px 0;font-size:12.5px;
background:var(--bg2)}
.gexec .li{color:var(--mut)}
.gexec .btns{margin-top:9px;margin-bottom:0}
/* Confirmacao de que o passo JA rodou. Discreta de proposito: e contexto para
   a decisao (seguir ou repetir), nao um achado. */
.gfeito{margin-top:8px;font-size:12px;color:var(--green)}
/* Fechamento da conducao. Ganha peso visual porque e a unica tela do guiado
   que AFIRMA algo - o resto pergunta e coleta. */
.gfim{border:1px solid var(--line2);border-left:3px solid var(--green);
border-radius:9px;padding:11px 13px;margin:9px 0;background:var(--bg2)}
.gfim h4{margin:0 0 8px;font-size:14px}
.gfim .li{font-size:12.5px;margin:4px 0;color:var(--txt)}
.gfim .li.ok{color:var(--green)}
.gfim .li.de{color:var(--mut)}
.gfim .li.av{color:var(--orange)}
.gfim .pq{color:var(--mut);font-weight:400}
.gfim .btns{margin-top:11px;margin-bottom:0}
/* Campo de &parametro: rotulo e campo na mesma linha, monoespacado - o valor e
   um SQL_ID ou nome de objeto, e digito errado ali analisa o objeto errado. */
.gexec .gp{display:flex;align-items:center;gap:9px;margin-top:8px}
.gexec .gp label{font-family:ui-monospace,Consolas,monospace;font-weight:700;
min-width:92px;color:var(--txt)}
.gexec .gp input{flex:1;font-family:ui-monospace,Consolas,monospace;
font-size:12px;padding:6px 9px;border:1px solid var(--line2);
border-radius:7px;background:var(--bg);color:var(--txt)}
/* Saida colada: o que a IA entendeu do retorno do comando.
   Cada linha carrega o "porque" - leitura sem lastro nao pode ter cara de
   conclusao, e o DBA precisa poder discordar do que foi entendido. */
#fgsaida{width:100%;font-family:ui-monospace,Consolas,monospace;font-size:12px;
line-height:1.45;padding:9px 11px;border:1px solid var(--line2);border-radius:9px;
background:var(--bg2);color:var(--txt);resize:vertical}
#fgsaida:focus{outline:2px solid var(--acc);outline-offset:-1px}
/* Contador da caixa: confirma que o texto CHEGOU (colar dentro de uma area
   monoespacada rolada nao da nenhum retorno visual). */
.cnt{float:right;text-transform:none;letter-spacing:0;color:var(--mut);
font-size:10.5px}
kbd{font-family:inherit;font-size:9.5px;font-weight:700;border-radius:4px;
padding:1px 5px;margin-left:6px;background:rgba(255,255,255,.22);
border:1px solid rgba(255,255,255,.35);vertical-align:1px}
.btn:not(.pri) kbd{background:var(--bg3);border-color:var(--line2);
color:var(--mut)}
/* O que o motor sabe ler: descoberta, nao decoracao. Recolhido por padrao para
   nao competir com a caixa de colar. */
.oqueleio{margin-top:9px}
.oqueleio>summary{cursor:pointer;font-size:11.5px;color:var(--mut);
padding:4px 2px;list-style:none;user-select:none}
.oqueleio>summary::-webkit-details-marker{display:none}
.oqueleio>summary::before{content:'\25B8';display:inline-block;margin-right:6px;
transition:transform .12s}
.oqueleio[open]>summary::before{transform:rotate(90deg)}
.oqueleio>summary:hover{color:var(--txt)}
.leios{display:flex;flex-wrap:wrap;gap:5px;margin-top:5px}
.leio{font-size:11px;padding:3px 9px;border-radius:20px;background:var(--bg3);
color:var(--mut);border:1px solid var(--line);cursor:help}
.leio b{font-weight:600;color:var(--txt)}
.lei{margin-top:10px;border:1px solid var(--line2);border-radius:11px;padding:11px 13px;
background:var(--bg2)}
.lei h5{font-size:11px;text-transform:uppercase;letter-spacing:.05em;
color:var(--mut);margin-bottom:8px}
.lei-a{display:flex;flex-direction:column;gap:1px;padding:6px 0 6px 10px;
border-left:3px solid var(--line2);margin-bottom:6px;font-size:12.5px}
.lei-a.crit{border-left-color:var(--red)}
.lei-a.warn{border-left-color:var(--orange)}
.lei-a.info{border-left-color:var(--acc)}
.lei-a .q{color:var(--mut);font-size:11.5px}
.lei-m{margin-top:9px;padding-top:9px;border-top:1px dashed var(--line2);
font-size:12.5px;line-height:1.6}
.lei-nada{margin-top:10px}

/* Racicionio: hipoteses concorrentes com o que sustenta e o que refuta.
   A hipotese refutada FICA na tela, apagada e riscada - o descarte e o que
   impede o proximo plantao de refazer o caminho ja andado. */
.hips{display:flex;flex-direction:column;gap:8px;margin:10px 0}
.hip{border:1px solid var(--line2);border-radius:11px;padding:10px 12px;
font-size:12.5px;border-left:3px solid var(--line2)}
.hip.sustentada{border-left-color:var(--red);background:var(--vermelho-fraco)}
.hip.possivel{border-left-color:var(--orange)}
.hip.consequencia{border-left-color:var(--mut);border-left-style:dashed}
.hip.refutada{opacity:.62}
/* Classe de risco: o que PARA o banco ou impede a recuperacao nao pode ficar
   com a mesma cara de "esta lento". */
.hip .cl{font-size:9.5px;font-weight:800;text-transform:uppercase;
letter-spacing:.04em;border-radius:20px;padding:2px 8px;margin-left:7px;
white-space:nowrap;background:var(--vermelho-fraco);color:var(--red)}
.hip .cl.perda{background:var(--roxo-fraco);color:var(--roxo)}
.hip .cab{display:flex;gap:9px;align-items:baseline;justify-content:space-between}
.hip .rot{font-weight:700}
.hip.refutada .rot{text-decoration:line-through}
.hip .ver{font-size:10.5px;font-weight:800;text-transform:uppercase;
letter-spacing:.04em;border-radius:20px;padding:2px 9px;white-space:nowrap;
border:1px solid var(--line2);color:var(--mut)}
.hip.sustentada .ver{color:#fff;background:var(--red);border-color:var(--red)}
.hip.possivel .ver{color:var(--orange);border-color:var(--orange)}
.hip .ex{color:var(--mut);margin-top:4px;line-height:1.5}
.hip .li{margin-top:5px;display:flex;gap:7px;align-items:baseline}
.hip .li .m{flex:none;font-weight:800;font-size:11px}
.hip .li.pro .m{color:var(--red)}
.hip .li.con .m{color:var(--green)}
.hip .dec{margin-top:6px;padding-top:6px;border-top:1px dashed var(--line2);
color:var(--mut)}
.hip .dec b{color:var(--txt)}
/* A narrativa e o que vai para o chamado e para a passagem de turno */
.narr{border:1px solid var(--line2);border-left:3px solid var(--acc);
border-radius:11px;padding:11px 13px;font-size:12.5px;line-height:1.6;
margin-bottom:10px}
/* Aviso de foco visivel: navegacao por teclado nao pode ficar invisivel */
:focus-visible{outline:2px solid var(--acc);outline-offset:2px;border-radius:6px}
/* Toast: confirmacao que nao empurra o layout */
#toasts{position:fixed;right:18px;bottom:18px;z-index:80;display:flex;
flex-direction:column;gap:8px;max-width:380px}
.toast{background:var(--bg2);border:1px solid var(--line2);border-left-width:4px;
border-radius:10px;padding:10px 14px;font-size:12.5px;line-height:1.5;
box-shadow:0 10px 30px var(--sombra-forte);animation:tin .22s ease}
.toast.ok{border-left-color:var(--green)}
.toast.err{border-left-color:var(--red)}
.toast.sai{animation:tout .2s ease forwards}
@keyframes tin{from{opacity:0;transform:translateX(16px)}to{opacity:1;transform:none}}
@keyframes tout{to{opacity:0;transform:translateX(16px)}}
@media (prefers-reduced-motion:reduce){
.card{transition:none}.toast{animation:none}}
label{display:block;font-size:11px;color:var(--mut);text-transform:uppercase;
letter-spacing:.5px;margin:10px 0 4px}
input,select,textarea{width:100%;background:var(--bg2);border:1px solid var(--line2);
border-radius:6px;padding:7px 10px;font-size:13px;color:var(--txt);font-family:inherit}
input:focus,select:focus,textarea:focus{outline:2px solid var(--acc);outline-offset:-1px}
.row{display:flex;gap:10px}.row>*{flex:1}
/* Botao que NAO parece botao: e uma saida secundaria (trazer saida de outro
   terminal) e nao pode competir com a acao principal do passo. */
.btn.lnk{background:none;border:none;color:var(--acc);padding:2px 0;
font-weight:600;text-decoration:underline}
.btn.lnk:hover{background:none;color:var(--azul-forte)}
.btn{background:var(--bg2);border:1px solid var(--line2);border-radius:6px;
padding:7px 14px;cursor:pointer;font-size:12px;color:var(--txt)}
.btn:hover:not(:disabled){background:var(--acc);border-color:var(--acc);color:#fff}
.btn:disabled{opacity:.5;cursor:not-allowed}
.btn.pri{background:var(--acc);border-color:var(--acc);color:#fff;font-weight:600}
.btn.pri:hover:not(:disabled){filter:brightness(1.1)}
.btns{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
.radios{display:flex;gap:14px;flex-wrap:wrap;margin-top:4px}
.radios label{display:flex;align-items:center;gap:5px;text-transform:none;
font-size:13px;color:var(--txt);letter-spacing:0;margin:0;cursor:pointer}
.radios input{width:auto;accent-color:var(--acc)}
/* Opcao com legenda: o DBA precisa saber o que cada modo faz (e o que custa em
   licenca) sem abrir a documentacao. */
.modos{display:flex;flex-direction:column;gap:6px;margin-top:4px}
.modos label.modo{display:flex;align-items:flex-start;gap:9px;margin:0;padding:9px 11px;
border:1px solid var(--line);border-radius:8px;cursor:pointer;text-transform:none;
letter-spacing:0;color:var(--txt);font-size:13px;background:var(--bg2)}
.modos label.modo:hover{border-color:var(--line2);background:var(--bg3)}
.modos label.modo:has(input:checked){border-color:var(--acc);background:var(--acc-fraco)}
.modos input{width:auto;accent-color:var(--acc);margin-top:2px;flex:none}
/* So o TITULO da opcao vira bloco. Um `.modos b` solto tambem pegava os <b>
   usados no meio da descricao e picava as frases em linhas soltas. */
.modos .modo>span>b{display:block;font-weight:600;font-size:13px}
.modos small{display:block;color:var(--mut);font-size:11.5px;line-height:1.5;margin-top:2px}
.modos small b{font-weight:600;color:var(--txt)}
/* Outros modos: recolhidos por padrao. O <summary> NAO pode ser generico -
   quem nao souber que existe modo sem pack acaba consumindo Diagnostics Pack
   a toa, entao a economia de licenca vai escrita na propria linha. */
.maismodos{margin-top:6px}
.maismodos>summary{cursor:pointer;font-size:11.5px;color:var(--mut);
padding:5px 2px;list-style:none;user-select:none}
.maismodos>summary::-webkit-details-marker{display:none}
.maismodos>summary::before{content:'\25B8';display:inline-block;margin-right:6px;
transition:transform .12s}
.maismodos[open]>summary::before{transform:rotate(90deg)}
.maismodos>summary:hover{color:var(--txt)}
.maismodos>summary.alt{color:var(--orange);font-weight:600}
.maismodos .modos{margin-top:2px}
.tagl{display:inline-block;font-size:9.5px;font-weight:700;padding:1px 6px;
border-radius:20px;margin-left:6px;vertical-align:1px;letter-spacing:.3px}
.tagl.pack{background:var(--laranja-fraco);color:var(--orange)}
.tagl.livre{background:var(--verde-fraco);color:var(--green)}
.tagl.rec{background:var(--acc-fraco);color:var(--azul-forte)}
.tagl.esc{background:var(--laranja-fraco);color:var(--orange)}
/* ── Advisor: opt-in que quebra o read-only ───────────────────
   Fica recuado sob o Completo (a que pertence) e some nos outros modos: o
   advisor parte da regressao apurada no AWR, que so o Completo coleta. */
.flagadv{display:none;align-items:flex-start;gap:9px;margin:2px 0 0 26px;
padding:8px 11px;border:1px dashed var(--orange);border-radius:8px;
cursor:pointer;text-transform:none;letter-spacing:0;font-size:12.5px;
color:var(--txt);background:var(--laranja-fraco)}
.modos:has(input[value="completo"]:checked) .flagadv{display:flex}
.flagadv input{width:auto;accent-color:var(--orange);margin-top:2px;flex:none}
.flagadv b{font-weight:600}
.flagadv small{display:block;color:var(--mut);font-size:11px;line-height:1.45;
margin-top:2px}
.flagadv:has(input:checked){border-style:solid;background:var(--laranja-fraco);
box-shadow:inset 3px 0 0 var(--orange)}
/* Selo no titulo do cartao: enquanto o advisor esta marcado, o cabecalho nao
   pode continuar prometendo "read-only". */
.selo-esc{font-size:10px;font-weight:700;letter-spacing:.4px;padding:2px 8px;
border-radius:20px;background:var(--orange);color:#fff;margin-left:8px;
vertical-align:2px}
.btn.perigo{background:var(--orange);border-color:var(--orange);color:#fff;
font-weight:600}
.btn.perigo:hover:not(:disabled){filter:brightness(1.08);
background:var(--orange);border-color:var(--orange)}
.modadv .box{width:min(620px,100%)}
.modadv .corpo{padding:16px 22px 20px;overflow:auto}
.modadv .advsub{font-size:12.5px;color:var(--mut);line-height:1.5}
.advlist{margin:0;padding-left:18px;font-size:12.5px;line-height:1.6}
.advlist li{margin-bottom:7px}
.advlist code{font-size:11px;background:var(--bg3);padding:1px 5px;
border-radius:4px}
.advrb{margin-top:12px;padding:10px 12px;border-radius:8px;
background:var(--bg2);border-left:3px solid var(--line2);font-size:12px;
line-height:1.55;color:var(--mut)}
.advrb code{font-size:11px;background:var(--bg3);padding:1px 5px;border-radius:4px}
/* Legenda curta ao lado do titulo de um bloco */
.leg{font-size:11px;color:var(--mut);text-transform:none;letter-spacing:0;
font-weight:400;margin-left:8px}
/* ── Base de conhecimento ────────────────────────────────── */
.pbs{display:flex;flex-direction:column;gap:5px;max-height:250px;overflow:auto}
.pb{border:1px solid var(--line);border-radius:8px;padding:8px 10px;cursor:pointer;
background:var(--bg2)}
.pb:hover{border-color:var(--acc);background:var(--bg3)}
.pb.sel{border-color:var(--acc);background:var(--acc-fraco)}
.pb .t{font-weight:600;font-size:12.5px;display:flex;justify-content:space-between;
gap:8px;align-items:baseline}
.pb .q{font-size:11px;color:var(--mut);line-height:1.5;margin-top:3px}
.pb .n{font-size:10px;color:var(--mut);white-space:nowrap;font-weight:400}
/* Passo do playbook */
.passo{border:1px solid var(--line);border-left-width:3px;border-radius:8px;
padding:10px 12px;margin-bottom:8px;background:var(--bg2)}
.passo.r-leitura{border-left-color:var(--green)}
.passo.r-mudanca{border-left-color:var(--orange)}
.passo.r-destrutivo{border-left-color:var(--red)}
.passo h4{margin:0 0 3px;font-size:13px;display:flex;gap:8px;align-items:baseline;
justify-content:space-between}
.passo .num{color:var(--mut);font-weight:400;font-size:11px;margin-right:5px}
.passo .obj{font-size:11.5px;color:var(--mut);line-height:1.55;margin-bottom:7px}
.passo pre{background:var(--codigo-bg);color:var(--codigo-txt);border-radius:6px;padding:9px 11px;
margin:0 0 7px;font-family:Consolas,'Courier New',monospace;font-size:11.5px;
line-height:1.55;white-space:pre-wrap;overflow-x:auto}
.passo .bloco{font-size:11.5px;line-height:1.55;margin-top:6px;padding:7px 9px;
border-radius:6px}
.passo .olhar{background:var(--acc-fraco);color:var(--azul-forte)}
.passo .arma{background:var(--laranja-fraco);color:var(--orange)}
.passo .antes{background:var(--vermelho-fraco);color:var(--red)}
.passo .bloco b{display:block;font-size:9.5px;text-transform:uppercase;
letter-spacing:.5px;margin-bottom:2px;opacity:.75}
.tagr{font-size:9.5px;font-weight:700;padding:2px 7px;border-radius:20px;
white-space:nowrap;letter-spacing:.3px}
.tagr.leitura{background:var(--verde-fraco);color:var(--green)}
.tagr.mudanca{background:var(--laranja-fraco);color:var(--orange)}
.tagr.destrutivo{background:var(--vermelho-fraco);color:var(--red)}
.tagr.onde{background:var(--bg3);color:var(--mut);font-weight:600}
/* ── Playbook em primeiro plano (modal) ──────────────────────
   A pagina anterior continua visivel e desfocada atras: da contexto de onde
   voce estava e deixa obvio que basta clicar fora para voltar. */
.mod{position:fixed;inset:0;z-index:60;display:flex;align-items:center;
justify-content:center;padding:28px;background:rgba(15,23,42,.55);
backdrop-filter:blur(3px);-webkit-backdrop-filter:blur(3px);
opacity:0;visibility:hidden;transition:opacity .22s ease,visibility .22s ease}
.mod.on{opacity:1;visibility:visible}
.mod .box{background:var(--bg);border-radius:16px;width:min(980px,100%);
max-height:100%;display:flex;flex-direction:column;overflow:hidden;
box-shadow:0 24px 70px rgba(15,23,42,.42);
transform:translateY(14px) scale(.985);transition:transform .24s cubic-bezier(.2,.8,.3,1)}
.mod.on .box{transform:none}
.mod .top{padding:16px 22px;border-bottom:1px solid var(--line);background:var(--bg2);
display:flex;gap:14px;align-items:flex-start;flex:none}
.mod .top h3{margin:0 0 5px;font-size:16px;line-height:1.35}
.mod .top .sb{font-size:11.5px;color:var(--mut);line-height:1.55}
.mod .body{padding:18px 22px 22px;overflow:auto}
/* Botao de fechar: alvo grande e sempre no mesmo lugar */
.xis{flex:none;width:32px;height:32px;border-radius:9px;border:1px solid var(--line);
background:var(--bg);color:var(--mut);font-size:17px;line-height:1;cursor:pointer;
transition:background .15s,color .15s,border-color .15s}
.xis:hover{background:var(--vermelho-fraco);color:var(--red);border-color:var(--red)}
/* Resumo de risco no cabecalho: quantos passos de cada tipo, antes de rolar */
.rsum{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
/* Entrada escalonada dos passos - da a sensacao de fluidez sem atrasar a leitura */
.mod.on .passo{animation:sobe .26s ease both}
@keyframes sobe{from{opacity:0;transform:translateY(7px)}to{opacity:1;transform:none}}
.passo{position:relative}
/* Cores fixas de proposito: este botao fica sobre o bloco de codigo, que e
   escuro nos DOIS temas. Usar variavel aqui o deixaria invisivel no claro. */
.passo .cp{position:absolute;top:8px;right:9px;font-size:10px;padding:3px 8px;
border-radius:6px;border:1px solid #334155;background:#1e293b;color:#94a3b8;
cursor:pointer;opacity:0;transition:opacity .15s,background .15s}
.passo:hover .cp{opacity:1}
.passo .cp:hover{background:#334155;color:#e2e8f0}
.pre-wrap{position:relative}
@media (max-width:700px){.mod{padding:0}.mod .box{border-radius:0;max-height:100%;
height:100%;width:100%}}
@media (prefers-reduced-motion:reduce){
.mod,.mod .box{transition:none}.mod.on .passo{animation:none}}
/* ── Guiado ──────────────────────────────────────────────────
   A Matriz de Risco e um bloco vermelho de largura inteira, e nao um selo ao
   lado do titulo: ela precisa interromper a leitura de quem esta com pressa. */
.gmat{border:1px solid var(--red);border-radius:8px;padding:10px 12px;
margin:0 0 9px;background:var(--vermelho-fraco)}
.gmat h5{font-size:11px;text-transform:uppercase;letter-spacing:.5px;
color:var(--red);margin:0 0 6px}
.gmat .li{font-size:11.5px;line-height:1.55;margin-top:4px;color:var(--txt)}
.gmat .li b{color:var(--red)}
.gmat .apr{margin-top:7px;font-size:11px;font-weight:700;color:var(--red)}
.gprog{display:flex;align-items:center;gap:9px;margin-bottom:10px;font-size:11.5px;
color:var(--mut)}
.gbar{flex:1;height:6px;border-radius:3px;background:var(--bg3);overflow:hidden}
.gbar i{display:block;height:100%;background:var(--acc)}
/* Pendencia em aberto e a unica coisa da aba que aparece mesmo com o roteiro
   fechado: acao executada e verificacao pendente e o estado mais perigoso. */
.gpend{border:1px solid var(--orange);border-radius:8px;padding:10px 12px;
margin:0 0 12px;background:var(--laranja-fraco)}
.gpend h5{font-size:11.5px;color:var(--orange);margin:0 0 7px}
.gpend .it{display:flex;gap:8px;align-items:flex-start;padding:5px 0;
border-top:1px solid rgba(180,83,9,.18);font-size:11.5px;line-height:1.5}
.gpend .it:first-of-type{border-top:none}
.gpend .it .q{display:block;color:var(--mut);font-size:10.5px;margin-top:2px}
.hint{font-size:11px;color:var(--mut);margin-top:5px;line-height:1.5}
#log{background:var(--codigo-bg);color:var(--codigo-txt);border-radius:8px;padding:12px;height:300px;
overflow:auto;font-family:Consolas,'Courier New',monospace;font-size:12px;
white-space:pre-wrap;line-height:1.5}
/* ── Execucao: fora das etapas, sempre na tela ───────────────
   A coleta e disparada na etapa 1 e o DBA vai conduzir OUTRO caso na etapa 2.
   Se o log morasse dentro de uma etapa, ele sumiria justamente enquanto roda -
   e a caixa de senha some junto. Por isso a execucao e uma faixa fixa: fechada
   mostra o estado em uma linha, e abre sozinha quando ha comando rodando ou
   pergunta esperando resposta. */
.exec{position:fixed;left:0;right:0;bottom:0;z-index:45;background:var(--vidro);
backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);
border-top:1px solid var(--line2);box-shadow:0 -2px 14px var(--sombra)}
.exec .barra{display:flex;align-items:center;gap:10px;padding:9px 24px;
cursor:pointer;font-size:12px;color:var(--mut)}
.exec .barra .qual{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.exec .barra .abrir{font-size:11px;color:var(--acc);white-space:nowrap}
.exec .corpo{display:none;padding:0 24px 16px;max-height:58vh;overflow:auto}
.exec.aberto .corpo{display:block}
.exec .st{margin:0}
/* Abas da faixa: SAIDA e HISTORICO.
   Antes os dois historicos ficavam empilhados EMBAIXO do log, sempre. Quem
   abria a faixa queria a saida do comando que esta rodando agora e recebia
   junto duas listas do que ja passou - rolagem a mais no minuto em que menos
   se tem paciencia. O historico nao sumiu (e ele que diz o que ja rodou nesta
   maquina quando o plantao troca): virou a segunda aba. */
.exec .abas{margin:0 0 10px;position:sticky;top:0;z-index:1;
background:var(--vidro);backdrop-filter:blur(10px)}
.exec .aba{padding:7px 12px;font-size:12px}
.epane{display:none}.epane.on{display:block}
/* O conteudo nao pode terminar embaixo da faixa fechada */
body{padding-bottom:46px}
/* Caixa de pergunta: o CLI parou e espera resposta. Fica colada no log, com a
   cor de atencao, porque e a UNICA coisa acionavel na tela naquele momento. */
.perg{margin-top:10px;border:1px solid var(--acc);border-radius:10px;
padding:10px 12px;background:rgba(56,139,253,.08)}
.perg .pq{font-size:13px;font-weight:600;margin-bottom:8px}
.perg .lin{display:flex;gap:8px;align-items:center}
.perg input{flex:1;margin:0}
.st{display:flex;align-items:center;gap:8px;margin-bottom:8px;font-size:12px;
color:var(--mut)}
.dot{width:9px;height:9px;border-radius:50%;background:var(--mut);flex:none}
.dot.run{background:var(--acc);animation:p 1s infinite}
.dot.ok{background:var(--green)}.dot.err{background:var(--red)}
@keyframes p{50%{opacity:.25}}
.casos{max-height:330px;overflow:auto;border:1px solid var(--line);border-radius:8px}
.caso{display:flex;align-items:center;gap:10px;padding:9px 12px;cursor:pointer;
border-bottom:1px solid var(--line)}
.caso:last-child{border-bottom:none}
.caso:hover{background:var(--bg3)}.caso.sel{background:var(--acc-fraco)}
.caso .nm{font-weight:600;flex:1;word-break:break-all}
.caso .mt{font-size:11px;color:var(--mut)}
.pill{font-size:10px;font-weight:700;padding:3px 9px;border-radius:20px;flex:none}
.p-inv{background:var(--acc-fraco);color:var(--azul-forte)}
.p-esc{background:var(--laranja-fraco);color:var(--orange)}
.p-res{background:var(--verde-fraco);color:var(--green)}
.p-none{background:var(--bg3);color:var(--mut)}
.p-agu{background:var(--roxo-fraco);color:var(--roxo)}
.msg{margin-top:10px;padding:9px 12px;border-radius:8px;font-size:12px;display:none}
.msg.ok{display:block;background:var(--verde-fraco);color:var(--verde-txt)}
.msg.err{display:block;background:var(--vermelho-fraco);color:var(--red)}
.aviso{border-left:4px solid var(--orange);background:var(--laranja-fraco);
padding:10px 12px;border-radius:6px;font-size:12px;color:var(--orange);
line-height:1.5;margin-top:12px}
.vazio{padding:20px;text-align:center;color:var(--mut);font-size:12px}

/* Artefatos do caso: cartao com icone, e nao mais uma fila de botoes cinzas
   iguais. O que ainda nao foi gerado fica apagado e SEM cursor de clique -
   some a duvida entre 'nao existe' e 'nao funcionou'. */
.artes{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px}
.arte{display:flex;align-items:center;gap:9px;text-align:left;padding:10px 12px;
background:var(--bg2);border:1px solid var(--line2);border-radius:9px;cursor:pointer;
color:var(--txt);font-family:inherit;width:100%}
.arte:hover:not(:disabled){border-color:var(--acc);background:var(--acc-fraco)}
.arte:disabled{opacity:.42;cursor:not-allowed}
.arte svg{flex:0 0 auto;width:19px;height:19px}
.arte .t{font-size:12.5px;font-weight:600;line-height:1.25}
.arte .s{font-size:10.5px;color:var(--mut);display:block;font-weight:400}

/* Cadastro de servidor novo. Borda laranja e nao vermelha de proposito: nao e
   uma falha, e uma pergunta - o nome pode estar certo e faltar cadastrar. */
.novaconn{border:1px solid var(--orange);border-left-width:4px;
background:var(--laranja-fraco);border-radius:8px;padding:12px 14px;margin-top:12px}
.novaconn .nctit{font-weight:700;font-size:13px;color:var(--orange);margin-bottom:3px}
.novaconn .ncq{font-size:12px;color:var(--mut);line-height:1.5;margin-bottom:9px}
.novaconn label{margin-top:8px}
.ncpb{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-bottom:4px}
.ncpb .l{font-size:11.5px;color:var(--mut)}
.ncsug{background:var(--bg2);border:1px solid var(--line2);border-radius:99px;
padding:3px 11px;font-size:11.5px;cursor:pointer;color:var(--txt)}
.ncsug:hover{background:var(--acc);border-color:var(--acc);color:#fff}
.sub2{font-size:11px;color:var(--mut);text-transform:uppercase;letter-spacing:.5px;
margin:16px 0 8px;padding-top:12px;border-top:1px solid var(--line)}
/* Primeiro subtitulo de um painel ja tem a aba acima: a linha e o respiro
   extra so repetiriam a separacao que a aba faz. */
.epane>.sub2:first-child{margin-top:0;padding-top:0;border-top:none}
/* ── Explicacao sob demanda ──────────────────────────────────
   O painel nasceu explicando tudo na tela: cada campo trazia o porque em
   volta. Junto, virava parede - e parede ninguem le, nem a frase que
   importava naquele minuto. O texto continua inteiro (ele responde duvida
   real de quem chega), mas RECOLHIDO: some do caminho de quem ja sabe e fica
   a um clique de quem nao sabe. */
.expl{margin:6px 0 2px}
.expl>summary{font-size:11.5px;color:var(--mut);cursor:pointer;
list-style:none;display:inline-flex;align-items:center;gap:5px;
border-bottom:1px dotted var(--line2);padding-bottom:1px}
.expl>summary::-webkit-details-marker{display:none}
.expl>summary::before{content:"?";display:inline-flex;align-items:center;
justify-content:center;width:14px;height:14px;border-radius:50%;
background:var(--bg3);font-size:10px;font-weight:700;line-height:1}
.expl>summary:hover{color:var(--acc)}
.expl[open]>summary{margin-bottom:6px}
.expl .hint{margin-top:0}
/* Proposta de preenchimento: a caixinha e o alvo de clique, e o rotulo inteiro
   e clicavel (alvo grande, na mao de quem esta no meio de um incidente). */
.prop{display:flex;gap:10px;align-items:flex-start;padding:10px 12px;
border:1px solid var(--line2);border-radius:8px;margin-bottom:8px;
background:var(--bg2);cursor:pointer;font-size:12.5px;line-height:1.5}
.prop:hover{border-color:var(--acc)}
.prop input{margin-top:3px;flex:none;width:16px;height:16px;cursor:pointer}
.prop:has(input:checked){border-color:var(--acc);background:var(--acc-fraco)}
.prop .mt{font-size:11.5px;color:var(--mut);margin-top:3px}
/* Linha do tempo do caso */
.tl{list-style:none;margin:4px 0 0;padding:0 0 0 18px;border-left:2px solid var(--line2)}
.tl li{position:relative;padding:0 0 12px 14px;font-size:12px;line-height:1.5}
.tl li::before{content:'';position:absolute;left:-23px;top:4px;width:9px;height:9px;
border-radius:50%;background:var(--mut);border:2px solid var(--bg2)}
.tl li.t-criado::before{background:var(--mut)}
.tl li.t-hipotese_inicial::before,.tl li.t-hipotese_atual::before{background:var(--acc)}
.tl li.t-hipotese_descartada::before{background:var(--line2)}
.tl li.t-acao::before{background:var(--green)}
.tl li.t-acao.r-alto::before{background:var(--red)}
.tl li.t-escalado::before{background:var(--orange)}
.tl li.t-resolvido::before{background:var(--green)}
.tl .q{color:var(--mut);font-size:11px;margin-right:6px;font-variant-numeric:tabular-nums}
.tl .tag{font-size:10px;font-weight:700;color:var(--mut);text-transform:uppercase;
letter-spacing:.4px;margin-right:5px}
.tl li.t-hipotese_descartada{opacity:.65;text-decoration:line-through}
.tl .det{color:var(--mut);font-size:11px;display:block}
.tl .r{font-size:10px;font-weight:700;padding:1px 7px;border-radius:20px;
background:var(--bg3);color:var(--mut);margin-left:5px}
.tl .r.alto{background:var(--vermelho-fraco);color:var(--red)}
/* Historico de execucoes */
.hist{list-style:none;margin:10px 0 0;padding:0;font-size:12px}
.hist li{display:flex;gap:8px;align-items:center;padding:5px 0;
border-top:1px solid var(--line);cursor:pointer}
.hist li:hover{color:var(--acc)}
.hist .c{flex:1;font-family:Consolas,monospace;font-size:11px;word-break:break-all}
.hist .q{color:var(--mut);font-size:11px}
/* ── Selo de Health Score ────────────────────────────────────
   O numero que responde "quao ruim esta este banco". Fica a esquerda do nome
   porque e o que faz o olho parar na linha certa; a cor vem da severidade que
   o nucleo ja decidiu, nunca recalculada aqui. */
.sc{flex:none;width:38px;height:38px;border-radius:10px;display:flex;
flex-direction:column;align-items:center;justify-content:center;
font-weight:800;font-size:14px;line-height:1;border:1px solid transparent;
font-variant-numeric:tabular-nums}
.sc small{font-size:7.5px;font-weight:700;text-transform:uppercase;
letter-spacing:.4px;opacity:.8;margin-top:1px}
.sc.s-crit{background:var(--vermelho-fraco);color:var(--red);border-color:var(--red)}
.sc.s-warn{background:var(--laranja-fraco);color:var(--orange);border-color:var(--orange)}
.sc.s-ok{background:var(--verde-fraco);color:var(--green);border-color:var(--green)}
.sc.s-info{background:var(--acc-fraco);color:var(--azul-forte)}
/* Caso sem relatorio: tracejado deixa claro que e AUSENCIA de dado, nao zero */
.sc.s-na{background:var(--bg3);color:var(--mut);border:1px dashed var(--line2);
font-size:11px;font-weight:600}
/* ── Busca e filtros da lista ────────────────────────────────
   Com 37 casos a lista rolava sem rumo. O filtro e local (o estado ja esta no
   navegador), entao responde a cada tecla sem ida ao servidor. */
.filtros{display:flex;gap:6px;flex-wrap:wrap;margin:8px 0 10px}
.chip{font-size:11px;padding:4px 11px;border-radius:20px;cursor:pointer;
border:1px solid var(--line2);background:var(--bg2);color:var(--mut);
transition:background .15s,color .15s,border-color .15s}
.chip:hover{border-color:var(--acc);color:var(--acc)}
.chip.on{background:var(--acc);border-color:var(--acc);color:#fff;font-weight:600}
.chip .qt{opacity:.7;margin-left:4px;font-variant-numeric:tabular-nums}
.caso .ach{font-size:10px;color:var(--mut);white-space:nowrap}
/* ── Abas do caso ────────────────────────────────────────────
   Antes: hipotese, acao, resolucao, playbooks, linha do tempo e drill-down
   empilhados num bloco unico de ~15 campos. O DBA rolava procurando o campo.
   Cada aba agora e uma pergunta so. */
.abas{display:flex;gap:2px;border-bottom:1px solid var(--line);margin:4px 0 14px;
overflow-x:auto}
.aba{padding:8px 13px;font-size:12.5px;cursor:pointer;color:var(--mut);
border:none;background:none;border-bottom:2px solid transparent;white-space:nowrap;
font-family:inherit;transition:color .15s,border-color .15s}
.aba:hover{color:var(--txt)}
.aba.on{color:var(--acc);border-bottom-color:var(--acc);font-weight:600}
.aba .b{display:inline-block;min-width:16px;padding:0 4px;margin-left:5px;
border-radius:10px;background:var(--bg3);color:var(--mut);font-size:10px;
font-weight:700;vertical-align:1px}
.aba.on .b{background:var(--acc);color:#fff}
.pane{display:none}.pane.on{display:block}
/* Primeiro campo de cada aba nao precisa do respiro de topo do label */
.pane>label:first-child{margin-top:0}
/* ── Alternador de tema ──────────────────────────────────── */
.tema{width:34px;height:34px;padding:0;font-size:15px;line-height:1;
display:flex;align-items:center;justify-content:center}
/* ── Esqueleto de carregamento ───────────────────────────────
   Substitui o texto "carregando...": mostra a FORMA do que vem, entao a tela
   nao salta quando o conteudo chega. */
.sk{padding:10px 12px;border-bottom:1px solid var(--line);display:flex;
align-items:center;gap:10px}
.sk i{display:block;height:10px;border-radius:5px;flex:1;
background:linear-gradient(90deg,var(--bg3) 25%,var(--line) 37%,var(--bg3) 63%);
background-size:400% 100%;animation:sk 1.3s ease infinite}
.sk i.q{flex:none;width:38px;height:38px;border-radius:10px}
@keyframes sk{0%{background-position:100% 50%}100%{background-position:0 50%}}
@media (prefers-reduced-motion:reduce){.sk i{animation:none}}
</style></head><body>
<div class="header">
<!-- Mascote ParraDibiei. SVG inline de proposito: o painel roda offline e a
     partir da pasta portatil, entao nao pode depender de arquivo de imagem
     nem de CDN. Vetor tambem nao borra em tela HiDPI e acompanha o tema. -->
<!--MARCA-->
<div><h1>Painel de Troubleshooting Oracle</h1>
<div class="sub">coleta read-only &middot; local (127.0.0.1) &middot; DBA Master</div></div>
<div class="hdr-right">
<div class="kpis" id="kpis">
<div class="kpi" title="casos com evidencia coletada">
<span class="n" id="kCasos">-</span><span class="l">casos</span></div>
<div class="kpi crit" title="casos ainda em investigacao ou escalados">
<span class="n" id="kAbertos">-</span><span class="l">abertos</span></div>
<div class="kpi" title="conexoes no repositorio stringConexao/">
<span class="n" id="kConn">-</span><span class="l">conexoes</span></div>
<div class="kpi viv" title="playbooks na base de conhecimento">
<span class="n" id="kKb">-</span><span class="l">playbooks</span></div>
</div>
<button class="btn tema" id="btema" onclick="trocaTema()"
 title="Alternar tema claro/escuro" aria-label="Alternar tema">&#9788;</button></div></div>

<!-- Barra de etapas: a tela passa a seguir a ordem do trabalho, nao a ordem
     em que os cards foram escritos. Quem chega e guiado; quem ja domina quase
     nunca clica aqui, porque a etapa acompanha o que foi feito (selecionar um
     caso leva para Conduzir, resolver leva para Redigir). -->
<div class="etapas" role="tablist" aria-label="Etapas do troubleshooting">
<button class="et on" id="et-1" role="tab" aria-selected="true"
 onclick="mostraEtapa(1)"><span class="n">1</span><span class="ck"
 aria-hidden="true">&#10003;</span> Coletar</button>
<span class="seta" aria-hidden="true">&#9656;</span>
<button class="et" id="et-2" role="tab" aria-selected="false"
 onclick="mostraEtapa(2)"><span class="n">2</span><span class="ck"
 aria-hidden="true">&#10003;</span> Conduzir</button>
<span class="seta" aria-hidden="true">&#9656;</span>
<button class="et" id="et-3" role="tab" aria-selected="false"
 onclick="mostraEtapa(3)"><span class="n">3</span><span class="ck"
 aria-hidden="true">&#10003;</span> Redigir e consolidar</button>
<span class="dica">teclas <b>1</b> <b>2</b> <b>3</b> trocam de etapa</span></div>

<div id="toasts" aria-live="polite"></div>

<div class="app">
<aside class="lateral">
<div class="card c-lista"><h2>Casos <span class="leg">o numero e o Health Score do ultimo relatorio</span></h2>
<input id="fbusca" placeholder="filtrar por nome, hipotese ou servidor"
 oninput="pintaCasos()" autocomplete="off" aria-label="Filtrar casos">
<div class="filtros" id="filtros"></div>
<div class="casos" id="casos">
<div class="sk"><i class="q"></i><i></i></div>
<div class="sk"><i class="q"></i><i></i></div>
<div class="sk"><i class="q"></i><i></i></div></div>
<div class="hint"><b>Investigando</b>: em analise. <b>Aguardando</b>: parado por
terceiro (app, storage). <b>Escalado</b>: passado adiante. <b>Resolvido</b>: com
causa raiz registrada. Selo <b>tracejado</b> = caso ainda sem relatorio gerado.</div>
<div class="btns"><button class="btn" onclick="carrega()">Atualizar</button></div>
</div></aside>

<main>
<section class="etapa on" id="etapa-1" aria-labelledby="et-1">
<div class="card c-nova"><h2>Nova coleta <span id="hro">(read-only)</span><span
 id="hesc" class="selo-esc" style="display:none">ESCREVE NO BANCO</span></h2>
<label for="fconn">Servidor / conexao</label>
<input id="fconn" list="lconn" placeholder="digite parte do nome, ex.: DBSRV600"
 oninput="sugereCaso()" autocomplete="off">
<datalist id="lconn"></datalist>
<div class="hint">Do repositorio <b>stringConexao/</b> - digite parte do nome e a
lista filtra.</div>
<label for="fcaso">Caso</label>
<input id="fcaso" placeholder="INC000987654 ou HC_DBSRV600" autocomplete="off">
<div class="hint">Pasta da evidencia: numero do incidente, ou <b>HC_&lt;servidor&gt;</b>
para health check avulso. Repetir o mesmo caso <b>acrescenta</b> ao historico
dele, nao sobrescreve.</div>
<label>Modo de coleta <span class="leg">o que entra na evidencia e o que custa em licenca</span></label>
<div class="modos">
<label class="modo"><input type="radio" name="modo" value="completo" checked
 onchange="modoEscolhido()">
<span><b>Completo <span class="tagl rec">recomendado</span><span class="tagl pack">usa pack</span></b>
<small>Tudo: views v$ + historico do AWR (DBA_HIST_*) + ADDM + regressao de SQL.
E o unico que enxerga o passado - compara hoje com a semana passada. Use quando
o diagnostico precisa de prova historica.</small></span></label>
<!-- Advisor: unica coisa no framework que NAO e read-only. Pertence ao Completo
     (parte da regressao apurada no AWR, que so ele coleta), mas fica FORA do
     <label> do radio: label dentro de label e invalido e faria o clique na
     caixa marcar o radio. O CSS o esconde quando o Completo nao esta escolhido. -->
<label class="flagadv" id="ladv" for="fadvisor"><input type="checkbox" id="fadvisor"
 onchange="marcaAdvisor(this)">
<span><b>Rodar tambem o SQL Tuning Advisor</b><span class="tagl esc">escreve no banco</span>
<small>Opcional, e pede confirmacao: cria e executa tuning tasks no dicionario e
consome <b>Tuning Pack</b>.</small></span></label></div>
<details class="maismodos" id="dmodos">
<summary id="smodos">Outros modos: triagem rapida, ou sem consumir pack</summary>
<div class="modos">
<label class="modo"><input type="radio" name="modo" value="rapido"
 onchange="modoEscolhido()">
<span><b>Rapido (triagem) <span class="tagl pack">usa pack</span></b>
<small>Pula o historico do AWR e o ADDM: retrato do <b>agora</b>, em poucos
segundos. Bom para a primeira olhada num incidente em curso.
<b>Atencao:</b> mais rapido nao quer dizer sem licenca - ainda usa Diagnostics
Pack conforme o banco licencia.</small></span></label>
<label class="modo"><input type="radio" name="modo" value="parcial"
 onchange="modoEscolhido()">
<span><b>Parcial <span class="tagl livre">sem pack</span></b>
<small>So views v$: <b>nao consome Diagnostics/Tuning Pack</b>. Use em banco sem
licenca ou quando nao se quer consumi-la. Perde AWR/ADDM/ASH historico - em
troca, o framework amostra <b>gv$session</b> para nao ficar sem perfil de
espera.</small></span></label></div></details>
<div class="hint"><b>Senha:</b> nao ha campo aqui de proposito - ela e pedida na
faixa <b>Execucao</b> (rodape, abre sozinha), vai direto para o processo e nunca
fica guardada.</div>
<div class="btns"><button class="btn pri" id="bcol" onclick="coletar()">Coletar</button></div>
<div class="msg" id="mcol"></div>

<!-- Confirmacao do advisor. Modal e nao confirm() do navegador porque o texto
     precisa caber inteiro na tela: sao quatro consequencias e um rollback, e
     confirm() nao formata nada. Cancelar e o padrao (autofocus). -->
<div class="mod modadv" id="madv" onclick="advFora(event)"
 role="dialog" aria-modal="true" aria-labelledby="madvt">
<div class="box" onclick="event.stopPropagation()">
<div class="top"><div style="flex:1;min-width:0">
<h3 id="madvt">Esta coleta vai ESCREVER no banco</h3>
<div class="advsub">O SQL Tuning Advisor roda em <b id="advsrv">-</b>, depois dos
checks de leitura. Os demais checks seguem read-only.</div></div></div>
<div class="corpo">
<ul class="advlist">
<li><b>Escreve no dicionario</b> - cria e executa tuning tasks
(<code>DBMS_SQLTUNE.CREATE_TUNING_TASK</code> e <code>EXECUTE_TUNING_TASK</code>).
E a unica parte do framework que nao e somente leitura.</li>
<li><b>Consome Tuning Pack</b>, licenca separada do Diagnostics. Se o banco nao
licenciar, a coleta continua e o advisor e ignorado - sem consumir nada.</li>
<li><b>Demora</b>: ate 5 min por SQL, no maximo 5 SQL elegiveis (troca de plano,
ou impacto a partir de 300s).</li>
<li><b>Pode deixar rastro</b>: havendo SQL Profile a aceitar, a tuning task fica
<b>mantida</b> no banco, senao a recomendacao nao teria como ser executada.</li>
</ul>
<div class="advrb"><b>Como desfazer:</b> o <code>acoes.sql</code> do caso ja sai com
o <code>DROP_TUNING_TASK</code> da task mantida. Tudo comentado - nada roda sozinho.</div>
<div class="btns">
<button class="btn" onclick="advNao()">Cancelar</button>
<button class="btn perigo" onclick="advSim()">Sim, rodar o advisor</button></div>
</div></div></div>

<!-- Cadastro do servidor que nao existe. Fica escondido ate a checagem falhar:
     e a resposta para o erro, nao mais um formulario na tela de coleta. -->
<div id="boxconn" class="novaconn" style="display:none">
<div class="nctit">Este servidor nao esta no repositorio</div>
<div class="ncq">Confirme que o nome esta certo. Se estiver, cadastre aqui -
 a coleta continua em seguida, sem sair do painel.</div>
<div id="ncpar"></div>
<div class="row"><div><label for="ncnome">Nome (como voce vai chamar)</label>
<input id="ncnome" autocomplete="off"></div>
<div><label for="nchost">Host ou IP</label>
<input id="nchost" placeholder="ex.: 10.20.30.40" autocomplete="off"></div></div>
<div class="row"><div><label for="ncport">Porta</label>
<input id="ncport" autocomplete="off"></div>
<div><label for="ncsvc">Service name</label>
<input id="ncsvc" placeholder="ex.: DBSRV543_SRV" autocomplete="off"></div>
<div><label for="ncuser">Usuario (leitura)</label>
<input id="ncuser" autocomplete="off"></div></div>
<div class="btns"><button class="btn pri" onclick="salvaConn()">Cadastrar e coletar</button>
<button class="btn" onclick="fechaConn()">Cancelar</button></div>
<div class="hint">Nao guardamos senha: ela e pedida na hora da coleta. A entrada
e gravada no formato do SQL Developer, entao pode ser reimportada la.</div>
<div class="msg" id="mconn"></div>
</div>

<div class="aviso"><b>--advisor</b> e a unica coisa aqui que ESCREVE no banco
(cria tuning tasks) e consome Tuning Pack. Por isso fica desmarcado, so aparece
no modo <b>Completo</b> e exige confirmacao. Todo o resto e leitura.</div>
<!-- Ponte para a etapa 2. So aparece quando ha evidencia analisada: oferecer
     "conduzir" antes disso mandaria o DBA para uma tela vazia. -->
<div class="prox" id="prox1" style="display:none">
<div class="t" id="prox1t"></div>
<button class="btn pri" onclick="mostraEtapa(2)">Conduzir o caso &#9656;</button>
</div></div>

</section>

<section class="etapa" id="etapa-2" aria-labelledby="et-2">
<div class="card c-caso"><h2 id="hcond">Conduzir o caso</h2>
<div id="cond" class="pedecaso">Selecione um caso na lista ao lado.<br>
Ainda nao ha nenhum? Comece pela etapa <b>1 Coletar</b>.</div>
<div id="condf" style="display:none">
<!-- Ordem = fluxo de trabalho, nao ordem de construcao da tela. O Guiado abre
     primeiro porque e o condutor: quem sabe o que fazer vai direto para
     Registrar, quem nao sabe seria justamente quem cairia num formulario em
     branco. -->
<div class="abas" role="tablist">
<button class="aba on" id="ab-guiado" onclick="mostraAba('guiado')"
 role="tab">Passo a passo <span class="b" id="ngu">0</span></button>
<button class="aba" id="ab-conduzir" onclick="mostraAba('conduzir')"
 role="tab">Registrar a mao</button>
<button class="aba" id="ab-linha" onclick="mostraAba('linha')"
 role="tab">Linha do tempo <span class="b" id="ntl">0</span></button>
<button class="aba" id="ab-historico" onclick="mostraAba('historico')"
 role="tab">Historico <span class="b" id="nhser">0</span></button>
<button class="aba" id="ab-playbooks" onclick="mostraAba('playbooks')"
 role="tab">Playbooks <span class="b" id="nkb">0</span></button>
<button class="aba" id="ab-drill" onclick="mostraAba('drill')"
 role="tab">Drill-down</button>
</div>

<div class="pane" id="pane-conduzir">
<label for="fhip">Hipotese atual (registrar nova descarta a anterior)</label>
<textarea id="fhip" rows="2" placeholder="ex.: IO do ASM saturado na janela das 9h"></textarea>
<label for="fmot">Motivo do descarte da anterior (opcional)</label>
<input id="fmot" placeholder="ex.: ASH nao mostra espera de IO">
<div class="btns"><button class="btn pri" onclick="caso('hipotese')">Registrar hipotese</button></div>
<label for="facao">Acao executada</label>
<textarea id="facao" rows="2" placeholder="ex.: coletei ASH da ultima hora"></textarea>
<div class="row"><div><label for="frisco">Risco de executar</label>
<select id="frisco"><option>nenhum</option><option>baixo</option><option>medio</option>
<option>alto</option></select></div>
<div><label for="fstatus">Status</label>
<select id="fstatus"><option value="">(nao alterar)</option><option>Investigando</option>
<option>Aguardando</option><option>Escalado</option></select></div></div>
<div class="hint"><b>nenhum</b>: so leitura. <b>baixo</b>: reversivel na hora.
<b>medio</b>: muda configuracao (pode exigir janela). <b>alto</b>: pode causar
indisponibilidade ou perda - exige aprovacao e plano de volta.</div>
<div class="btns"><button class="btn pri" onclick="caso('acao')">Registrar acao</button>
<button class="btn" onclick="caso('status')">Salvar status</button>
<button class="btn" onclick="escalar()">Escalar</button></div>
<label for="fcausa">Causa raiz (para resolver)</label>
<input id="fcausa" placeholder="ex.: plano regrediu apos coleta de estatisticas">
<label for="fsol">Solucao aplicada</label>
<input id="fsol" placeholder="ex.: fixado o plano anterior via SQL Plan Baseline">
<label for="fprev">Prevencao</label>
<input id="fprev" placeholder="ex.: travar estatisticas da tabela X">
<div class="btns"><button class="btn" onclick="caso('resolver')">Resolver caso</button>
<button class="btn" onclick="redija()">Redigir (template padrao)</button></div>
<div class="hint"><b>Resolver caso</b> abre o fechamento em primeiro plano, com
o texto pronto para copiar. <b>Redigir</b> so prepara a etapa <b>3</b>, para
quando a intencao e conferir antes.</div>
<div class="msg" id="mcaso"></div>
</div>

<!-- Guiado: o fluxo do DBA Master conduzido um passo por vez. O comando de um
     passo que MUDA o ambiente so aparece depois da Matriz de Risco - e por isso
     que a matriz e renderizada antes, e nao ao lado. -->
<div class="pane on" id="pane-guiado">
<div id="gu-pend"></div>
<div id="gu-vazio">
<div class="hint" style="margin:0 0 10px">O roteiro parte do que a evidencia
mostrou. Sem relatorio gerado, escolha o playbook a mao na Base de conhecimento.</div>
<div id="gu-ramo"></div>
<div id="gu-sug"><div class="vazio">sem sugestao</div></div>
</div>
<div id="gu-passo" style="display:none"></div>
<!-- A nota fica FORA do bloco repintado: o passo e redesenhado a cada acao, e
     o que o DBA acabou de digitar sumiria junto. -->
<div id="gu-nota" style="display:none">
<!-- A saida colada e o retorno do comando: e ela que faz a conducao reagir ao
     que esta acontecendo agora, em vez de seguir a foto da coleta inicial.

     A caixa fica RECOLHIDA quando o framework executa o passo e ABERTA quando
     nao executa. Ela continua indispensavel - 82 dos 265 passos do catalogo
     (31%) dependem dela: 55 de RISCO, que o framework se recusa a executar por
     desenho, 23 de ferramenta fora do SQL*Net (RMAN, adrci, lsnrctl, SO) e o
     caminho Citrix inteiro, onde nao ha conexao direta. Some-la quebraria um
     terco dos playbooks.
     O que ela nao pode e disputar atencao com o botao de executar quando a
     consulta roda aqui: duas formas de fazer a mesma coisa, lado a lado e com
     o mesmo peso, fazem a pessoa parar para escolher. -->
<div id="gu-colar-recolhido" style="display:none">
<button class="btn lnk" onclick="abreColar()">+ trazer saida de outro terminal
</button>
<span class="hint" style="display:inline">para RMAN, listener, SO - ou saida
copiada de outra sessao</span></div>
<div id="gu-colar">
<label for="fgsaida">Cole aqui a saida do comando (o retorno do terminal)
<span class="cnt" id="gsaicnt"></span></label>
<textarea id="fgsaida" rows="6" spellcheck="false"
 oninput="contaSaida()" onkeydown="atalhoSaida(event)"
 placeholder="cole a tabela inteira, com o cabecalho das colunas - ou o 'no rows selected'"></textarea>
<div class="btns">
<button class="btn pri" onclick="lerSaida()">Ler saida e atualizar hipoteses
<kbd>Ctrl+Enter</kbd></button>
<button class="btn" onclick="colaELe()">Colar e ler</button>
<button class="btn" onclick="limpaSaida()">Limpar</button></div>
<!-- O que o motor sabe ler. Nao e enfeite: o desperdicio do recurso nunca foi
     erro de leitura, era ninguem saber o que valia a pena colar. Recolhido para
     nao competir com a caixa, e gerado da fonte unica (saida.O_QUE_LEIO). -->
<!--LEITORES-->
<div class="hint">A saida vira <b>evidencia do caso</b>: o motor repontua as
hipoteses e diz o que mudou. <b>Consulta que volta vazia tambem conta</b> - e o
que descarta uma causa. Esta caixa serve para o que o framework <b>nao</b>
executa: RMAN, listener, comando de sistema operacional - e para saida trazida
de outro terminal. A verificacao em SQL roda no botao acima.</div>
<div id="gu-leitura"></div>
</div>

<label for="fgnota" style="margin-top:12px;display:block">O que voce viu / fez neste passo</label>
<input id="fgnota" placeholder="ex.: nenhuma sessao bloqueada na janela"
 autocomplete="off">
<div class="btns">
<button class="btn pri" onclick="guia('avancar')">Concluir e seguir</button>
<button class="btn" id="gbexec" onclick="guia('confirmar')">Executei</button>
<button class="btn" onclick="guia('pular')">Pular</button></div>
<div class="hint"><b>Executei</b> grava a acao no caso com o risco deste passo -
e o que aparece na linha do tempo, no relatorio e no consolidado do dia. Passo
que muda o ambiente tambem abre o checklist pos-acao.</div>
</div>
<div class="msg" id="mgu"></div>
</div>

<div class="pane" id="pane-playbooks">
<div class="hint" style="margin:0 0 10px">Sugeridos a partir dos achados deste
caso. Sem relatorio gerado, nao ha o que sugerir.</div>
<div id="kbsug"><div class="vazio">sem sugestao</div></div>
</div>

<div class="pane" id="pane-linha">
<div id="rei"></div>
<ul class="tl" id="tl"><li>sem eventos registrados</li></ul>
</div>

<div class="pane" id="pane-historico">
<div class="hint">Serie acumulada das COLETAS deste ambiente, nao de uma foto.
O AWR lembra de 8 a 15 dias e a previsao de crescimento do catalogo exige
Diagnostics Pack; a serie de espaco daqui sai do <code>space.txt</code>, que le
<code>dba_data_files</code> - sem pacote de licenca. Regere com
<code>HISTORICO.py --atualizar</code> depois de cada coleta.</div>
<div id="hamb" class="hint"></div>
<div class="hint">Em <b>espaco</b> o percentual e contra o TETO do autoextend, e
nao contra o que ja esta alocado - a mesma regra do analisador de espaco. Uma
tablespace com 65% do alocado e teto de 33 TB aparece perto de zero aqui, e
esta certo: o que a previsao mede e a parede de verdade, nao a proxima
extensao.</div>
<div id="hserie"><div class="vazio">sem historico</div></div>
<div class="sep"></div>
<label for="fmdia">Registrar o que aconteceu num dia</label>
<div class="hint">O analisador de carga diz QUANDO o patamar mudou. O que mudou
naquele dia so voce sabe - e sem registrar, a mesma investigacao e refeita do
zero no mes seguinte. O marco sobrevive a regeracao da serie.</div>
<div class="row"><div><label for="fmdia">Dia (AAAA-MM-DD)</label>
<input id="fmdia" placeholder="2026-09-07"></div>
<div style="flex:3"><label for="fmtexto">O que aconteceu</label>
<input id="fmtexto" placeholder="ex.: entrou o batch novo da folha"></div></div>
<div class="btns"><button class="btn pri" onclick="marcaHist()">Registrar marco</button></div>
<div id="mhist" class="msg"></div>
<div id="hmarcos"></div>
</div>

<div class="pane" id="pane-drill">
<label for="fdconn">Servidor <span id="dconn" class="hint"></span></label>
<input id="fdconn" list="lconn" placeholder="ex.: DBSRV600" autocomplete="off">
<div class="row"><div><label for="fsqlid">SQL_ID (13 caracteres)</label>
<input id="fsqlid" placeholder="9xy1abcd2ef3g" autocomplete="off"></div>
<div style="flex:0 0 auto;display:flex;align-items:flex-end">
<button class="btn" onclick="detalhar()">Detalhar SQL</button></div></div>
<div class="row"><div><label for="fini">Janela: inicio</label>
<input id="fini" placeholder="2026-07-30 09:00" autocomplete="off"></div>
<div><label for="ffim">Fim</label>
<input id="ffim" placeholder="2026-07-30 10:00" autocomplete="off"></div>
<div style="flex:0 0 auto;display:flex;align-items:flex-end">
<button class="btn" onclick="janela()">Analisar janela</button></div></div>
<div class="hint">O servidor vem do meta.json do caso quando existe. A janela
le o ASH historico (exige Diagnostics Pack).</div>
<div class="msg" id="mdrill"></div></div>

<!-- Ponte para a etapa 3. Aparece quando ja ha o que redigir (hipotese ou acao
     registrada): antes disso o template sairia so com <preencher>. -->
<div class="prox" id="prox2" style="display:none">
<div class="t" id="prox2t"></div>
<button class="btn pri" onclick="redija()">Redigir o chamado &#9656;</button>
</div></div></div>

<div class="card c-kb"><h2>Base de conhecimento <span class="leg">passo a passo, com o risco de cada comando</span></h2>
<label for="fkb">Buscar playbook</label>
<input id="fkb" placeholder="ex.: ORA-00257, lock, plano, swap, standby"
 oninput="buscaKb()" autocomplete="off">
<div class="hint">Busque por sintoma, codigo ORA- ou assunto. A KB nao executa
nada: ela mostra o comando para voce revisar e rodar onde for devido.</div>
<div class="pbs" id="pbs"><div class="vazio">carregando...</div></div>
<div class="hint" style="display:flex;gap:12px;flex-wrap:wrap;margin-top:9px">
<span><span class="tagr leitura">leitura</span> nao muda nada</span>
<span><span class="tagr mudanca">mudanca</span> altera config, reversivel</span>
<span><span class="tagr destrutivo">destrutivo</span> pode causar perda</span></div>
</div>
</section>

<section class="etapa" id="etapa-3" aria-labelledby="et-3">
<div class="card c-caso"><h2 id="hred">Redigir o chamado</h2>
<div id="redvazio" class="pedecaso">Selecione um caso na lista ao lado para
redigir o texto do chamado.</div>
<div id="redf" style="display:none">
<div class="btns"><button class="btn pri" onclick="redija()">Redigir (template padrao)</button>
<button class="btn" onclick="regeraRelatorio()">Regerar relatorio</button>
<button class="btn" onclick="copilotoBloco(0)">Perguntar ao Copilot</button></div>

<!-- Ponte para o Copilot. Caixa PROPRIA, e nao a do template: os dois textos no
     mesmo campo terminariam com um prompt de LLM colado dentro do chamado. -->
<div id="boxcopi" style="display:none">
<div class="sub2">Bloco para o Copilot</div>
<div class="hint" style="margin:0 0 8px">Junta persona, estado do caso e os
achados graves. <b>Nada e enviado por aqui</b> - o texto vai para a area de
transferencia e quem cola no Teams e voce. Nao existe integracao com o Copilot
do Teams: aquela sessao nao expoe API para uma aplicacao local.</div>
<textarea id="fcopi" rows="14" readonly spellcheck="false"
 style="font-family:ui-monospace,Consolas,monospace;font-size:12px"></textarea>
<div class="btns"><button class="btn pri" onclick="copiaCopi()">Copiar</button>
<button class="btn" onclick="copilotoBloco(1)">Refazer sem o nome do servidor</button>
<button class="btn" onclick="copilotoBloco(0)">Refazer com o servidor</button></div>
<div class="hint">Evidencia de producao carrega nome de servidor. Se o destino
nao precisa desse detalhe, use a versao sem o nome.</div>
</div>

<!-- Artefatos do caso. Antes so o dashboard tinha botao; PPTX, .md, JSON e a
     evidencia bruta so pelo Explorer - justo o PPTX, que e o que sobe quando o
     incidente escala. Cartao apagado = arquivo ainda nao gerado. -->
<div class="sub2">Abrir</div>
<div class="artes" id="artes"><div class="vazio">selecione um caso</div></div>
<div class="hint">O template le so o que foi <b>registrado no caso</b> - nao
depende de gerar relatorio. <b>Regerar relatorio</b> e outra coisa: reanalisa a
evidencia ja coletada e refaz dashboard, PPTX e .md (nao conecta no banco).</div>
<!-- Propostas de preenchimento. Cada caixinha e um FATO medido na evidencia
     (carga a mais, regressao, SQL pesado, bloqueio) com a solucao de contorno
     tirada do playbook que trata aquela secao. Marcar e ato do DBA: por isso o
     texto aplicado vai sem marca de "automatico" - e a decisao dele. -->
<div id="boxprop" style="display:none">
<div class="sub2">Propostas de preenchimento</div>
<div class="hint" style="margin:0 0 8px">Marque o que explica o caso. A causa
raiz e a <b>solucao de contorno</b> (do playbook, com o risco declarado) vao
para o caso e para o template. Nada e executado no banco.</div>
<div id="props"></div>
<div class="btns"><button class="btn pri" onclick="aplicaPropostas()">Aplicar
selecionadas</button></div>
</div>
<div id="boxred" style="display:none">
<label for="fredija">Template padrao - copie para o chamado</label>
<textarea id="fredija" rows="20" readonly spellcheck="false"
 style="font-family:ui-monospace,Consolas,monospace;font-size:12px"></textarea>
<div class="btns"><button class="btn pri" onclick="copiaRedija()">Copiar</button>
<button class="btn" onclick="abreFechamento()">Abrir em primeiro plano</button></div>
<div class="hint">E a transcricao do que esta registrado no caso. O que sair
como <b>&lt;preencher&gt;</b> ainda nao foi registrado - registre na etapa
<b>2</b>, nao so no chamado, senao o historico do caso fica menor que o texto
entregue.</div>
</div>
<div class="msg" id="mred"></div>
</div></div>

<div class="card c-lista"><h2>Consolidar o dia <span class="leg">o que aconteceu hoje, em uma planilha</span></h2>
<div class="hint">O consolidado junta todos os casos do dia: status, hipotese
atual, acoes, causa raiz e Health Score. E o material da passagem de plantao.</div>
<div class="btns"><button class="btn pri" onclick="fimDoDia()">Fim do dia (CSV)</button>
<button class="btn" onclick="window.open('/indice','_blank')">Indice de casos</button></div>
<div class="msg" id="mcons"></div>
</div>
</section>
</main></div>

<!-- Execucao: fora das etapas de proposito (ver CSS). Fechada e uma linha de
     estado; abre sozinha quando ha comando rodando ou pergunta esperando. -->
<section class="exec" id="exec" aria-label="Execucao">
<div class="barra" onclick="alternaExec()" title="Mostrar/ocultar a saida do comando">
<span class="dot" id="dot"></span>
<span class="qual" id="stxt">sem execucao</span>
<span class="abrir" id="execabrir">ver saida</span></div>
<div class="corpo">
<!-- Duas abas, e nao duas pilhas: quem abre a faixa com um comando rodando
     quer a saida dele, nao o que ja passou. O contador no rotulo evita que o
     historico precise ser aberto so para descobrir que esta vazio. -->
<div class="abas" role="tablist" aria-label="Execucao">
<button class="aba on" id="ea-saida" role="tab"
 onclick="mostraExecAba('saida')">Saida</button>
<button class="aba" id="ea-hist" role="tab"
 onclick="mostraExecAba('hist')">Historico <span class="b" id="nhist">0</span></button>
</div>

<div class="epane on" id="epane-saida">
<div id="log">Aguardando um comando...</div>
<div class="perg" id="perg" style="display:none">
<div class="pq" id="pqLabel"></div>
<div class="lin"><input id="fperg" autocomplete="off"
 onkeydown="if(event.key==='Enter')responde()">
<button class="btn pri" onclick="responde()">Responder</button>
<button class="btn" onclick="cancelaJob()">Cancelar</button></div>
<div class="hint">O comando esta esperando esta resposta. <b>Cancelar</b> encerra
a execucao - a coleta e read-only, entao parar no meio nao deixa nada pela metade
no banco.</div></div>
</div>

<div class="epane" id="epane-hist">
<div class="sub2">Nesta sessao</div>
<ul class="hist" id="hist"><li class="vazio">nada executado ainda</li></ul>
<!-- Sobrevive ao fechar o painel. Quem assume o plantao seguinte precisa saber
     o que ja rodou nesta maquina, e o painel e reaberto varias vezes durante o
     mesmo incidente. So metadados: a saida fica na pasta do caso. -->
<div class="sub2" style="display:flex;justify-content:space-between;
 align-items:center">Sessoes anteriores
<span class="abrir" onclick="limpaExec()">limpar</span></div>
<ul class="hist" id="execs"><li class="vazio">nenhuma execucao registrada</li></ul>
<details class="expl"><summary>o que fica guardado</summary>
<div class="hint">Guarda comando, caso, hora e resultado - nunca a saida do
banco. Fica em <code>output\painel_execucoes.jsonl</code>.</div></details>
</div>
</div></section>

<!--RODAPE-->

<!-- Playbook em primeiro plano. Fecha no X, no ESC ou clicando na pagina
     visivel atras (o clique no proprio quadro nao pode fechar, senao rolar o
     texto viraria uma armadilha). -->
<div class="mod" id="pbmod" onclick="fechaPb(event)" role="dialog"
 aria-modal="true" aria-labelledby="pbtit">
<div class="box" onclick="event.stopPropagation()">
<div class="top"><div style="flex:1;min-width:0">
<h3 id="pbtit"></h3><div class="sb" id="pbsub"></div>
<div class="rsum" id="pbrisco"></div></div>
<button class="xis" onclick="fechaPb()" title="Fechar (ESC)"
 aria-label="Fechar">&times;</button></div>
<div class="body" id="pbdet"></div>
</div></div>

<!-- Fechamento do chamado em primeiro plano, no mesmo formato do playbook.
     Aqui o modal serve para outra coisa: o texto vai ser colado em OUTRO
     programa, entao ele precisa aparecer inteiro, por cima de tudo, e sumir
     assim que for copiado - sem fazer o DBA achar de novo onde estava. -->
<div class="mod" id="redmod" onclick="fechaRed(event)" role="dialog"
 aria-modal="true" aria-labelledby="redtit">
<div class="box" onclick="event.stopPropagation()">
<div class="top"><div style="flex:1;min-width:0">
<h3 id="redtit">Fechamento do chamado</h3>
<div class="sb">Template Padrao (Diagnostico Support Platform + RCA) -
transcricao do que esta registrado no caso</div></div>
<button class="xis" onclick="fechaRed()" title="Fechar (ESC)"
 aria-label="Fechar">&times;</button></div>
<div class="body">
<div class="msg" id="mredm"></div>
<textarea id="fredmod" rows="22" spellcheck="false"
 style="width:100%;box-sizing:border-box;font-family:ui-monospace,Consolas,monospace;font-size:12px"></textarea>
<div class="btns"><button class="btn pri" onclick="copiaRedModal()">Copiar</button>
<button class="btn" onclick="fechaRed()">Fechar</button></div>
<div class="hint">Editar aqui muda so o que voce vai colar - o caso continua
como esta. O que sair como <b>&lt;preencher&gt;</b> ainda nao foi registrado:
registre na etapa <b>2</b>, senao o historico fica menor que o texto
entregue.</div>
</div></div></div>

<script>
var sel = null, est = {casos:[],conexoes:[]}, desde = 0, timer = null;
var jobAtual = null, pergAtual = null;
function $(i){return document.getElementById(i)}
function esc(s){var d=document.createElement('div');d.textContent=s==null?'':s;
return d.innerHTML}
function msg(id,txt,ok){var e=$(id);e.className='msg '+(ok?'ok':'err');e.textContent=txt;
if(ok)setTimeout(function(){e.className='msg'},6000);
toast(txt,ok)}

/* Toast: a confirmacao aparece mesmo quando o bloco de mensagem esta fora da
   area visivel - com a pagina rolada, o antigo aviso inline passava batido. */
function toast(txt,ok){
  var t=document.createElement('div');
  t.className='toast '+(ok?'ok':'err');
  t.textContent=txt;
  $('toasts').appendChild(t);
  setTimeout(function(){
    t.classList.add('sai');
    setTimeout(function(){t.remove()},220)}, ok?4200:7000)}

/* Indicadores do cabecalho: estado do trabalho sem precisar clicar em nada. */
function pintaKpis(){
  var cs=est.casos||[];
  var abertos=cs.filter(function(c){
    return c.status==='Investigando'||c.status==='Escalado'
        || c.status==='Aguardando'}).length;
  $('kCasos').textContent=cs.length;
  $('kAbertos').textContent=abertos;
  $('kConn').textContent=(est.conexoes||[]).length;
  if($('kKb').textContent==='-'){
    api('/api/kb').then(function(r){
      $('kKb').textContent=(r.playbooks||[]).length}).catch(function(){})}}

/* Token da sessao, injetado pelo servidor ao servir esta pagina. Vai em TODA
   chamada de API: sem ele a rota devolve 403. E o que impede que outra aba,
   de outro site, dispare uma coleta em producao com um fetch() para
   127.0.0.1 - o loopback protege contra a rede, nao contra o navegador. */
var TOKEN='__TOKEN__';

function api(rota,corpo){
  var o={method:corpo?'POST':'GET',headers:{'Content-Type':'application/json',
    'X-Painel-Token':TOKEN}};
  if(corpo)o.body=JSON.stringify(corpo);
  return fetch(rota,o).then(function(r){return r.json().then(function(j){
    if(!r.ok){
      /* O corpo do erro viaja junto. Alguns erros nao sao becos: o 409 de
         'servidor nao existe' traz as sugestoes de nome e o palpite de
         cadastro, e a tela usa isso para oferecer a saida em vez de so
         mostrar a frase. */
      var e=new Error(j.erro||('HTTP '+r.status));e.dados=j;e.http=r.status;
      throw e}
    return j})})}

var PILL={'Investigando':'p-inv','Escalado':'p-esc','Resolvido':'p-res',
'Aguardando':'p-agu'};

function carrega(){
  return api('/api/estado').then(function(e){
    est=e; pintaConns(); pintaCasos(); pintaJob(e.job); pintaHist(e.historico);
    pintaExec(e.execucoes); pintaKpis(); pintaEtapas();
    /* Retoma o acompanhamento de um job que NAO foi iniciado nesta aba: so
       `segue()` liga o polling que desenha a caixa de senha, e ate aqui ele so
       era chamado por quem clicou no botao. Quem recarregasse a pagina durante
       uma coleta (F5), abrisse o painel numa segunda aba ou disparasse pela
       API via um comando ficava com a faixa aberta dizendo "rodando" e sem
       campo nenhum - o CLI esperando senha para sempre. E o travamento de
       02/08 de novo, por outra porta. */
    if(e.job && e.job.rodando && !timer)segue();
  }).catch(function(x){msg('mcol','falha ao ler o estado: '+x.message,0)})}

/* O contador da aba soma as DUAS listas: com o historico recolhido, ele e a
   unica pista de que ha algo la dentro - sem isso a aba teria de ser aberta
   so para descobrir que esta vazia. */
var _nSessao=0, _nAntes=0;

function pintaContaHist(){
  var b=$('nhist'); if(!b)return;
  b.textContent=_nSessao+_nAntes}

function mostraExecAba(qual){
  ['saida','hist'].forEach(function(n){
    var p=$('epane-'+n), bt=$('ea-'+n);
    if(p)p.className='epane'+(n===qual?' on':'');
    if(bt){bt.className='aba'+(n===qual?' on':'');
           bt.setAttribute('aria-selected',n===qual?'true':'false')}})}

function pintaHist(h){
  _nSessao=(h||[]).length; pintaContaHist();
  if(!h || !h.length){
    $('hist').innerHTML='<li class="vazio">nada executado ainda</li>';return}
  $('hist').innerHTML=h.map(function(j){
    var ic=j.rodando?'&#9679;':(j.rc===0?'&#10003;':'&#10007;');
    var cor=j.rodando?'var(--acc)':(j.rc===0?'var(--green)':'var(--red)');
    /* `esc(j.id)` - o `pintaExec` logo abaixo ja escapava o mesmo campo na
       mesma construcao (`esc(r.id)`), e este ficou cru. Quando duas funcoes
       irmas tratam o mesmo dado de formas diferentes, a que escapa e a que
       revela a intencao; a outra e esquecimento. O id vem do servidor hoje,
       entao nao ha exploracao possivel - o que ha e uma diferenca que ninguem
       consegue justificar. */
    return '<li onclick="veLog(\''+esc(j.id)+'\')" title="ver o log desta execucao">'+
      '<span style="color:'+cor+'">'+ic+'</span>'+
      '<span class="q">'+esc(j.iniciado_em)+'</span>'+
      '<span class="c">'+esc(j.cmd)+'</span>'+
      '<span class="q">'+j.linhas+' l.</span></li>'}).join('')}

/* 'execs', nao 'exec': a faixa de execucao la em cima ja usa 'exec'. Com o id
   repetido, getElementById devolvia a SECTION e este innerHTML apagava a faixa
   inteira - dot, stxt, log e a caixa de senha junto. O sintoma era um
   "Cannot set properties of null" em pintaJob, tres funcoes adiante. */
function pintaExec(h){
  _nAntes=(h||[]).length; pintaContaHist();
  if(!h || !h.length){
    $('execs').innerHTML='<li class="vazio">nenhuma execucao registrada</li>';
    return}
  $('execs').innerHTML=h.map(function(r){
    var ic=r.rodando?'&#9679;':(r.rc===0?'&#10003;':(r.rc==null?'&#8210;':'&#10007;'));
    var cor=r.rodando?'var(--acc)':(r.rc===0?'var(--green)':
      (r.rc==null?'var(--mut)':'var(--red)'));
    /* Execucao de OUTRA sessao nao tem log em memoria: clicar so mostraria
       'sem saida'. Abrir o caso e o que o DBA realmente quer dali. */
    var clic=r.desta_sessao?' onclick="veLog(\''+esc(r.id)+'\')"':
      (r.caso?' onclick="sele(\''+esc(r.caso)+'\')"':'');
    var t=r.desta_sessao?'ver o log desta execucao':
      (r.caso?'abrir o caso '+r.caso:'execucao de uma sessao anterior');
    return '<li'+clic+' title="'+esc(t)+'">'+
      '<span style="color:'+cor+'">'+ic+'</span>'+
      '<span class="q">'+esc((r.em||'').slice(5,16))+'</span>'+
      '<span class="c">'+esc(r.cmd)+'</span>'+
      (r.caso?'<span class="q">'+esc(r.caso)+'</span>':'')+'</li>'}).join('')}

function limpaExec(){
  if(!confirm('Apagar o historico de execucoes?\n\nSo a lista some: casos, '+
    'evidencia e relatorios continuam onde estao.'))return;
  api('/api/execucoes/limpar',{}).then(function(r){
    msg('mcol',r.msg,1);carrega()
  }).catch(function(x){msg('mcol',x.message,0)})}

function veLog(id){
  api('/api/log?id='+encodeURIComponent(id)).then(function(l){
    $('log').textContent=(l.linhas||[]).join('\n')||'(sem saida)';
    $('log').scrollTop=0; pintaJob(l);
    /* O log vive na aba Saida. Sem trocar de aba, clicar no historico parecia
       nao fazer nada: a saida era escrita numa aba que nao esta na frente. */
    mostraExecAba('saida')})}

function pintaConns(){
  $('lconn').innerHTML=est.conexoes.map(function(c){
    return '<option value="'+esc(c.name)+'">'+esc(c.dsn)+' (user='+esc(c.user)+')</option>'
  }).join('')}

/* ── Tema ────────────────────────────────────────────────────
   Comeca pelo que o sistema operacional ja pede (prefers-color-scheme) e so
   depois obedece a escolha manual, que fica guardada no navegador. Incidente
   as 3h da manha nao pode comecar com uma tela branca na cara do DBA. */
function aplicaTema(t){
  document.documentElement.setAttribute('data-tema',t);
  var b=$('btema');
  if(b){b.innerHTML=t==='escuro'?'&#9790;':'&#9788;';
        b.title=t==='escuro'?'Mudar para tema claro':'Mudar para tema escuro'}}

function temaInicial(){
  var s=null;
  try{s=localStorage.getItem('tema')}catch(e){}
  if(s)return s;
  return (window.matchMedia&&
          window.matchMedia('(prefers-color-scheme: dark)').matches)
    ?'escuro':'claro'}

function trocaTema(){
  var t=document.documentElement.getAttribute('data-tema')==='escuro'
    ?'claro':'escuro';
  aplicaTema(t);
  try{localStorage.setItem('tema',t)}catch(e){}}

/* ── Etapas: uma de cada vez ─────────────────────────────────
   A barra existe para quem chega, mas quem trabalha quase nunca clica nela:
   a etapa ACOMPANHA o que foi feito (selecionar caso -> Conduzir, resolver ->
   Redigir). Sem isso, o modo foco so trocaria rolagem por clique. */
var ETAPAS=[1,2,3], etapa=1;

function mostraEtapa(n){
  etapa=n;
  ETAPAS.forEach(function(i){
    var sec=$('etapa-'+i), bt=$('et-'+i);
    if(sec)sec.className='etapa'+(i===n?' on':'');
    if(bt){bt.className='et'+(i===n?' on':'');
           bt.setAttribute('aria-selected',i===n?'true':'false')}});
  pintaEtapas();
  window.scrollTo({top:0,behavior:'smooth'})}

/* O que ja esta CUMPRIDO neste caso, e o que vem a seguir.

   Sem isto a barra so diz "onde estou" - e o DBA que termina uma coleta fica
   olhando para uma tela pronta sem nada apontando o proximo movimento. Marcar
   as etapas fecha o circuito: coleta -> conduzir -> redigir, cada uma sabendo
   quando a anterior acabou.

   O estado vem do caso SELECIONADO, nao de um contador da sessao: o DBA troca
   de caso o dia inteiro, e progresso de sessao mentiria no caso novo. */
function pintaEtapas(){
  var c=atual(), feitas={};
  if(c){
    feitas[1]=!!c.tem_relatorio;          // ha evidencia analisada
    feitas[2]=!!c.tem_ciclo_de_vida;      // ha hipotese ou acao registrada
    feitas[3]=c.status==='Resolvido'}
  ETAPAS.forEach(function(i){
    var bt=$('et-'+i); if(!bt)return;
    var cls='et'+(i===etapa?' on':'')+(feitas[i]?' feita':'');
    if(bt.className!==cls)bt.className=cls});
  pintaProximo(c,feitas)}

/* A ponte no fim de cada etapa. Aparece so quando ha para onde ir: um botao
   "conduzir" antes de existir evidencia levaria o DBA a uma tela vazia. */
function pintaProximo(c,feitas){
  var p1=$('prox1'), p2=$('prox2');
  if(p1){
    var mostra1=!!(c&&feitas[1]);
    p1.style.display=mostra1?'flex':'none';
    if(mostra1)$('prox1t').innerHTML='Evidencia de <b>'+esc(c.caso)+
      '</b> analisada'+(c.score!=null?' (Health Score '+c.score+'/100)':'')+
      '. Proximo: conduzir o caso passo a passo.'}
  if(p2){
    var mostra2=!!(c&&feitas[2]&&!feitas[3]);
    p2.style.display=mostra2?'flex':'none';
    if(mostra2)$('prox2t').innerHTML='Ha hipotese e acoes registradas em <b>'+
      esc(c.caso)+'</b>. Proximo: redigir o texto do chamado.'}}

/* ── Faixa de execucao ───────────────────────────────────────
   Fica fora das etapas: a coleta disparada na etapa 1 continua visivel
   enquanto se conduz outro caso na etapa 2 - e a caixa de senha junto. */
function alternaExec(){
  var e=$('exec');
  e.className=(e.className.indexOf('aberto')>=0)?'exec':'exec aberto';
  $('execabrir').textContent=(e.className.indexOf('aberto')>=0)
    ?'ocultar saida':'ver saida'}

/* `foco` traz a aba Saida junto. So quem COMECA algo pede foco: pintaJob
   chama esta funcao a cada volta do polling, e forcar a aba ali brigaria com
   o DBA que abriu o historico de proposito no meio de uma coleta longa. */
function abreExec(foco){
  var e=$('exec');
  if(e.className.indexOf('aberto')<0){e.className='exec aberto';
    $('execabrir').textContent='ocultar saida'}
  if(foco)mostraExecAba('saida')}

/* ── Abas do caso ───────────────────────────────────────────── */
/* Mesma ordem dos botoes: o fluxo do trabalho, nao a ordem em que as telas
   foram escritas. */
/* A ORDEM aqui e a ordem dos botoes no HTML, e o
   `test_a_ordem_do_js_e_a_ordem_dos_botoes` cobra as duas. Nao e preciosismo:
   acrescentei o botao de 'historico' e esqueci desta lista - o botao aparecia
   na tela e o clique nao abria nada, porque `mostraAba` percorre ESTA lista
   para saber quais paineis esconder. O teste reprovou antes de eu ver. */
var ABAS=['guiado','conduzir','linha','historico','playbooks','drill'];
function mostraAba(nome){
  ABAS.forEach(function(a){
    var pane=$('pane-'+a), bt=$('ab-'+a);
    if(pane)pane.className='pane'+(a===nome?' on':'');
    if(bt){bt.className='aba'+(a===nome?' on':'');
           bt.setAttribute('aria-selected',a===nome?'true':'false')}})}

/* ── Lista de casos ──────────────────────────────────────────
   O selo de score vem primeiro: com dezenas de casos, o que responde "qual
   esta pegando fogo" e o numero, nao o nome. Severidade e score sao os que o
   nucleo gravou no index.json - a tela nao recalcula nada. */
var filtro='todos';

function selo(c){
  if(c.score==null)return '<span class="sc s-na" title="sem relatorio gerado">--</span>';
  var m={crit:'s-crit',warn:'s-warn',ok:'s-ok',info:'s-info'};
  return '<span class="sc '+(m[c.severidade]||'s-info')+'" title="Health Score '+
    c.score+'/100 - severidade '+esc(c.severidade||'?')+'">'+c.score+
    '<small>score</small></span>'}

function passaFiltro(c){
  if(filtro==='abertos')return c.status==='Investigando'||c.status==='Escalado'
                            ||c.status==='Aguardando';
  if(filtro==='criticos')return c.severidade==='crit';
  if(filtro==='resolvidos')return c.status==='Resolvido';
  return true}

function pintaFiltros(){
  var cs=est.casos||[];
  var n=function(f){var g=filtro;filtro=f;
    var q=cs.filter(passaFiltro).length;filtro=g;return q};
  var defs=[['todos','Todos'],['abertos','Abertos'],
            ['criticos','Criticos'],['resolvidos','Resolvidos']];
  $('filtros').innerHTML=defs.map(function(d){
    return '<button class="chip'+(filtro===d[0]?' on':'')+
      '" onclick="setFiltro(\''+d[0]+'\')">'+d[1]+
      '<span class="qt">'+n(d[0])+'</span></button>'}).join('')}

function setFiltro(f){filtro=f;pintaCasos()}

function pintaCasos(){
  pintaFiltros();
  var q=($('fbusca')?$('fbusca').value:'').trim().toLowerCase();
  var lista=(est.casos||[]).filter(function(c){
    if(!passaFiltro(c))return false;
    if(!q)return true;
    return (c.caso+' '+(c.hipotese_atual||'')+' '+(c.conn||'')+' '+
            (c.hostname||'')).toLowerCase().indexOf(q)>=0});
  var h=lista.map(function(c){
    var p=PILL[c.status]||'p-none';
    var ach=(c.achados!=null)?'<span class="ach">'+c.achados+' achados</span>':'';
    return '<div class="caso'+(c.caso===sel?' sel':'')+'" onclick="sele(\''+
      esc(c.caso)+'\')">'+selo(c)+'<span class="nm">'+esc(c.caso)+
      '<div class="mt">'+(c.hipotese_atual?esc(c.hipotese_atual.slice(0,70)):
      'sem hipotese registrada')+'</div></span>'+ach+
      '<span class="pill '+p+'">'+esc(c.status)+'</span></div>'}).join('');
  $('casos').innerHTML=h||'<div class="vazio">'+
    ((est.casos||[]).length?'nenhum caso com esse filtro':'nenhum caso ainda')+
    '</div>';
  if(sel)pintaCond()}

/* Selecionar um caso E dizer "quero trabalhar neste": a tela vai junto para
   Conduzir, em vez de esperar mais um clique na barra de etapas. */
function sele(c){sel=c;$('fcaso').value=c;pintaCasos();pintaCond();
  /* Caso novo comeca pelo condutor. Sem isto, quem trocasse de caso herdaria a
     aba do caso anterior - Drill-down aberto num caso que nem foi analisado. */
  mostraAba('guiado');
  if(etapa===1)mostraEtapa(2)}

function atual(){return est.casos.filter(function(c){return c.caso===sel})[0]}

function pintaCond(){
  var c=atual(); if(!c){return}
  $('hcond').textContent='Conduzir: '+c.caso;
  pintaEtapas();
  // Template de OUTRO caso na tela seria o erro mais caro possivel aqui:
  // texto plausivel, caso errado, colado no chamado.
  if(REDCASO!==c.caso){REDCASO='';$('boxred').style.display='none';
    $('fredija').value='';
    // As propostas tambem sao do caso: deixa-las na tela ofereceria o
    // bloqueio de OUTRO servidor como causa raiz deste chamado.
    $('boxprop').style.display='none';$('props').innerHTML='';
    // E o bloco do Copilot idem: colar num chat a evidencia do caso errado
    // seria o vazamento mais bobo possivel.
    $('boxcopi').style.display='none';$('fcopi').value=''}
  $('cond').style.display='none'; $('condf').style.display='block';
  $('hred').textContent='Redigir o chamado: '+c.caso;
  $('redvazio').style.display='none'; $('redf').style.display='block';
  pintaArtes(c.caso);
  $('fhip').value=c.hipotese_atual||'';
  $('fstatus').value=(c.status in PILL)?c.status:'';
  $('fcausa').value=c.causa_raiz||'';
  api('/api/caso?caso='+encodeURIComponent(c.caso)).then(function(d){
    // Caso antigo pode nao ter meta.json: ai o campo fica em branco para o DBA
    // dizer de onde veio a evidencia, em vez de travar o drill-down.
    $('fdconn').value=d.conn||'';
    $('dconn').textContent=d.conn?'(do meta.json do caso)':'(caso sem meta.json: informe)';
    pintaTL(d.timeline); pintaSugestoes(d.kb); pintaRei(d.reincidencia);
    // Contador na aba: mostra que ha conteudo ali sem obrigar a abrir para ver.
    $('nkb').textContent=(d.kb||[]).length;
    $('ntl').textContent=(d.timeline||[]).length}).catch(function(){});
  carregaGuia(); carregaHistorico()}

/* ── Historico: a serie que atravessa as COLETAS ─────────────
   Pedido separado de proposito. O detalhe do caso le arquivos de UM caso; a
   serie le o historico.json da frota inteira e pode nao existir ainda (ele so
   nasce depois de `HISTORICO.py --atualizar`). Num pedido unico, historico
   ausente derrubaria o drill-down inteiro - timeline, playbooks e
   reincidencia junto. */
function carregaHistorico(){
  if(!sel)return;
  api('/api/historico?caso='+encodeURIComponent(sel))
    .then(pintaHistorico).catch(function(){});}

/* Reincidencia: achado que ja apareceu antes NESTE servidor. Fica junto da
   linha do tempo porque as duas respondem a mesma pergunta - o que ja
   aconteceu aqui antes. Achado cronico pede causa, nao mais um paliativo. */
/* Serie por metrica, com a TENDENCIA ao lado e a previsao SO onde a amostra
   autoriza. O r2 aparece junto da inclinacao de proposito: serie em degraus
   tem inclinacao positiva e r2 baixo, e extrapolar dali produz uma data com
   cara de precisao. Numero sem essa qualificacao e lido como promessa. */
var HAMB='';   /* ambiente da serie em tela - o marco precisa saber onde gravar */

function pintaHistorico(d){
  if(!d){return}
  HAMB=d.ambiente||'';
  $('nhser').textContent=(d.series||[]).length;
  $('hamb').textContent=d.sem_ambiente
    ? 'Este caso nao diz de qual ambiente veio (sem conn no meta.json), entao '
      +'nao ha serie para cruzar.'
    : 'Ambiente '+(d.ambiente||'?')+' - '+(d.series||[]).length+' serie(s).';
  pintaMarcos(d.marcos);
  var s=d.series||[], min=d.minimo||{pontos:4,dias:7};
  if(!s.length){
    $('hserie').innerHTML='<div class="vazio">sem historico deste ambiente. '
      +'Rode HISTORICO.py --atualizar depois de coletar.</div>';return}
  /* Quem cresce e chega perto do limite vem primeiro: e a unica pergunta da
     tela que tem prazo. Depois o resto, por familia e nome. */
  s=s.slice().sort(function(a,b){
    var pa=(a.dias_para==null)?1e9:a.dias_para, pb=(b.dias_para==null)?1e9:b.dias_para;
    if(pa!==pb)return pa-pb;
    return (a.familia+a.nome).localeCompare(b.familia+b.nome)});
  $('hserie').innerHTML='<table class="tb"><thead><tr><th>familia</th>'
    +'<th>metrica</th><th>atual</th><th>pontos</th><th>tendencia</th>'
    +'<th>chega a 90%</th></tr></thead><tbody>'
    +s.map(function(x){
      var tend, prev, cls='';
      if(x.por_dia==null){
        /* Amostra insuficiente NAO vira celula vazia: vazio se le como "nao
           cresce", e o que ha e "nao sei ainda". */
        tend='<span class="q">amostra curta ('+x.n+' de '+min.pontos+' pontos)</span>';
        prev='<span class="q">--</span>';
      }else{
        var sinal=x.por_dia>0?'+':'';
        tend=sinal+x.por_dia+'/dia <span class="q">r2 '+x.r2+' em '+x.dias+'d</span>';
        if(x.r2<0.5)tend+=' <span class="q">(dispersa: a reta explica pouco)</span>';
        if(x.dias_para==null){
          prev=(x.por_dia<=0)?'<span class="q">nao cresce</span>'
                             :'<span class="q">--</span>';
        }else{
          prev='<b>'+x.dias_para+' dias</b>';
          if(x.dias_para<=30)cls=' class="lin-crit"';
          else if(x.dias_para<=90)cls=' class="lin-warn"';
        }
      }
      return '<tr'+cls+'><td>'+esc(x.familia)+'</td><td>'+esc(x.nome)+'</td>'
        +'<td>'+(x.atual==null?'--':esc(x.atual))+'</td><td>'+x.n+'</td>'
        +'<td>'+tend+'</td><td>'+prev+'</td></tr>'}).join('')
    +'</tbody></table>';}

function pintaMarcos(ms){
  if(!ms || !ms.length){
    $('hmarcos').innerHTML='<div class="vazio">nenhum marco registrado</div>';
    return}
  $('hmarcos').innerHTML='<ul class="tl">'+ms.map(function(m){
    return '<li><span class="q">'+esc(m.dia)+'</span> '+esc(m.texto)+'</li>'
    }).join('')+'</ul>';}

function marcaHist(){
  var dia=$('fmdia').value.trim(), txt=$('fmtexto').value.trim();
  if(!dia||!txt){msg('mhist','informe o dia e o que aconteceu',0);return}
  api('/api/historico/marco',{ambiente:HAMB,dia:dia,texto:txt})
    .then(function(r){
      if(r && r.erro){msg('mhist',r.erro,0);return}
      $('fmtexto').value='';
      pintaMarcos(r&&r.marcos);
      msg('mhist','marco registrado em '+dia,1)});}

function pintaRei(rs){
  if(!rs || !rs.length){$('rei').innerHTML='';return}
  $('rei').innerHTML='<div class="reib"><b>Ja aconteceu antes'+
    (rs[0].servidor?' em '+esc(rs[0].servidor):'')+'</b>'+
    rs.map(function(r){
      return '<div class="rl"><span class="nx">'+r.ocorrencias+'x</span>'+
        '<span>'+esc(r.titulo)+'<span class="q">de '+esc(r.primeira)+' a '+
        esc(r.ultima)+' &middot; '+esc((r.casos||[]).join(', '))+
        '</span></span></div>'}).join('')+'</div>'}

/* Achado -> playbook: o salto de "esta ruim" para "faca isto". A sugestao vem
   dos achados que o relatorio JA produziu, e mostra o motivo - sugestao sem
   justificativa nao gera confianca. */
function pintaSugestoes(ps){
  if(!ps || !ps.length){
    $('nkb').textContent='0';
    $('kbsug').innerHTML='<div class="vazio">nenhum playbook casa com os '
      +'achados deste caso (ou o relatorio ainda nao foi gerado)</div>';return}
  $('kbsug').innerHTML='<div class="pbs" style="max-height:none">'+ps.map(function(p){
    var c=p.crit?'<span class="tagr destrutivo">'+p.crit+' crit</span>':'';
    return '<div class="pb" onclick="abrePb(\''+esc(p.id)+'\')">'+
      '<div class="t"><span>'+esc(p.titulo)+'</span>'+
      '<span class="n">'+c+' '+p.passos+' passos</span></div>'+
      '<div class="q">por: '+esc(p.motivo)+'</div></div>'}).join('')+'</div>'}

/* ── Guiado ──────────────────────────────────────────────────
   O fluxo do DBA Master conduzido pela tela: um passo por vez, comando de risco
   atras da Matriz, e a confirmacao gravando a acao no caso sozinha. */
var guiaEst=null;   /* ultimo payload do guiado (parametros do passo atual) */

function carregaGuia(){
  if(!sel)return;
  api('/api/guia?caso='+encodeURIComponent(sel)).then(pintaGuia)
    .catch(function(){})}

/* O fechamento da conducao: o que ficou de pe, o que CAIU e para onde ir.
   A refutacao entra nomeada de proposito - e o ganho menos obvio de conduzir
   um roteiro e o que impede alguem de reabrir o mesmo caminho amanha. */
function conclusaoGuia(d,pr){
  var hs=d.hipoteses||[];
  function porVer(v){return hs.filter(function(h){return h.veredito===v})}
  var dePe=porVer('sustentada'), pos=porVer('possivel'), fora=porVer('refutada');
  var h='<div class="gfim"><h4>Roteiro concluido</h4>';
  if(pr.pulados)h+='<div class="li av"><b>Atencao:</b> '+pr.pulados+
    ' passo(s) foram PULADOS - a conclusao abaixo nao cobre o que eles veriam.</div>';
  h+='<div class="li"><b>O que a conducao estabeleceu</b></div>';
  dePe.forEach(function(x){
    h+='<div class="li ok"><b>sustentada:</b> '+esc(x.rotulo)+
      ((x.a_favor||[]).length?' <span class="pq">&larr; '+
        esc(x.a_favor.slice(0,3).map(function(a){return a.marca}).join(', '))+
        '</span>':'')+'</div>'});
  pos.forEach(function(x){
    h+='<div class="li"><b>possivel:</b> '+esc(x.rotulo)+'</div>'});
  /* Escapar CADA rotulo e so entao juntar. Juntar primeiro passaria o
     separador `&middot;` pelo esc() junto com o texto, e o `&` sairia
     escapado de novo: a tela mostrava "&middot;" literal no lugar do ponto.
     So apareceu ao rodar a funcao de verdade - a assercao sobre a string do
     arquivo passava. */
  if(fora.length)h+='<div class="li de"><b>descartado:</b> '+
    fora.slice(0,5).map(function(x){return esc(x.rotulo)}).join(' &middot; ')+
    '</div>';
  if(!dePe.length&&!pos.length)h+='<div class="li">Nenhuma hipotese ficou de pe '+
    'com o que foi observado. Isso NAO e resultado vazio: os caminhos '+
    'percorridos estao descartados e o proximo roteiro comeca de outro lugar.</div>';
  h+='<div class="btns">'+
     (dePe.length?'<button class="btn" onclick="mostraEtapa(2);verHip()">'+
        'Aprofundar a causa</button>':
        '<button class="btn" onclick="verSugestoes()">Outro roteiro</button>')+
     '<button class="btn pri" onclick="redija()">Redigir o chamado '+
     '&#9656;</button></div></div>';
  return h}

/* `gu-ramo` e onde `pintaHipoteses` desenha o raciocinio. Errei o id na
   primeira versao (`hip`, que nao existe) e o guarda de ids da suite pegou:
   o botao daria erro de JS silencioso e nao faria nada - o pior desfecho para
   um botao que promete "aprofundar". */
function verHip(){var e=$('gu-ramo');if(e)e.scrollIntoView({behavior:'smooth'})}
function verSugestoes(){guia('encerrar')}

function pintaGuia(d){
  if(!d || d.caso!==sel)return;   /* resposta atrasada de OUTRO caso */
  /* Guardado porque `executaPasso` precisa saber QUAIS parametros o passo
     pede, e ele e chamado pelo clique, nao pelo repintar. Ler do DOM daria a
     mesma resposta ate o dia em que um passo nao tiver campo nenhum. */
  guiaEst=d;
  var pr=d.progresso||{}, p=d.passo||null, pend=d.pendencias||[];
  $('ngu').textContent=pend.length?('!'+pend.length):(pr.total?pr.pct+'%':'0');
  pintaPend(pend);
  if(!pr.playbook){$('gu-vazio').style.display='block';
    $('gu-passo').style.display='none';$('gu-nota').style.display='none';
    pintaSugGuia(d);return}
  $('gu-vazio').style.display='none';
  $('gu-passo').style.display='block';
  var h='<div class="gprog"><span>'+esc(pr.titulo||'')+'</span>'+
    '<span class="gbar"><i style="width:'+(pr.pct||0)+'%"></i></span>'+
    '<span>'+(pr.concluidos||0)+'/'+(pr.total||0)+
    ((pr.pulados)?' ('+pr.pulados+' pulado)':'')+'</span>'+
    '<button class="btn" onclick="guia(\'encerrar\')">encerrar</button></div>';
  /* `p.indice===undefined` e o cinto: o servidor ja manda `null` quando nao ha
     passo, mas `{}` e TRUTHY em JavaScript (e falsy em Python) - foi assim que
     um roteiro 8/8 concluido desenhou "NaN/undefined" e a caixa de colar em
     vez da conclusao. Testar pelo VALOR, e nao pela verdade, e o que faz a
     tela sobreviver a um payload antigo em cache. */
  if(!p || p.indice===undefined){
    /* Fim do roteiro e o momento de mais atrito do fluxo: o DBA acabou de
       fazer o trabalho e a tela precisa dizer para onde ir, nao so avisar que
       terminou.
       Dizer "concluido, va redigir" deixa no colo de quem conduziu justamente
       o trabalho que o framework existe para fazer: afirmar o que a evidencia
       sustenta. O payload ja traz `hipoteses` - faltava usar. */
    h+=conclusaoGuia(d,pr);
    $('gu-passo').innerHTML=h;$('gu-nota').style.display='none';return}
  var r=p.risco||'leitura';
  h+='<div class="passo r-'+esc(r)+'">'+
    '<h4><span><span class="num">'+(p.indice+1)+'/'+p.total+'</span>'+
    esc(p.titulo||'')+'</span><span style="display:flex;gap:5px">'+
    (p.onde?'<span class="tagr onde">'+esc(p.onde)+'</span>':'')+
    '<span class="tagr '+esc(r)+'">'+esc(r)+'</span></span></h4>';
  if(p.objetivo)h+='<div class="obj">'+esc(p.objetivo)+'</div>';
  /* A matriz vem ANTES do comando: quem le de cima para baixo topa com o risco
     antes de topar com algo para copiar. */
  var m=p.matriz_risco||{};
  if(m.risco){
    h+='<div class="gmat"><h5>Matriz de risco &middot; acao de '+esc(m.risco)+'</h5>'+
      '<div class="li"><b>Impacto:</b> '+esc(m.impacto||'')+'</div>'+
      (m.antes_de_executar?'<div class="li"><b>Validar antes:</b> '+
        esc(m.antes_de_executar)+'</div>':'')+
      '<div class="li"><b>Rollback:</b> '+esc(m.rollback||'')+'</div>'+
      (m.exige_aprovacao?'<div class="apr">Exige aprovacao formal e janela '+
        'combinada antes de executar.</div>':'')+'</div>'}
  if(p.comando)h+='<div class="pre-wrap"><pre>'+esc(p.comando)+'</pre>'+
    '<button class="cp" onclick="copiaCmd(this)">copiar</button></div>';
  h+=blocoExec(d,p);
  if(p.olhar)h+='<div class="bloco olhar"><b>O que olhar</b>'+esc(p.olhar)+'</div>';
  if(p.armadilha)h+='<div class="bloco arma"><b>Armadilha</b>'+
    esc(p.armadilha)+'</div>';
  $('gu-passo').innerHTML=h+'</div>';
  $('gu-nota').style.display='block';
  /* A caixa de colar segue o PASSO: recolhida quando a consulta roda aqui,
     aberta quando nao roda - ali ela e o unico caminho, e escondida atras de
     um link viraria um beco. 82 dos 265 passos dependem dela (55 de risco, 23
     de ferramenta fora do SQL*Net). */
  colar((d.execucao||{}).pode?0:1);
  /* O rotulo do botao carrega o risco: 'Executei' e igual para um SELECT e para
     um KILL, e o dedo vai no mesmo lugar. */
  $('gbexec').textContent=(r==='leitura')
    ?'Executei (registrar acao)'
    :'Executei (registrar acao de risco '+r+')'}

/* aberta=1 mostra a caixa; 0 recolhe atras do link. */
function colar(aberta){
  var c=$('gu-colar'), r=$('gu-colar-recolhido');
  if(!c||!r)return;
  c.style.display=aberta?'block':'none';
  r.style.display=aberta?'none':'block'}

function abreColar(){colar(1);var e=$('fgsaida');if(e)e.focus()}

/* ── Executar a verificacao no proprio framework ──────────────
   O passo de LEITURA roda aqui, pela mesma trava read-only da coleta: a saida
   vira evidencia do caso e o motor repontua as hipoteses. O que muda o
   ambiente nao ganha botao em nenhuma hipotese - a tela mostra o motivo, que e
   sempre mais util que um botao desabilitado sem explicacao. */
function blocoExec(d,p){
  var e=d.execucao||{}, lote=d.lote||[];
  var h='<div class="gexec">';
  if(!e.pode){
    h+='<div class="li"><b>Nao roda aqui:</b> '+esc(e.motivo||'')+'</div>';
    return h+'</div>'}
  var n=(e.statements||[]).length;
  h+='<div class="li"><b>Verificacao read-only:</b> '+n+
     (n>1?' consultas':' consulta')+
     ' &middot; SET TRANSACTION READ ONLY &middot; MODULE=TROUBLESHOOT-RO</div>';
  /* Parametro: o valor sugerido vem dos achados do proprio caso. SID e numero
     de instancia nao sao sugeridos de proposito - a sessao da coleta
     provavelmente nao existe mais, e um numero errado PARECE certo. */
  (e.parametros||[]).forEach(function(nome){
    var v=(e.sugestoes||{})[nome]||'';
    h+='<div class="gp"><label for="gpar-'+esc(nome)+'">&amp;'+esc(nome)+'</label>'+
       '<input id="gpar-'+esc(nome)+'" value="'+esc(v)+'" autocomplete="off"'+
       ' placeholder="'+(v?'sugerido pela evidencia do caso':'preencha com o valor de agora')+'"></div>'});
  /* Ja executado NESTA conducao? Muda qual e a acao principal. Sem isso, quem
     acabou de executar via o MESMO botao em destaque - a tela mandava repetir
     o que acabou de ser feito em vez de seguir. Conta so `origem: executado`:
     saida colada a mao nao prova o que rodou. */
  var feitas=(d.saidas||[]).filter(function(s){
    return s.origem==='executado' && s.passo_executado===p.indice}).length;
  h+='<div class="btns">';
  if(feitas){
    h+='<button class="btn pri" onclick="guia(\'avancar\')">Concluir e '+
       'seguir &#9656;</button>'+
       '<button class="btn" onclick="executaPasso(0)">Executar de novo</button>';
  }else{
    h+='<button class="btn pri" onclick="executaPasso(0)">'+
       'Executar aqui (read-only)</button>';
  }
  if(lote.length>1)h+='<button class="btn" onclick="executaPasso(1)">'+
     'Executar os '+lote.length+' passos de verificacao</button>';
  h+='</div>';
  if(feitas)h+='<div class="gfeito">&#10003; '+feitas+' leitura(s) ja '+
    'gravada(s) neste passo - o resultado esta em <b>Saidas observadas</b> '+
    'e as hipoteses ja foram repontuadas.</div>';
  return h+'</div>'}

function executaPasso(tudo){
  var e=guiaEst?guiaEst.execucao:null;
  var b={caso:sel,tudo:tudo?1:0,params:{}};
  if(!tudo){
    var faltou=[];
    ((e&&e.parametros)||[]).forEach(function(nome){
      var i=$('gpar-'+nome);
      var v=i?i.value.trim():'';
      if(!v)faltou.push(nome); else b.params[nome]=v});
    if(faltou.length){
      msg('mgu','preencha: &'+faltou.join(', &'),0);return}}
  api('/api/executar',b).then(function(j){
    msg('mgu','executando: '+j.cmd,1);
    /* A faixa de execucao TEM de abrir: e onde a caixa de senha aparece. Sem
       isso o processo espera uma senha que ninguem ve - o travamento de 02/08
       por outra porta. */
    desde=0; abreExec(); segue()
  }).catch(function(x){msg('mgu',x.message,0)})}

/* ── Raciocinio: as hipoteses, e o que a evidencia faz com cada uma ──
   Mostrar so a conclusao ("a evidencia aponta para I/O") pede fe. O DBA que
   vai executar precisa ver o que sustenta, o que ja foi descartado e por que -
   inclusive para discordar, que e o uso mais legitimo desta tela. */
var VERD={sustentada:'sustentada',possivel:'possivel',
          consequencia:'sintoma, nao causa',refutada:'descartada',
          sem_evidencia:'sem evidencia'};
/* Selo de classe: o caso aberto por lentidao nao pode terminar sem alguem ver
   que o banco esta em NOARCHIVELOG. */
var CLASSE={parada:'pode PARAR o banco',perda:'risco de PERDA de dado'};

function pintaHipoteses(d){
  var hs=(d.hipoteses||[]).filter(function(h){
    /* 'sem evidencia' e a maioria do catalogo em qualquer caso: listar todas
       viraria parede de texto e esconderia justamente as que importam. */
    return h.veredito!=='sem_evidencia'});
  if(!hs.length){$('gu-ramo').innerHTML=
    '<div class="hint">A evidencia nao aponta um gargalo dentro do banco. '+
    'Isso tambem e resposta: verifique rede, cliente e aplicacao (SQL*Net) - '+
    'ou gere o relatorio antes de conduzir.</div>';return}
  var h='';
  if(d.narrativa)h+='<div class="narr">'+esc(d.narrativa)+'</div>';
  h+='<div class="hips">'+hs.map(function(x){
    var b='<div class="hip '+esc(x.veredito)+'">'+
      '<div class="cab"><span class="rot">'+esc(x.rotulo)+
      (CLASSE[x.classe]?'<span class="cl '+esc(x.classe)+'">'+
        esc(CLASSE[x.classe])+'</span>':'')+'</span>'+
      '<span class="ver">'+esc(VERD[x.veredito]||x.veredito)+'</span></div>'+
      '<div class="ex">'+esc(x.explica||'')+'</div>';
    /* Dizer de QUEM e consequencia e o que evita o chamado para a infra por um
       I/O que o proprio plano produziu. */
    if(x.veredito==='consequencia'&&x.causa)
      b+='<div class="li con"><span class="m">&rarr;</span><span>Explicado '+
         'por <b>'+esc(x.causa)+'</b>: tratar a causa, nao este sintoma.'+
         '</span></div>';
    (x.a_favor||[]).slice(0,3).forEach(function(p){
      b+='<div class="li pro"><span class="m">+</span><span>'+
         esc(p.achado)+'</span></div>'});
    (x.contra||[]).slice(0,2).forEach(function(p){
      b+='<div class="li con"><span class="m">&minus;</span><span>'+
         esc(p.achado)+' <span class="q">('+esc(p.marca)+')</span></span></div>'});
    /* O que DECIDE so aparece em hipotese viva: numa descartada seria mandar
       coletar evidencia para uma pergunta ja respondida. */
    if(x.veredito!=='refutada' && x.decide)
      b+='<div class="dec"><b>O que decide:</b> '+esc(x.decide)+
         (x.coletar?' <span class="q">('+esc(x.coletar)+')</span>':'')+'</div>';
    /* ADOTAR em um clique. Ate 09/2026 esta tela MOSTRAVA a hipotese com a
       evidencia a favor e contra, e para registra-la o DBA tinha de mudar de
       aba e REDIGITAR num campo livre o que estava na tela. Ninguem redigita
       o que ja esta escrito: medido, 0 de 30 casos tinham raciocinio
       registrado, e 1 de 27 tinha causa.

       O custo de registrar era escrever um paragrafo; passa a ser um clique.
       Nao aparece em hipotese refutada - adotar o que a evidencia derrubou
       nao e um caminho que a tela deva oferecer. */
    if(x.veredito!=='refutada')
      b+='<div class="btns"><button class="btn" onclick="adotaHip(\''+
         esc(x.id)+'\')">Foi esta &mdash; registrar como hipotese</button>'+
         '</div>';
    return b+'</div>'}).join('')+'</div>';
  $('gu-ramo').innerHTML=h;
  HIPS={}; hs.forEach(function(x){HIPS[x.id]=x.rotulo});}

/* Rotulo de cada hipotese em tela, por id: o clique manda o TEXTO (que e o
   que o DBA le no chamado depois) e a ORIGEM (que e o rastro de auditoria).
   Guardar o mapa evita repetir o rotulo dentro do onclick, onde aspas e
   acento do texto teriam de ser escapados duas vezes. */
var HIPS={};

function adotaHip(id){
  var rot=HIPS[id];
  if(!rot){msg('mcaso','hipotese nao esta mais em tela - recarregue',0);return}
  api('/api/caso',{acao:'hipotese',caso:sel,texto:rot,
                   /* "motor:<id>" e o que distingue, semanas depois, o DBA
                      confirmando o framework do DBA concluindo sozinho. */
                   origem:'motor:'+id,
                   motivo:'substituida ao adotar a hipotese do motor'})
    .then(function(r){
      $('fhip').value=rot;
      msg('mcaso',r.msg,1);
      carrega()})
    .catch(function(x){msg('mcaso',x.message,0)})}

function pintaSugGuia(d){
  var sug=d.sugestoes||[];
  pintaHipoteses(d);
  $('gu-sug').innerHTML=sug.length
    ?'<div class="pbs" style="max-height:none">'+sug.slice(0,6).map(function(s){
        return '<div class="pb" onclick="guiaIniciar(\''+esc(s.id)+'\')">'+
          '<div class="t"><span>'+esc(s.titulo)+'</span>'+
          '<span class="n">iniciar</span></div>'+
          '<div class="q">por: '+esc(s.porque)+'</div></div>'}).join('')+'</div>'
    :'<div class="vazio">nenhum playbook sugerido para este caso</div>'}

function pintaPend(pend){
  if(!pend||!pend.length){$('gu-pend').innerHTML='';return}
  $('gu-pend').innerHTML='<div class="gpend"><h5>Checklist pos-acao &middot; '+
    pend.length+' em aberto</h5>'+pend.map(function(p){
      return '<div class="it"><button class="btn" onclick="guiaPend(\''+
        esc(p.id)+'\')">feito</button><span>'+esc(p.texto)+
        '<span class="q">'+esc(p.porque||'')+'</span></span></div>'}).join('')+
    '</div>'}

function guiaIniciar(id){
  api('/api/guia',{acao:'iniciar',caso:sel,playbook:id}).then(function(d){
    pintaGuia(d);msg('mgu',d.msg,1)}).catch(function(e){msg('mgu',e.message,0)})}

function guia(acao){
  var n=$('fgnota');
  api('/api/guia',{acao:acao,caso:sel,texto:n?n.value:''}).then(function(d){
    // A nota e do passo que acabou de sair. Mante-la colaria a observacao de um
    // passo no registro do seguinte.
    if(n)n.value='';
    pintaGuia(d);msg('mgu',d.msg,1);
    /* Acao registrada muda a linha do tempo e o status: recarregar o caso
       impede que as duas abas contem historias diferentes. */
    if(acao==='confirmar')pintaCond()
  }).catch(function(e){msg('mgu',e.message,0)})}

function guiaPend(id){
  api('/api/guia',{acao:'pendencia',caso:sel,id:id}).then(function(d){
    pintaGuia(d);msg('mgu',d.msg,1)}).catch(function(e){msg('mgu',e.message,0)})}

/* ── Saida colada: o retorno do comando vira evidencia ────────
   Aqui o guiado deixa de ser roteiro fixo. O DBA cola o que o terminal
   devolveu, o motor repontua as hipoteses e a tela mostra O QUE MUDOU - ver a
   mesma lista de novo nao ajudaria; saber que a hipotese de bloqueio caiu e
   que e o proximo passo do raciocinio. */
/* Contador, atalho e colar-e-ler. No plantao a mao ja esta no teclado com o
   terminal do lado: obrigar a achar o botao com o mouse a cada passo era o
   atrito mais bobo do fluxo. */
function contaSaida(){
  var t=$('fgsaida'), c=$('gsaicnt');
  if(!t||!c)return;
  var v=t.value;
  if(!v.trim()){c.textContent='';return}
  var n=v.split('\n').filter(function(l){return l.trim()}).length;
  c.textContent=n+(n===1?' linha':' linhas')+' colada(s)'}

function atalhoSaida(e){
  /* Enter puro segue quebrando linha: a saida colada e multilinha por natureza,
     e enviar no Enter cortaria a tabela no meio. */
  if((e.ctrlKey||e.metaKey) && e.key==='Enter'){e.preventDefault();lerSaida()}}

function limpaSaida(){$('fgsaida').value='';contaSaida()}

/* Le a area de transferencia e interpreta em um clique. Sem permissao (ou sem
   HTTPS/gesto valido), avisa e deixa a caixa em foco - nunca finge que colou. */
function colaELe(){
  if(!navigator.clipboard||!navigator.clipboard.readText){
    $('fgsaida').focus();
    msg('mgu','este navegador nao deixa ler a area de transferencia: '+
        'cole com Ctrl+V e use Ctrl+Enter',0);return}
  navigator.clipboard.readText().then(function(txt){
    if(!txt||!txt.trim()){msg('mgu','a area de transferencia esta vazia',0);return}
    $('fgsaida').value=txt;contaSaida();lerSaida()
  }).catch(function(){
    $('fgsaida').focus();
    msg('mgu','o navegador negou a leitura da area de transferencia: '+
        'cole com Ctrl+V e use Ctrl+Enter',0)})}

function lerSaida(){
  var t=$('fgsaida');
  if(!t || !t.value.trim()){msg('mgu','cole a saida do comando primeiro',0);return}
  api('/api/guia',{acao:'saida',caso:sel,saida:t.value}).then(function(d){
    pintaGuia(d);pintaLeitura(d.leitura);msg('mgu',d.msg,1);
    /* O texto colado SAI da caixa: deixa-lo levaria a colar duas vezes a mesma
       evidencia no passo seguinte, inflando a pontuacao sem observacao nova. */
    t.value='';contaSaida()
  }).catch(function(e){msg('mgu',e.message,0)})}

function pintaLeitura(l){
  var d=$('gu-leitura');
  if(!d)return;
  if(!l){d.innerHTML='';return}
  if(!l.achados||!l.achados.length){
    d.innerHTML='<div class="hint lei-nada">'+esc(l.aviso||
      'nada reconhecido nesta saida')+'</div>';return}
  var h='<div class="lei"><h5>O que eu entendi desta saida</h5>';
  /* Cada achado diz de onde saiu: sem o "porque", a leitura vira afirmacao sem
     lastro e o DBA nao tem como discordar. */
  h+=l.achados.map(function(a){
    return '<div class="lei-a '+esc(a.severity||'')+'"><span>'+
      esc(a.title||'')+'</span><span class="q">'+esc(a.porque||'')+
      '</span></div>'}).join('');
  if(l.mudou&&l.mudou.length)
    h+='<div class="lei-m"><b>Mudou no raciocinio:</b>'+l.mudou.map(function(m){
      return '<div>'+esc(m.texto)+'</div>'}).join('')+'</div>';
  else h+='<div class="lei-m q">nenhuma hipotese mudou de veredito</div>';
  d.innerHTML=h+'</div>'}

var TAG={criado:'inicio',hipotese_inicial:'hipotese',hipotese_atual:'hipotese',
hipotese_descartada:'descartada',acao:'acao',escalado:'escalado',resolvido:'resolvido',
/* verificacao do passo a passo: rotulo proprio para nao confundir o que foi
   OBSERVADO com o que foi FEITO no banco */
observado:'verificado'};

function pintaTL(ev){
  if(!ev || !ev.length){
    $('tl').innerHTML='<li>sem eventos registrados</li>';return}
  $('tl').innerHTML=ev.map(function(e){
    /* `esc` no `tipo` mesmo ele sendo vocabulario FECHADO hoje (acao, criado,
       observado, resolvido, escalado e as tres de hipotese, todas emitidas
       pelo caso.py). O valor entra num atributo `class` por concatenacao, e
       atributo montado a mao e o lugar onde escape esquecido nao aparece em
       teste nenhum: a tela continua igual ate o dia em que alguem deixar o
       tipo ser texto livre. Custa uma chamada. */
    var cls='t-'+esc(e.tipo)+(e.risco==='alto'?' r-alto':'');
    var r=e.risco&&e.risco!=='nenhum'?'<span class="r'+(e.risco==='alto'?' alto':'')+
      '">risco '+esc(e.risco)+'</span>':'';
    var det=e.motivo?'<span class="det">motivo: '+esc(e.motivo)+'</span>':
      (e.solucao?'<span class="det">solucao: '+esc(e.solucao)+'</span>':'');
    return '<li class="'+cls+'"><span class="q">'+esc(e.em||'sem hora')+'</span>'+
      '<span class="tag">'+esc(TAG[e.tipo]||e.tipo)+'</span>'+esc(e.texto)+r+det+'</li>'
  }).join('')}

function detalhar(){
  api('/api/detalhar',{caso:sel,sql_id:$('fsqlid').value,conn:$('fdconn').value})
    .then(function(j){msg('mdrill','rodando: '+j.cmd,1);desde=0;abreExec();segue()})
    .catch(function(x){msg('mdrill',x.message,0)})}

function janela(){
  api('/api/janela',{caso:sel,inicio:$('fini').value,fim:$('ffim').value,
                     conn:$('fdconn').value})
    .then(function(j){msg('mdrill','rodando: '+j.cmd,1);desde=0;abreExec();segue()})
    .catch(function(x){msg('mdrill',x.message,0)})}

function coletar(){
  var adv=$('fadvisor').checked && advOk;
  var b={caso:$('fcaso').value,conn:$('fconn').value,
         modo:document.querySelector('input[name=modo]:checked').value,
         advisor:adv,advisor_confirmado:adv};
  $('bcol').disabled=true;
  fechaConn();
  api('/api/coletar',b).then(function(j){
    msg('mcol','rodando: '+j.cmd,1); desde=0; abreExec(); segue()
  }).catch(function(x){
    // Servidor que nao existe nao e beco sem saida: o proprio erro vira o
    // formulario de cadastro, ja preenchido com o que da para adivinhar.
    if(x.dados&&x.dados.existe===false){abreConn(x.dados);return}
    msg('mcol',x.message,0)})
   .finally(function(){$('bcol').disabled=false})}

/* ── Servidor fora do repositorio ────────────────────────────
   Antes o nome errado so aparecia como rc=2 no fim do log, e cadastrar exigia
   sair do painel e rodar COLETAR.py --add no terminal. */
function abreConn(d){
  var s=d.sugestao||{};
  $('ncnome').value=d.nome; $('nchost').value=s.host||'';
  $('ncport').value=s.port||''; $('ncsvc').value=s.service||'';
  $('ncuser').value=s.user||'';
  // As parecidas vem primeiro: erro de digitacao e mais comum que banco novo,
  // e cadastrar um nome torto cria uma entrada duplicada para sempre.
  var p=d.parecidas||[];
  $('ncpar').innerHTML = p.length
    ? '<div class="ncpb"><span class="l">Voce quis dizer:</span>'+
      p.map(function(n){return '<button type="button" class="ncsug" '+
        'onclick="usaConn(\''+esc(n)+'\')">'+esc(n)+'</button>'}).join('')+
      '</div>' : '';
  $('boxconn').style.display='block';
  $('mcol').className='msg err';
  $('mcol').textContent="'"+d.nome+"' nao esta no repositorio de conexoes";
  ($('nchost')).focus()}

function fechaConn(){$('boxconn').style.display='none';
  $('mconn').className='msg';$('mconn').textContent=''}

/* Clicou numa sugestao: era erro de digitacao. Corrige e coleta. */
function usaConn(nome){$('fconn').value=nome; fechaConn(); coletar()}

function salvaConn(){
  api('/api/conexao',{nome:$('ncnome').value,host:$('nchost').value,
    port:$('ncport').value,service:$('ncsvc').value,user:$('ncuser').value})
    .then(function(r){
      msg('mconn',r.msg,1);
      $('fconn').value=r.nome;
      fechaConn();
      // Recarrega a lista antes de coletar: sem isso a conexao recem-criada
      // nao apareceria no datalist e o proximo Coletar diria de novo que ela
      // nao existe.
      carrega(); coletar()
    }).catch(function(x){msg('mconn',x.message,0)})}

/* Regerar relatorio NAO e redigir: reanalisa a evidencia ja gravada e refaz
   dashboard/PPTX/.md. Os dois nomes ja colidiram uma vez neste arquivo - a
   segunda definicao vencia calada e o botao fazia a coisa errada. */
function regeraRelatorio(){
  var c=sel||$('fcaso').value;
  if(!c){msg('mred','selecione um caso',0);return}
  api('/api/redija',{caso:c}).then(function(j){
    msg('mred','rodando: '+j.cmd,1); desde=0; abreExec(); segue()
  }).catch(function(x){msg('mred',x.message,0)})}

function caso(acao){
  var b={acao:acao,caso:sel,texto:acao==='hipotese'?$('fhip').value:$('facao').value,
    motivo:$('fmot').value,risco:$('frisco').value,status:$('fstatus').value,
    causa:$('fcausa').value,solucao:$('fsol').value,prevencao:$('fprev').value};
  api('/api/caso',b).then(function(r){msg('mcaso',r.msg,1);
    if(acao==='acao')$('facao').value='';
    // Fechou o caso: o texto do chamado sobe na hora, em primeiro plano. A
    // etapa 3 tambem e preparada, para quando o modal for fechado.
    if(acao==='resolver'){mostraEtapa(3);redija(true)}
    carrega()
  }).catch(function(x){msg('mcaso',x.message,0)})}

/* ── Redigir: o caso no Template Padrao ─────────────────────────
   Ultimo passo do fluxo, na mesma tela em que o caso foi conduzido. Nao
   depende de o relatorio ter sido gerado - le so o que foi registrado. */
var REDCASO='';

/* Fechamento em primeiro plano, igual ao playbook. O destino do texto e outro
   programa: ele precisa aparecer inteiro e sumir assim que for copiado. */
function abreFechamento(){
  if(!REDCASO){msg('mred','gere o template primeiro',0);return}
  $('fredmod').value=$('fredija').value;
  $('redtit').textContent='Fechamento do chamado - '+REDCASO;
  $('redmod').classList.add('on');
  document.body.style.overflow='hidden';
  $('fredmod').focus()}

function fechaRed(ev){
  if(ev&&ev.target&&ev.target.id!=='redmod')return;
  $('redmod').classList.remove('on');
  document.body.style.overflow='';
  $('mredm').className='msg';$('mredm').textContent=''}

function copiaRedModal(){
  var t=$('fredmod'); if(!t.value){return}
  var ok=function(){var e=$('mredm');e.className='msg ok';
    e.textContent='copiado - pode colar no chamado';
    toast('copiado',1)};
  if(navigator.clipboard&&navigator.clipboard.writeText){
    navigator.clipboard.writeText(t.value).then(ok,man)}
  else{man()}
  function man(){t.select();
    try{document.execCommand('copy');ok()}
    catch(e){var m=$('mredm');m.className='msg err';
      m.textContent='copie manualmente (Ctrl+C)'}}}

/* `subir` distingue os dois caminhos: resolver joga o texto na cara; clicar em
   'Redigir' so prepara a etapa 3, porque ali a intencao costuma ser conferir. */
function redija(subir){
  if(!sel){msg('mcaso','selecione um caso',0);return}
  mostraEtapa(3);
  api('/api/caso/redija?caso='+encodeURIComponent(sel)).then(function(d){
    REDCASO=d.caso; $('fredija').value=d.texto;
    $('boxred').style.display='block';
    pintaPropostas(d.propostas);
    msg('mred', d.faltando ? d.faltando+' campo(s) <preencher> - registre no caso'
                           : 'template pronto para copiar', !d.faltando);
    if(subir)abreFechamento()
  }).catch(function(x){msg('mred',x.message,0)})}

/* Caixinhas de preenchimento. Mostra o FATO e, junto, o contorno do playbook com
   o risco - proposta de acao sem risco declarado, num texto que vai para chamado,
   e convite a executar no lugar errado. */
function pintaPropostas(ps){
  ps=ps||[];
  if(!ps.length){$('boxprop').style.display='none';$('props').innerHTML='';return}
  var SEV={crit:'p-crit',warn:'p-warn'};
  $('props').innerHTML=ps.map(function(p){
    var c=p.contorno||{}, pb=p.playbook||{};
    var det;
    if(c.passo){
      det='<div class="mt"><b>Contorno:</b> '+esc(c.passo)+
        (c.risco?' <span class="pill p-warn">risco: '+esc(c.risco)+'</span>':'')+
        (c.antes_de_executar?'<div class="mt"><b>Validar antes:</b> '+
          esc(c.antes_de_executar)+'</div>':'')+
        (pb.titulo?'<div class="mt">playbook: '+esc(pb.titulo)+'</div>':'')+
        '</div>';
    }else if(pb.titulo){
      /* Playbook de diagnostico nao tem passo de contorno. Dizer isso e melhor
         que oferecer um SELECT como se resolvesse o incidente. */
      det='<div class="mt">playbook <b>'+esc(pb.titulo)+
          '</b> e de diagnostico: sem passo de contorno definido</div>';
    }else{
      det=p.recomendacao?'<div class="mt">'+esc(p.recomendacao)+'</div>':'';
    }
    return '<label class="prop"><input type="checkbox" class="pchk" value="'+
      esc(p.id)+'"><span><span class="pill '+(SEV[p.severidade]||'p-none')+'">'+
      esc(p.severidade_label)+'</span> <b>'+esc(p.rotulo)+'</b><div class="mt">'+
      esc(p.causa)+'</div>'+det+'</span></label>'}).join('');
  $('boxprop').style.display='block'}

function aplicaPropostas(){
  var ids=[].slice.call(document.querySelectorAll('.pchk:checked'))
             .map(function(e){return e.value});
  if(!ids.length){msg('mred','marque ao menos uma proposta',0);return}
  api('/api/caso/propostas',{caso:REDCASO||sel,ids:ids}).then(function(d){
    $('fredija').value=d.texto;
    msg('mred',d.aplicadas+' proposta(s) aplicada(s) ao caso'+
        (d.faltando?' - ainda faltam '+d.faltando+' campo(s)':''),1);
    carrega()
  }).catch(function(x){msg('mred',x.message,0)})}

function copiaRedija(){
  var t=$('fredija'); if(!t.value){return}
  // Area de transferencia moderna so existe em contexto seguro; 127.0.0.1 e
  // um, mas o execCommand fica de reserva para navegador antigo do parque.
  var ok=function(){msg('mred','copiado',1)};
  if(navigator.clipboard&&navigator.clipboard.writeText){
    navigator.clipboard.writeText(t.value).then(ok,function(){manual(t)})}
  else{manual(t)}
  function manual(a){a.removeAttribute('readonly');a.select();
    try{document.execCommand('copy');ok()}
    catch(e){msg('mred','copie manualmente (Ctrl+C)',0)}
    a.setAttribute('readonly','readonly')}}

/* Ponte para o Copilot do Teams: monta o bloco, joga na area de transferencia
   e MOSTRA o texto na caixa. Mostrar nao e detalhe - o DBA precisa ver o que
   vai colar num chat antes de colar, ainda mais com nome de servidor dentro. */
function copilotoBloco(anon){
  if(!sel){return}
  api('/api/copiloto?caso='+encodeURIComponent(sel)+'&anon='+(anon?'1':'0'))
   .then(function(d){
    $('fcopi').value=d.texto;
    $('boxcopi').style.display='block';
    copiaCopi(d.anonimizado)
  }).catch(function(x){msg('mred',x.message,0)})}

function copiaCopi(anon){
  var t=$('fcopi'); if(!t.value){return}
  var ok=function(){msg('mred','bloco'+(anon?' anonimizado':'')+' copiado - '+
    'revise antes de colar no chat',1)};
  if(navigator.clipboard&&navigator.clipboard.writeText){
    navigator.clipboard.writeText(t.value).then(ok,ok)}
  else{t.removeAttribute('readonly');t.select();
    try{document.execCommand('copy');ok()}
    catch(e){msg('mred','copie manualmente (Ctrl+C)',0)}
    t.setAttribute('readonly','readonly')}}

function escalar(){
  var para=prompt('Escalar para quem? (ex.: Storage, Aplicacao)','');
  if(para===null)return;
  api('/api/caso',{acao:'escalar',caso:sel,para:para}).then(function(r){
    msg('mcaso',r.msg,1); carrega()}).catch(function(x){msg('mcaso',x.message,0)})}

function fimDoDia(){
  api('/api/caso',{acao:'fim-do-dia'}).then(function(r){
    msg('mcons','consolidado: '+r.msg,1)}).catch(function(x){msg('mcons',x.message,0)})}

function abreRel(){if(sel)window.open('/rel/'+encodeURIComponent(sel),'_blank')}

/* ── Artefatos do caso ───────────────────────────────────────
   Icone como SVG inline: o painel roda offline e a partir de pendrive, entao
   nao pode depender de fonte de icone nem de CDN. */
var ICO={
 dashboard:'<path d="M3 3h18v18H3z"/><path d="M3 9h18M9 21V9"/>',
 historico:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
 pptx:'<path d="M2 3h20v13H2z"/><path d="M8 21h8M12 16v5"/>',
 relatorio:'<path d="M14 2H6v20h12V8z"/><path d="M14 2v6h6M9 13h6M9 17h6"/>',
 achados:'<path d="M4 7V4h16v3"/><path d="M9 20h6M12 4v16"/>',
 pasta:'<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>'};

function pintaArtes(caso){
  var el=$('artes'); if(!el)return;
  el.innerHTML='<div class="vazio">carregando...</div>';
  api('/api/caso/artefatos?caso='+encodeURIComponent(caso)).then(function(r){
    el.innerHTML=(r.itens||[]).map(function(a){
      var svg='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '+
        'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'+
        (ICO[a.chave]||'')+'</svg>';
      // O title muda quando o arquivo nao existe: sem isso o cartao apagado
      // nao explica o motivo, e o DBA fica clicando num botao morto.
      var t=a.existe?a.dica:'ainda nao gerado - rode "Regerar relatorio"';
      return '<button type="button" class="arte" title="'+esc(t)+'"'+
        (a.existe?'':' disabled')+' onclick="abreArte(\''+esc(a.chave)+'\')">'+
        svg+'<span><span class="t">'+esc(a.rotulo)+'</span>'+
        '<span class="s">'+esc(a.existe?a.arquivo:'nao gerado')+'</span></span></button>'
    }).join('')||'<div class="vazio">nada gerado ainda</div>'
  }).catch(function(x){el.innerHTML='<div class="vazio">'+esc(x.message)+'</div>'})}

function abreArte(alvo){
  if(!sel){msg('mred','selecione um caso',0);return}
  // O dashboard e HTML: abre em aba, que e onde ele e lido. O resto vai para o
  // programa padrao do Windows - o navegador baixaria o .pptx em vez de abrir.
  if(alvo==='dashboard'){abreRel();return}
  api('/api/abrir',{caso:sel,alvo:alvo})
    .then(function(r){msg('mred',r.msg,1)})
    .catch(function(x){msg('mred',x.message,0)})}

function sugereCaso(){
  var c=$('fconn').value.trim().toUpperCase();
  if(c && (!$('fcaso').value || /^HC_/.test($('fcaso').value)))
    $('fcaso').value='HC_'+c.replace(/[^A-Z0-9_.-]/g,'')}

/* Rapido e Parcial ficam recolhidos, e isso abre um buraco: escolher "Rapido",
   fechar o bloco e ver so o "Completo" na tela faria o DBA coletar o modo
   errado achando que esta no recomendado - e, no caso do Rapido, ainda
   consumindo pack. Por isso o proprio resumo passa a dizer o que esta
   marcado (e fica laranja) sempre que a escolha nao for a padrao. */
var ROTMODO={rapido:'Rapido (triagem) - ainda usa pack',
             parcial:'Parcial - sem consumir pack'};
function modoEscolhido(){
  var s=$('smodos'); if(!s)return;
  var m=document.querySelector('input[name=modo]:checked');
  var v=m?m.value:'completo';
  if(v==='completo'){
    s.textContent='Outros modos: triagem rapida, ou sem consumir pack';
    s.className=''}
  else{
    s.textContent='Modo escolhido: '+ROTMODO[v];
    s.className='alt'}
  /* Sair do Completo desmarca o advisor: o CSS apenas esconde a caixa, e uma
     caixa marcada e invisivel viraria um --advisor que o backend recusa (ele
     so roda no completo) - erro na cara do DBA por algo que ele nao ve. */
  if(v!=='completo' && $('fadvisor').checked){
    $('fadvisor').checked=false; pintaAdvisor()}}

/* ── Advisor: o unico comando da tela que ESCREVE no banco ──────
   Marcar a caixa nao basta: abre o modal e a marca so fica de pe se o DBA
   confirmar. Cancelar (ou clicar fora, ou Esc) desmarca.

   `advOk` e guardado separado do estado da caixa porque o navegador RESTAURA
   checkbox em recarga (F5, voltar): sem isso a caixa voltaria marcada sem
   ninguem ter confirmado nada, e o pedido sairia com o advisor ligado. O
   backend exige as duas chaves justamente para nao depender so da tela. */
var advOk=false;

function marcaAdvisor(cx){
  if(!cx.checked){advOk=false;pintaAdvisor();return}
  var srv=($('fconn').value||'').trim();
  $('advsrv').textContent=srv||'(servidor ainda nao escolhido)';
  $('madv').classList.add('on');
  document.body.style.overflow='hidden';
  var b=$('madv').querySelector('.btn'); if(b)b.focus()}

function fechaAdv(){
  $('madv').classList.remove('on');
  document.body.style.overflow=''}

/* Clicar no fundo equivale a cancelar: sair sem decidir nao pode deixar um
   opt-in de escrita pendurado na tela. */
function advFora(ev){
  if(ev&&ev.target&&ev.target.id!=='madv')return;
  advNao()}

function advSim(){advOk=true;fechaAdv();pintaAdvisor()}

function advNao(){
  $('fadvisor').checked=false; advOk=false;
  fechaAdv(); pintaAdvisor()}

/* O cartao se chama "Nova coleta (read-only)". Com o advisor marcado isso
   deixa de ser verdade, e o titulo tem de dizer. */
function pintaAdvisor(){
  var on=$('fadvisor').checked;
  $('hro').style.display=on?'none':'';
  $('hesc').style.display=on?'':'none';
  $('bcol').textContent=on?'Coletar + advisor':'Coletar';
  $('bcol').className=on?'btn perigo':'btn pri'}

function pintaJob(j){
  var d=$('dot'), t=$('stxt');
  if(!j){d.className='dot';t.textContent='sem execucao';return}
  if(j.rodando){d.className='dot run';t.textContent='rodando: '+j.cmd;
    // Comando em curso e a coisa mais importante da tela: a faixa abre sozinha.
    abreExec()}
  else{d.className='dot '+(j.rc===0?'ok':'err');
    t.textContent=(j.rc===0?'terminou ok':'terminou com erro (rc='+j.rc+')')+
      ' - '+j.cmd}}

function segue(){
  if(timer)clearInterval(timer);
  puxaLog();
  timer=setInterval(puxaLog,900)}

function puxaLog(){
  api('/api/log?desde='+desde).then(function(l){
    if(l.linhas && l.linhas.length){
      var e=$('log');
      if(desde===0)e.textContent='';
      e.textContent+=l.linhas.join('\n')+'\n';
      e.scrollTop=e.scrollHeight; desde=l.total}
    jobAtual=l.id||null;
    pintaPergunta(l);
    pintaJob(l.id?l:null);
    if(!l.rodando && timer){clearInterval(timer);timer=null;carrega()}
  }).catch(function(){})}

/* ── Pergunta do comando em execucao ──────────────────────────
   O CLI anuncia que parou para perguntar; aqui so mostramos e devolvemos a
   resposta. Nada de adivinhar pelo texto do log: quem pergunta avisa. */
function pintaPergunta(l){
  var box=$('perg'), p=l&&l.pergunta;
  if(!p){pergAtual=null;box.style.display='none';return}
  var nova=(pergAtual!==p.label);
  pergAtual=p.label;
  $('pqLabel').textContent=p.label+(p.default?'  ['+p.default+']':'');
  var i=$('fperg');
  i.type=p.secreto?'password':'text';
  i.placeholder=p.secreto?'a senha nao aparece no log':(p.default||'');
  box.style.display='block';
  // Pergunta escondida trava a coleta em silencio - a faixa TEM de estar aberta,
  // e na aba certa: a caixa de senha vive na Saida, e o DBA pode estar olhando
  // o Historico quando o CLI para para perguntar.
  abreExec(nova);
  /* so limpa e foca quando a pergunta MUDA: o polling roda a cada 900ms e
     apagaria o que o DBA esta digitando */
  if(nova){i.value='';i.focus()}}

function responde(){
  var i=$('fperg');
  api('/api/responder',{job:jobAtual,valor:i.value}).then(function(){
    i.value='';pergAtual=null;$('perg').style.display='none';segue()
  }).catch(function(x){msg('mcol',x.message,0)})}

function cancelaJob(){
  api('/api/cancelar',{job:jobAtual}).then(function(){
    pergAtual=null;$('perg').style.display='none';segue()
  }).catch(function(x){msg('mcol',x.message,0)})}

/* ── Base de conhecimento ─────────────────────────────────── */
var pbSel=null, kbTimer=null;

function buscaKb(){
  /* espera o DBA parar de digitar: a busca e local, mas nao ha razao para
     disparar uma chamada por tecla */
  if(kbTimer)clearTimeout(kbTimer);
  kbTimer=setTimeout(carregaKb,180)}

function carregaKb(){
  var t=$('fkb')?$('fkb').value:'';
  return api('/api/kb?q='+encodeURIComponent(t)).then(function(r){
    pintaKb(r.playbooks||[])
  }).catch(function(){
    $('pbs').innerHTML='<div class="vazio">falha ao ler a base</div>'})}

function pintaKb(ps){
  if(!ps.length){
    $('pbs').innerHTML='<div class="vazio">nenhum playbook para essa busca</div>';
    return}
  $('pbs').innerHTML=ps.map(function(p){
    var ora=(p.erros_ora||[]).length?' &middot; '+esc(p.erros_ora.join(', ')):'';
    return '<div class="pb'+(p.id===pbSel?' sel':'')+
      '" onclick="abrePb(\''+esc(p.id)+'\')">'+
      '<div class="t"><span>'+esc(p.titulo)+'</span>'+
      '<span class="n">'+p.passos+' passos</span></div>'+
      '<div class="q">'+esc(p.quando_usar||'').slice(0,110)+ora+'</div></div>'
  }).join('')}

function abrePb(id){
  pbSel=id; carregaKb();
  $('pbtit').textContent='Carregando...';
  $('pbsub').textContent='';$('pbrisco').innerHTML='';
  $('pbdet').innerHTML='<div class="vazio">carregando o passo a passo...</div>';
  abreModal();
  api('/api/kb/playbook?id='+encodeURIComponent(id)).then(pintaPb)
    .catch(function(x){$('pbdet').innerHTML=
      '<div class="msg err">'+esc(x.message)+'</div>'})}

function abreModal(){
  $('pbmod').classList.add('on');
  /* trava o scroll do fundo: sem isso a roda do mouse rola a pagina de tras
     quando o ponteiro sai do quadro, e da a impressao de que travou */
  document.body.style.overflow='hidden';
  $('pbmod').querySelector('.xis').focus()}

function fechaPb(ev){
  /* clique no fundo fecha; no quadro nao (o stopPropagation do .box cuida
     disso, e a checagem abaixo protege se o HTML mudar) */
  if(ev&&ev.target&&ev.target.id!=='pbmod')return;
  $('pbmod').classList.remove('on');
  document.body.style.overflow='';
  pbSel=null; carregaKb();
  if($('fkb'))$('fkb').focus()}

/* Esc no advisor RECUSA (advNao desmarca a caixa): sair sem decidir nao pode
   deixar um opt-in de escrita pendurado. */
document.addEventListener('keydown',function(e){
  if(e.key!=='Escape')return;
  if($('madv').classList.contains('on')){advNao();return}
  // O de fechamento vem primeiro: e o que pode estar por cima do outro.
  if($('redmod').classList.contains('on')){fechaRed();return}
  if($('pbmod').classList.contains('on'))fechaPb()});

function copiaCmd(b){
  var t=b.parentNode.querySelector('pre').textContent;
  var ok=function(){b.textContent='copiado';
    setTimeout(function(){b.textContent='copiar'},1400)};
  if(navigator.clipboard&&navigator.clipboard.writeText){
    navigator.clipboard.writeText(t).then(ok,function(){b.textContent='falhou'})}
  else{ /* http em maquina sem clipboard API: seleciona para o Ctrl+C */
    var r=document.createRange();r.selectNodeContents(b.parentNode.querySelector('pre'));
    var s=getSelection();s.removeAllRanges();s.addRange(r);ok()}}

function pintaPb(p){
  $('pbtit').textContent=p.titulo||'';
  $('pbsub').textContent=p.quando_usar||'';
  /* conta os passos por risco: o DBA ve o que o playbook exige dele ANTES de
     rolar ate o fim */
  var c={leitura:0,mudanca:0,destrutivo:0}, ps=p.passos||[];
  ps.forEach(function(s){var r=s.risco||'leitura';if(r in c)c[r]++});
  var t='<span class="tagr onde">'+ps.length+' passos</span>';
  ['leitura','mudanca','destrutivo'].forEach(function(r){
    if(c[r])t+='<span class="tagr '+r+'">'+c[r]+' '+r+'</span>'});
  (p.erros_ora||[]).forEach(function(e){
    t+='<span class="tagr onde">'+esc(e)+'</span>'});
  $('pbrisco').innerHTML=t;

  var h='';
  if(p.nota)h+='<div class="aviso">'+esc(p.nota)+'</div>';
  h+=ps.map(function(s,i){
    var r=s.risco||'leitura';
    /* escalona a entrada de cada passo, limitando o atraso: numa lista de 9
       passos, atraso proporcional deixaria o ultimo aparecer tarde demais */
    var d=Math.min(i*35,280);
    var o='<div class="passo r-'+esc(r)+'" style="animation-delay:'+d+'ms">'+
      '<h4><span><span class="num">'+(i+1)+'.</span>'+esc(s.titulo)+'</span>'+
      '<span style="display:flex;gap:5px">'+
      (s.onde?'<span class="tagr onde">'+esc(s.onde)+'</span>':'')+
      '<span class="tagr '+esc(r)+'">'+esc(r)+'</span></span></h4>';
    if(s.objetivo)o+='<div class="obj">'+esc(s.objetivo)+'</div>';
    if(s.antes_de_executar)o+='<div class="bloco antes"><b>Antes de executar</b>'+
      esc(s.antes_de_executar)+'</div>';
    if(s.comando)o+='<div class="pre-wrap"><pre>'+esc(s.comando)+'</pre>'+
      '<button class="cp" onclick="copiaCmd(this)">copiar</button></div>';
    if(s.olhar)o+='<div class="bloco olhar"><b>O que olhar</b>'+
      esc(s.olhar)+'</div>';
    if(s.armadilha)o+='<div class="bloco arma"><b>Armadilha</b>'+
      esc(s.armadilha)+'</div>';
    return o+'</div>'}).join('');
  if(p.fontes&&p.fontes.length)h+='<div class="hint"><b>Fontes:</b> '+
    esc(p.fontes.join(' | '))+'</div>';
  $('pbdet').innerHTML=h;
  $('pbdet').scrollTop=0}

aplicaTema(temaInicial());
/* Recarga nao herda opt-in: o navegador restaura o estado da caixa, a
   confirmacao do modal nao. Zerar aqui mantem os dois em sincronia. */
$('fadvisor').checked=false; advOk=false; pintaAdvisor();
carrega();
carregaKb();
setInterval(function(){if(!timer)carrega()},15000);
</script></body></html>
"""

def _bloco_leitores() -> str:
    """Chips do que o motor sabe ler, gerados do catalogo de `saida.py`.

    Escrever a lista a mao no HTML faria a tela envelhecer calada: leitor novo
    no modulo e a tela seguindo prometendo o conjunto antigo. Aqui a fonte e uma
    so - e um teste garante que todo leitor aparece.
    """
    chips = "".join(
        f'<span class="leio" title="{exemplo}"><b>{rotulo}</b></span>'
        for _nome, rotulo, exemplo in saida.O_QUE_LEIO)
    return ('<details class="oqueleio"><summary>o que eu sei ler desta caixa '
            f'({len(saida.O_QUE_LEIO)} tipos de saida)</summary>'
            f'<div class="leios">{chips}</div>'
            '<div class="hint">Passe o mouse para ver o exemplo. O que nao for '
            'reconhecido <b>nao vira achado</b>: a tela diz que nao entendeu, '
            'em vez de inventar leitura.</div></details>')


# A marca entra por substituicao, e nao escrita a mao aqui, para o painel e os
# relatorios do report.py servirem o MESMO desenho: sao superficies diferentes
# que o usuario ve lado a lado (tela e PDF do chamado).
PAGINA = (PAGINA
          .replace("/*CSS-MARCA*/", marca.CSS.strip())
          .replace("<!--MARCA-->", marca.assinatura())
          .replace("<!--RODAPE-->", marca.rodape())
          .replace("<!--LEITORES-->", _bloco_leitores()))


# ── HTTP ──────────────────────────────────────────────────────
def _handler(painel: Painel):
    class H(BaseHTTPRequestHandler):
        server_version = "PainelTroubleshoot/1.0"
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):  # silencia o log de acesso no console
            pass

        # -- helpers
        def _envia(self, corpo: bytes, tipo: str, code: int = 200) -> None:
            self.send_response(code)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(corpo)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(corpo)
            except (BrokenPipeError, ConnectionAbortedError):  # aba fechada
                pass

        def _json(self, dados: dict, code: int = 200) -> None:
            self._envia(json.dumps(dados, ensure_ascii=False).encode("utf-8"),
                        "application/json; charset=utf-8", code)

        def _corpo(self) -> dict:
            """Corpo JSON do POST, ja com os tipos que o resto do painel supoe.

            O JSON e escrito pelo cliente, entao `texto` pode chegar como numero
            e `caso` como lista. As acoes fazem `(dados.get(x) or "").strip()` -
            que estoura `AttributeError` e devolve **500 com stacktrace** em vez
            de um 400 com instrucao. Sanear aqui, no unico ponto por onde todo
            POST passa, evita repetir a checagem em cada campo de cada acao (e
            esquecer no campo novo).

            Numero vira texto porque e digitavel num formulario ('42' e um nome
            de caso plausivel) e `null` vira string vazia. **Lista e dicionario
            passam intactos**: ha rotas que os recebem de verdade (`ids` das
            propostas, por exemplo), e achatar tudo aqui quebraria justamente
            quem esta certo. O tipo errado num campo de TEXTO e barrado adiante,
            por `_txt`, que e onde se sabe que o campo devia ser texto.
            """
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            try:
                dados = json.loads(self.rfile.read(n).decode("utf-8")) or {}
            except Exception:
                return {}
            if not isinstance(dados, dict):
                return {}
            for k, v in list(dados.items()):
                if v is None:
                    dados[k] = ""
                elif isinstance(v, (int, float)) and not isinstance(v, bool):
                    dados[k] = str(v)
            return dados

        # -- porteiro
        def _autorizado(self, u) -> bool:
            """So a pagina servida por este processo fala com a API.

            Escutar em 127.0.0.1 protege contra a REDE, nao contra o proprio
            navegador: qualquer aba aberta em qualquer site pode disparar
            `fetch('http://127.0.0.1:8765/api/coletar', ...)`. Sem porteiro,
            uma pagina qualquer inicia coleta em banco de producao.

            O token nasce a cada abertura do painel, e injetado na pagina e
            viaja no cabecalho. Um site de fora nao tem como le-lo (a
            same-origin policy impede que ele leia a resposta do GET /).
            """
            if not u.path.startswith("/api/"):
                return True          # pagina, relatorio e artefato: navegacao
            enviado = (self.headers.get("X-Painel-Token")
                       or (parse_qs(u.query).get("t") or [""])[0])
            return secrets.compare_digest(str(enviado), painel.token)

        def _porta_fechada(self) -> None:
            self._json({"erro": "requisicao sem o token da sessao. Recarregue "
                                "o painel (F5) na aba aberta por PAINEL.py."}, 403)

        # -- rotas
        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if not self._autorizado(u):
                return self._porta_fechada()
            try:
                if u.path in ("/", "/index.html"):
                    # O token entra AQUI, na resposta, e nao na constante:
                    # PAGINA e global e um segredo gravado nela vazaria para
                    # qualquer outro processo que importasse o modulo.
                    pagina = PAGINA.replace("__TOKEN__", painel.token)
                    return self._envia(pagina.encode("utf-8"),
                                       "text/html; charset=utf-8")
                if u.path == "/api/estado":
                    return self._json(painel.estado())
                if u.path == "/api/log":
                    jid = (q.get("id") or [""])[0]
                    job = painel.acha_job(jid) if jid else painel.job
                    if job is None:
                        return self._json({"linhas": [], "total": 0,
                                           "rodando": False, "rc": None})
                    return self._json(job.log(
                        int((q.get("desde") or ["0"])[0] or 0)))
                if u.path == "/api/caso":
                    return self._json(painel.detalhe_caso(
                        (q.get("caso") or [""])[0]))
                if u.path == "/api/caso/redija":
                    return self._json(painel.redija_caso(
                        (q.get("caso") or [""])[0]))
                if u.path == "/api/caso/artefatos":
                    return self._json({"itens": painel.artefatos(
                        (q.get("caso") or [""])[0])})
                if u.path == "/api/historico":
                    return self._json(painel.historico_ambiente(
                        (q.get("caso") or [""])[0]))
                if u.path == "/api/conexao/checar":
                    return self._json(painel.checar_conexao(
                        (q.get("nome") or [""])[0]))
                if u.path == "/api/guia":
                    return self._json(painel.guia_estado(
                        (q.get("caso") or [""])[0]))
                if u.path == "/api/copiloto":
                    return self._json(painel.copiloto_texto(
                        (q.get("caso") or [""])[0],
                        (q.get("anon") or [""])[0] == "1"))
                if u.path == "/api/kb":
                    termo = (q.get("q") or [""])[0]
                    return self._json({"termo": termo,
                                       "playbooks": _kb_lista(termo)})
                if u.path == "/api/kb/playbook":
                    pb = kb.por_id((q.get("id") or [""])[0])
                    if not pb:
                        return self._json({"erro": "playbook nao encontrado"}, 404)
                    return self._json(pb)
                if u.path.startswith("/rel/"):
                    p = caminho_relatorio(u.path[len("/rel/"):])
                    if not p:
                        return self._json({"erro": "relatorio ainda nao gerado"}, 404)
                    with open(p, "rb") as fh:
                        return self._envia(fh.read(), "text/html; charset=utf-8")
                if u.path == "/indice":
                    p = os.path.join(OUTPUT_DIR, "index.html")
                    if not os.path.exists(p):
                        return self._json({"erro": "indice ainda nao gerado"}, 404)
                    with open(p, "rb") as fh:
                        return self._envia(fh.read(), "text/html; charset=utf-8")
                return self._json({"erro": "rota desconhecida"}, 404)
            except ValueError as e:
                return self._json({"erro": str(e)}, 400)
            except Exception as e:  # pragma: no cover - defensivo
                return self._json({"erro": f"{type(e).__name__}: {e}"}, 500)

        def do_POST(self):
            u = urlparse(self.path)
            # O corpo e lido ANTES do porteiro de proposito: recusar sem drenar
            # a requisicao deixa bytes no socket e o keep-alive do HTTP/1.1
            # entrega esses bytes como se fossem a proxima requisicao.
            d = self._corpo()
            if not self._autorizado(u):
                return self._porta_fechada()
            try:
                if u.path == "/api/coletar":
                    # Duas chaves separadas de proposito: `advisor` e a caixa
                    # marcada, `advisor_confirmado` e o popup respondido. Uma so
                    # bandeira viraria advisor por acidente em qualquer cliente
                    # que mandasse o corpo errado - e este e o unico comando do
                    # painel que escreve no banco.
                    adv = bool(d.get("advisor")) and bool(d.get("advisor_confirmado"))
                    if d.get("advisor") and not d.get("advisor_confirmado"):
                        return self._json({"erro": (
                            "o SQL Tuning Advisor exige confirmacao explicita: "
                            "ele escreve no dicionario (tuning tasks) e consome "
                            "Tuning Pack")}, 400)
                    cmd = montar_cmd_coletar(d.get("caso", ""), d.get("conn", ""),
                                             d.get("modo", "completo"),
                                             painel.python, advisor=adv)
                    # Servidor inexistente e barrado ANTES de abrir processo. O
                    # 409 carrega o que a tela precisa para oferecer o cadastro,
                    # em vez de deixar o DBA esperando por um rc=2.
                    ck = painel.checar_conexao(d.get("conn", ""))
                    if not ck["existe"]:
                        return self._json({"erro": (
                            f"o servidor '{ck['nome']}' nao existe no "
                            "repositorio de conexoes"), **ck}, 409)
                    job = painel.iniciar(cmd, advisor_ok=adv)
                    return self._json({"ok": True, "job": job.id,
                                       "cmd": " ".join(job.cmd[1:])})
                if u.path == "/api/conexao":
                    return self._json(painel.cadastrar_conexao(d))
                if u.path == "/api/abrir":
                    return self._json(painel.abrir_artefato(
                        d.get("caso", ""), d.get("alvo", "")))
                if u.path == "/api/redija":
                    cmd = montar_cmd_redija(d.get("caso", ""), painel.python)
                    job = painel.iniciar(cmd)
                    return self._json({"ok": True, "job": job.id,
                                       "cmd": " ".join(job.cmd[1:])})
                if u.path == "/api/detalhar":
                    caso = d.get("caso", "")
                    conn = (d.get("conn") or "").strip() or painel.conn_do_caso(caso)
                    cmd = montar_cmd_detalhar(caso, d.get("sql_id", ""), conn,
                                              painel.python)
                    job = painel.iniciar(cmd)
                    return self._json({"ok": True, "job": job.id,
                                       "cmd": " ".join(job.cmd[1:])})
                if u.path == "/api/janela":
                    caso = d.get("caso", "")
                    conn = (d.get("conn") or "").strip() or painel.conn_do_caso(caso)
                    cmd = montar_cmd_janela(caso, d.get("inicio", ""),
                                            d.get("fim", ""), conn, painel.python)
                    job = painel.iniciar(cmd)
                    return self._json({"ok": True, "job": job.id,
                                       "cmd": " ".join(job.cmd[1:])})
                if u.path == "/api/responder":
                    jid = (d.get("job") or "").strip()
                    job = painel.acha_job(jid) if jid else painel.job
                    if job is None:
                        return self._json({"erro": "nenhuma execucao"}, 404)
                    if not job.responder(str(d.get("valor", ""))):
                        # Pergunta ja respondida ou processo encerrado: 409 e nao
                        # 500 - nao e defeito, e corrida entre a tela e o filho.
                        return self._json({"erro": "nao ha pergunta pendente"}, 409)
                    return self._json({"ok": True})
                if u.path == "/api/cancelar":
                    jid = (d.get("job") or "").strip()
                    job = painel.acha_job(jid) if jid else painel.job
                    if job is None or not job.cancelar():
                        return self._json({"erro": "nada em execucao"}, 409)
                    return self._json({"ok": True})
                if u.path == "/api/caso/propostas":
                    ids = d.get("ids") or []
                    if not isinstance(ids, list):
                        return self._json({"erro": "ids deve ser lista"}, 400)
                    return self._json(painel.aplicar_propostas(
                        d.get("caso", ""), [str(i) for i in ids]))
                if u.path == "/api/caso":
                    return self._json(painel.acao_caso(d.get("acao", ""), d))
                if u.path == "/api/guia":
                    return self._json(painel.guia_acao(d.get("acao", ""), d))
                if u.path == "/api/historico/marco":
                    return self._json(painel.marcar_historico(d))
                if u.path == "/api/executar":
                    # Vira JOB, e nao chamada sincrona, porque conecta no banco
                    # e portanto pede senha - e a senha so tem um caminho no
                    # painel: a caixa mascarada da faixa de execucao, pelo
                    # stdin do subprocesso (protocolo `dialogo`).
                    caso = d.get("caso", "")
                    conn = (d.get("conn") or "").strip() or painel.conn_do_caso(caso)
                    params = d.get("params") or {}
                    if not isinstance(params, dict):
                        return self._json({"erro": "params deve ser objeto"}, 400)
                    cmd = montar_cmd_executar(caso, conn, params,
                                              tudo=bool(d.get("tudo")),
                                              python=painel.python)
                    job = painel.iniciar(cmd)
                    return self._json({"ok": True, "job": job.id,
                                       "cmd": " ".join(job.cmd[1:])})
                if u.path == "/api/execucoes/limpar":
                    return self._json(painel.limpar_execucoes())
                return self._json({"erro": "rota desconhecida"}, 404)
            except ValueError as e:
                return self._json({"erro": str(e)}, 400)
            except RuntimeError as e:
                return self._json({"erro": str(e)}, 409)
            except Exception as e:  # pragma: no cover - defensivo
                return self._json({"erro": f"{type(e).__name__}: {e}"}, 500)

    return H


def criar_servidor(host: str = "127.0.0.1", port: int = 8765,
                   painel: Painel = None) -> ThreadingHTTPServer:
    """Servidor do painel. Loopback por padrao: evidencia de producao nao pode
    ficar exposta na rede corporativa por descuido."""
    srv = ThreadingHTTPServer((host, port), _handler(painel or Painel()))
    srv.daemon_threads = True
    return srv
