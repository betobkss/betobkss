"""
saida.py - o que o DBA COLA de volta vira evidencia, e a evidencia move o motor.

O guiado ja dizia o que rodar e o que olhar. Faltava o retorno: o DBA executava
no terminal, lia o resultado com os proprios olhos e o framework continuava
achando que o caso era o que a coleta inicial dizia. O raciocinio parava na
primeira foto.

Aqui a saida colada e interpretada e vira achado, no MESMO formato do
`<caso>_findings.json`. A partir dai o motor de `raciocinio` reavalia tudo:
hipotese ganha ponto, cai por refutacao ou vira consequencia de outra. E o
troubleshooting deixa de ser um roteiro fixo e passa a reagir ao que foi
observado agora.

O que este modulo NAO faz, e o motivo
-------------------------------------
Nao adivinha. Cada leitor reconhece uma forma concreta de saida (codigo ORA-,
coluna de bloqueio, evento com percentual, valor de parametro, ausencia de
linhas) e diz de onde tirou a conclusao. Texto que nenhum leitor reconhece nao
vira achado nenhum - devolve vazio e avisa. Num framework cuja regra numero um
e "nao de palpite sem evidencia", inventar leitura de um texto que nao foi
entendido seria pior do que nao ler.

A ausencia tambem e evidencia
-----------------------------
`no rows selected` na consulta de bloqueio nao e "nada aconteceu": e a resposta
que DERRUBA a hipotese de lock. Este e o ganho menos obvio e o mais valioso do
modulo - um passo que volta vazio costuma ser descartado como inutil, quando na
verdade acabou de eliminar um caminho inteiro da investigacao.
"""
from __future__ import annotations

import contextvars
import re
from datetime import date, datetime

from . import erros as dic_erros

# Secao com que os achados observados entram no motor. Nome proprio de
# proposito: no relatorio e na tela tem de dar para separar o que a coleta
# mediu do que o DBA observou conduzindo.
SECAO = "Passo a passo (saida observada)"

# Marcador do texto no findings: quem le o chamado precisa saber que aquele
# achado nao veio da coleta automatica.
PREFIXO = "[observado]"

# ── ausencia de linhas ────────────────────────────────────────
# 'no rows selected' so vira refutacao quando se sabe O QUE foi perguntado.
# A pergunta vem do passo do playbook; estas marcas traduzem a pergunta para a
# frase canonica que o motor de raciocinio reconhece como sinal CONTRA.
#
# Sem esse pareamento, um passo vazio nao diria nada - e ele acabou de eliminar
# uma hipotese inteira.
# A ORDEM e significativa: a pergunta "ha gap de archive no standby?" contem as
# duas palavras, e responder 'archive' ali seria descartar a hipotese errada.
# Do mais especifico para o mais generico.
VAZIO_REFUTA = (
    (("standby", "data guard", "dataguard", "apply lag", "mrp", "gap"),
     "standby sem gap na verificacao"),
    (("bloque", "lock", "enq:", "blocking", "cadeia"),
     "sem sessao bloqueada no momento da verificacao"),
    (("invalid",),
     "sem objeto invalido na verificacao"),
    (("archive", "fra", "recovery area"),
     "archive/FRA sem pendencia na verificacao"),
    (("temp", "undo", "tablespace", "espaco"),
     "espaco dentro do esperado na verificacao"),
    (("i/o", " io ", "storage", "datafile", "latencia"),
     "storage dentro do esperado na verificacao"),
)

_RE_VAZIO = re.compile(
    r"no rows selected|nenhuma linha selecionada|0 rows selected|"
    r"^\s*sem linhas\s*$|no data found", re.I | re.M)

# Erro de execucao: o comando NAO rodou. Interpretar a saida como se fosse
# resultado seria concluir a partir de uma consulta que nunca aconteceu.
_RE_FALHA = re.compile(
    r"ORA-00942|ORA-00904|ORA-01031|SP2-\d+|command not found|"
    r"not connected|ORA-12154|ORA-01017", re.I)

_RE_PCT = re.compile(r"(\d{1,3}(?:[.,]\d+)?)\s*%")
_RE_SEG = re.compile(r"\b(\d{2,7})\s*(?:s|seg|segundos?)\b", re.I)

# Eventos de espera que valem por si: quando aparecem com peso, mudam o rumo.
# O texto gerado usa as MESMAS palavras que o catalogo de hipoteses reconhece.
EVENTOS = (
    ("enq: tx - row lock contention", "enq: TX - row lock contention"),
    ("enq: tx", "enq: TX"),
    ("enq: tm", "enq: TM - contencao de DML"),
    ("db file sequential read", "db file sequential read"),
    ("db file scattered read", "db file scattered read"),
    ("direct path read", "direct path read"),
    ("log file sync", "log file sync"),
    ("log file parallel write", "log file parallel write"),
    ("gc buffer busy", "gc buffer busy"),
    ("gc cr block", "gc cr block"),
    ("gc current block", "gc current block"),
    ("library cache lock", "library cache lock"),
    ("library cache pin", "library cache pin"),
    ("cursor: pin s wait on x", "cursor: pin S wait on X"),
    ("latch: shared pool", "latch: shared pool"),
    ("resmgr:cpu quantum", "resmgr: cpu quantum"),
    ("resmgr: cpu quantum", "resmgr: cpu quantum"),
    ("sql*net message from client", "SQL*Net message from client"),
    ("sql*net more data", "SQL*Net more data from client"),
    ("buffer busy waits", "buffer busy waits"),
    ("free buffer waits", "free buffer waits"),
    ("read by other session", "read by other session"),
)

# Esperas da classe IDLE: a sessao nao esta travada, esta parada esperando a
# aplicacao mandar o proximo comando. O Oracle exclui essas do Top Timed Events
# justamente por isso, e o proprio AWR nao as soma no DB time.
#
# Tratadas como as demais, elas viravam 'warn' em toda saida de v$session - que
# quase sempre tem sessao ociosa - e podiam ate ser anunciadas como "a maior da
# tabela" num paste de v$session_event. Isso e o oposto do diagnostico: manda
# investigar o cliente ocioso em vez do que consome o banco.
#
# Continuam sendo LIDAS: o bloqueador de uma cadeia costuma estar exatamente
# aqui - transacao aberta, aplicacao sem dar commit. So nao pontuam hipotese.
EVENTOS_IDLE = frozenset((
    "SQL*Net message from client",
))


def _linhas(texto: str) -> list:
    return [l.rstrip() for l in (texto or "").splitlines() if l.strip()]


# Data contra a qual se mede IDADE. Um ContextVar - e nao um parametro em cada
# leitor - porque sao 20 leitores com a assinatura `(texto, pergunta)` e um
# unico deles precisa disto; mudar todas as assinaturas para carregar um dado
# que 19 ignoram troca um problema pequeno por acoplamento permanente.
# `interpretar` seta e reseta num `finally`, entao o escopo e a chamada.
_REFERENCIA: contextvars.ContextVar = contextvars.ContextVar(
    "saida_referencia", default=None)


def data_de_referencia() -> date:
    """Hoje, ou a data que `interpretar(referencia=...)` informou.

    Hoje e o padrao certo no uso normal: a saida acabou de sair do banco. Quem
    RELE evidencia gravada informa a data da coleta - senao o tempo decorrido
    desde ela entra na conta da idade.
    """
    return _REFERENCIA.get() or datetime.now().date()


def _ach(titulo: str, sev: str, porque: str, impacto: str = "",
         leitor: str = "") -> dict:
    """Achado no formato que o motor consome, com a origem sempre declarada."""
    return {"section": SECAO, "severity": sev,
            "title": f"{PREFIXO} {titulo}", "impact": impacto,
            "recommendation": "", "porque": porque, "leitor": leitor}


# ── leitores ──────────────────────────────────────────────────
def _ler_falha(texto: str, _pergunta: str) -> list:
    """O comando nao executou. Nao ha o que concluir - e preciso dizer isso.

    Tratar 'ORA-00942: table or view does not exist' como achado do banco seria
    transformar erro de digitacao em diagnostico.
    """
    m = _RE_FALHA.search(texto)
    if not m:
        return []
    return [_ach(f"o comando nao executou ({m.group(0)}) - nada a concluir "
                 f"deste passo", "info",
                 "a saida e erro de execucao, nao resultado")]


def _tabela_sem_linhas(texto: str) -> bool:
    """Tabela impressa com cabecalho e NENHUMA linha de dado.

    E a outra forma de "consulta vazia", e era um ponto cego: o leitor exigia a
    frase `no rows selected`, que o SQL*Plus imprime no modo interativo mas NAO
    quando a saida vem de spool com cabecalho - nem quando vem do nosso proprio
    `render_table`. Medido na evidencia real de APPHO600: `deadlocks.txt`,
    `corruption.txt`, `dict_health.txt` e `idx_unusable.txt` sao exatamente
    isso, e os quatro passavam em silencio.

    Silencio aqui e caro: verificacao vazia e o que FECHA um caminho da
    investigacao. Perde-se a refutacao - o achado que encurta o incidente.

    Conservador de proposito: se QUALQUER tabela do texto tiver dado, o texto
    nao e vazio. Um passo com tres consultas em que so a primeira voltou vazia
    nao pode ser lido como "nada encontrado".
    """
    linhas = (texto or "").splitlines()
    reguas = [i for i, ln in enumerate(linhas) if _RE_REGUA_COLUNAS.match(ln)]
    if not reguas:
        return False
    for i in reguas:
        for ln in linhas[i + 1:]:
            if not ln.strip():
                continue
            if _RE_TITULO_SECAO.match(ln) or _RE_REGUA_COLUNAS.match(ln):
                break          # comecou outro bloco: esta tabela estava vazia
            if _RE_VAZIO.search(ln):
                break          # 'no rows selected' logo abaixo do cabecalho
            return False       # ha dado
    return True


def _ler_vazio(texto: str, pergunta: str) -> list:
    """Consulta sem linhas: a ausencia que elimina hipotese."""
    if not (_RE_VAZIO.search(texto) or _tabela_sem_linhas(texto)):
        return []
    alvo = (pergunta or "").lower()
    for marcas, frase in VAZIO_REFUTA:
        if any(m in alvo for m in marcas):
            return [_ach(frase, "info",
                         "a consulta voltou sem linhas - a ausencia e a prova",
                         leitor="vazio")]
    # Sem contexto, 'no rows' e so 'no rows': dizer que refuta algo seria
    # escolher a hipotese pelo DBA.
    return [_ach("verificacao sem linhas (sem contexto para concluir)", "info",
                 "a consulta voltou vazia, mas o passo nao diz o que ela "
                 "descartaria", leitor="vazio")]


# Titulo de secao (`=== DEADLOCKS (ORA-00060) ===`) e cabecalho de coluna
# (`ORA_01555 SEM_ESPACO ...`) sao escritos pelo FRAMEWORK, nao pelo banco: o
# subtitulo vem do `mcp/catalog.json`. Um codigo ORA- ali declara o que o check
# PROCURA - nunca o que ele ACHOU.
#
# Ignorar isso era o pior defeito possivel neste modulo, e nao era teorico:
# medido na evidencia real de APPHO600, um `deadlocks.txt` com a tabela VAZIA
# (zero deadlocks) produzia "[warn] ORA-00060 - Deadlock detectado" e deixava a
# hipotese de bloqueio como POSSIVEL (2 pt); um `undo_errors.txt` com a coluna
# `ORA_01555` valendo ZERO sustentava "Snapshot too old". A evidencia dizia o
# oposto da conclusao. Com dois desses e `CORTE_SUSTENTADA = 3`, um terceiro
# arquivo faria o framework AFIRMAR um problema inexistente.
_RE_TITULO_SECAO = re.compile(r"^\s*={2,}.*?={2,}\s*$")
# Regua de COLUNAS do SQL*Plus: grupos de tracos separados por espaco
# (`--------- ---------- ---`). Diferente de uma linha solida de tracos, que o
# dbms_xplan usa como separador - e cuja linha anterior pode ser dado de
# verdade. So a regua de colunas autoriza descartar a linha de cima.
_RE_REGUA_COLUNAS = re.compile(r"^\s*-+(?: +-+)+\s*$")


def _texto_dos_dados(texto: str) -> str:
    """A saida SEM titulo de secao e SEM cabecalho de coluna.

    Usado por quem procura SINTOMA no texto. Nao serve para quem precisa da
    largura das colunas (`_colunas`), que depende do cabecalho e da regua.
    """
    linhas = (texto or "").splitlines()
    fora = set()
    for i, ln in enumerate(linhas):
        if _RE_TITULO_SECAO.match(ln):
            fora.add(i)
        elif _RE_REGUA_COLUNAS.match(ln):
            fora.add(i)
            j = i - 1
            while j >= 0 and not linhas[j].strip():
                j -= 1
            if j >= 0:
                fora.add(j)       # o cabecalho de coluna
    return "\n".join(ln for i, ln in enumerate(linhas) if i not in fora)


def _ler_erros(texto: str, _pergunta: str) -> list:
    """Codigos ORA- na saida, com severidade e titulo do dicionario.

    Le so os DADOS: codigo no titulo da secao ou no nome da coluna e o que o
    check procura, nao o que ele encontrou (ver `_texto_dos_dados`).
    """
    dados = _texto_dos_dados(texto)
    codigos = [c for c in dic_erros.extrair(dados) if not _RE_FALHA.search(c)]
    # Moldura da pilha (`ORA-06512: at line N`, `ORA-02063: preceding ... from
    # LINK`, NO_DATA_FOUND) nao e erro proprio: e o endereco de outro erro. Um
    # incidente produz varias dessas linhas, uma por nivel de chamada, e cada
    # uma virava um achado - diluindo o erro de verdade no meio da moldura. O
    # par canonico e ORA-20000 (o QUE falhou) + ORA-06512 (ONDE).
    principais = [c for c in codigos if not dic_erros.secundario(c)]
    molduras = [c for c in codigos if dic_erros.secundario(c)]

    out = []
    for cod in principais:
        info = dic_erros.explicar(cod)
        titulo = info.get("titulo", "erro Oracle (sem entrada no dicionario)")
        n = len(re.findall(re.escape(cod).replace(r"ORA\-0*", r"ORA-0*"), dados))
        out.append(_ach(f"{cod} - {titulo}",
                        dic_erros.severidade(cod, "warn"),
                        "codigo ORA- encontrado nos dados da saida",
                        f"{max(n, 1)} ocorrencia(s) na saida"))

    # Moldura SOZINHA e informacao util, e de um tipo que so aparece aqui: o
    # que foi colado e o meio da pilha, sem a linha que diz o que falhou. Dizer
    # isso e melhor que ficar calado - o DBA recola com o topo do erro.
    if molduras and not principais:
        out.append(_ach(
            f"a saida traz so a pilha do erro ({', '.join(molduras[:3])}) - "
            f"falta a linha que diz O QUE falhou", "info",
            "codigos de moldura de pilha sem o erro principal na saida",
            leitor="erros_moldura"))
    return out


def _colunas(linhas: list, cab: int) -> dict:
    """Mapa cabecalho -> (inicio, fim) a partir da regua de tracos do SQL*Plus.

    A regua (`---- ------ ---`) e o unico lugar onde a largura das colunas esta
    escrita sem ambiguidade. Sem ela nao ha como saber que uma celula esta
    VAZIA: separar por espaco faz a coluna seguinte escorregar para o lugar da
    que faltou, e a saida continua parecendo bem formada.
    """
    if cab + 1 >= len(linhas):
        return {}
    regua = linhas[cab + 1]
    if set(regua.strip()) - {"-", " "} or "-" not in regua:
        return {}
    cabecalho = linhas[cab]
    col = {}
    for m in re.finditer(r"-+", regua):
        ini, fim = m.start(), m.end()
        nome = cabecalho[ini:fim].strip().lower()
        if not nome:      # coluna cujo titulo transbordou a largura
            nome = cabecalho[max(0, ini - 2):fim + 2].strip().lower()
        if nome:
            col[nome] = (ini, fim)
    return col


def _celula(linha: str, span) -> str:
    ini, fim = span
    return linha[ini:fim].strip() if ini < len(linha) else ""


# Identificador de sessao como o Oracle e este framework o escrevem: `1042` ou
# `1042,55` (sid,serial#). O segundo e a forma do check `top_active_sessions`.
_RE_SID = re.compile(r"\d{1,7}(?:,\d{1,7})?")


def _ler_bloqueio(texto: str, _pergunta: str) -> list:
    """Cadeia de bloqueio: coluna BLOCKING_SESSION preenchida ou espera de TX.

    Duas formas porque as duas aparecem na pratica: quem roda a consulta de
    `gv$session` ve a coluna; quem roda o top de esperas ve o evento.

    A contagem sai da POSICAO da coluna, nao de "tem numero na linha". A
    diferenca decide o plantao: numa cadeia real, a sessao RAIZ aparece com
    `BLOCKING_SESSION` vazio - ela bloqueia, nao e bloqueada. Contando qualquer
    linha com digito, o proprio bloqueador entrava como vitima e o numero saia
    inflado justamente no dado em que alguem age.
    """
    baixo = texto.lower()
    # `blocker` entrou aqui porque e o nome da coluna que o NOSSO PROPRIO
    # coletor grava: o check `blocking_chain` (locks.txt) traz o alias BLOCKER,
    # nao BLOCKING_SESSION. Sem isso, colar o locks.txt do caso na caixa do
    # guiado deixava o leitor MUDO numa cadeia ativa - medido na evidencia real:
    # duas vitimas presas em enq: TX e nenhum achado de bloqueio, perdendo a
    # RAIZ, que e a resposta do passo (matar a vitima nao devolve nada ao
    # ambiente). A hipotese ainda subia pelo nome do evento, por acidente.
    #
    # Seguro em relacao ao locks_hist.txt, cujas colunas sao em portugues
    # (VITIMAS, BLOQUEADORES) e nao casam com nenhuma marca daqui.
    if not any(m in baixo for m in
               ("blocking_session", "blocking session", "blocker")):
        return []
    linhas = _linhas(texto)
    # O TITULO da secao nao pode roubar a deteccao do cabecalho. Com
    # `=== BLOCKING SESSIONS ===` na primeira linha, `cab` apontava para o
    # titulo, `_colunas` procurava a regua na linha seguinte (que e o cabecalho
    # de verdade), nao achava, e o leitor caia na contagem APROXIMADA - sem a
    # raiz da cadeia. A evidencia real escapou por sorte de idioma: os
    # subtitulos do catalogo estao em portugues ("ARVORE DE BLOQUEIO"). O caso
    # DEMO, que todo clone novo analisa, caia direto nisso.
    cab = next((i for i, l in enumerate(linhas)
                if not _RE_TITULO_SECAO.match(l)
                and ("blocking" in l.lower() or "blocker" in l.lower())), None)
    if cab is None:
        return []

    col = _colunas(linhas, cab)
    span_bloq = next((v for k, v in col.items()
                      if "blocking" in k or k == "blocker"), None)
    span_sid = next((v for k, v in col.items() if k == "sid"), None)
    corpo = [l for l in linhas[cab + 1:] if not set(l.strip()) <= {"-", " "}]

    if span_bloq:
        pares = []                       # (sid da vitima, sid do bloqueador)
        for linha in corpo:
            bloq = _celula(linha, span_bloq)
            # `sid` OU `sid,serial#`: as duas formas sao do proprio framework -
            # o check `top_active_sessions` monta a coluna como
            # `s.sid||','||s.serial#`, e o caso DEMO grava exatamente assim.
            # Exigindo so digitos, `123,45` era descartado e o leitor concluia
            # "nenhuma sessao bloqueada" sobre uma cadeia ATIVA. Refutacao
            # falsa e pior que silencio: fecha o caminho certo da investigacao.
            if not _RE_SID.fullmatch(bloq) or bloq.strip("0,") == "":
                continue
            sid = _celula(linha, span_sid) if span_sid else ""
            pares.append((sid, bloq))
        if not pares:
            # A coluna existe, veio com linhas e NENHUMA aponta bloqueador:
            # isso nao e ausencia de dado, e a prova de que ninguem esta
            # travado. Devolver lista vazia jogava fora a evidencia negativa -
            # o passo ficava sem resposta e a hipotese de bloqueio seguia de
            # pe sem nada que a sustentasse.
            if corpo:
                return [_ach("nenhuma sessao bloqueada na verificacao "
                             f"({len(corpo)} sessao(oes) na saida, "
                             "BLOCKING_SESSION vazio)", "info",
                             "coluna BLOCKING_SESSION sem nenhum bloqueador "
                             "na saida colada", leitor="bloqueio")]
            return []
        # Raiz da cadeia: quem bloqueia sem estar bloqueado. E a resposta que o
        # passo persegue - matar a vitima nao devolve nada ao ambiente.
        vitimas = {s for s, _ in pares if s}
        raizes = sorted({b for _, b in pares if b not in vitimas})
        titulo = (f"{len(pares)} sessao(oes) bloqueada(s) - cadeia de bloqueio "
                  f"ativa")
        if len(raizes) == 1:
            titulo += f", raiz = SID {raizes[0]}"
        elif len(raizes) > 1:
            titulo += f", {len(raizes)} raizes: SID " + ", ".join(raizes[:4])
        return [_ach(titulo, "crit",
                     "coluna BLOCKING_SESSION preenchida na saida",
                     f"{len(pares)} sessao(oes) esperando")]

    # Sem regua de tracos (saida reformatada, colada de planilha ou de um
    # cliente grafico) nao da para afirmar qual celula esta vazia. Aqui vale a
    # heuristica antiga - mas o texto NAO promete contagem exata, para o numero
    # nao virar base de decisao sem ter como estar certo.
    bloqueadas = [l for l in corpo if re.search(r"\b\d{1,7}\b", l)]
    if not bloqueadas:
        return []
    return [_ach(f"cadeia de bloqueio ativa em {len(bloqueadas)} linha(s) da "
                 f"saida (sem a regua de colunas, a contagem e aproximada)",
                 "crit", "coluna BLOCKING_SESSION presente na saida colada",
                 f"{len(bloqueadas)} linha(s) com sessao")]


_RE_SO_NUMERO = re.compile(r"\d{1,3}(?:[.,]\d+)?")


def _pct_da_coluna(texto: str) -> dict:
    """Percentual por linha, lido da COLUNA de %, e nao do sinal na linha.

    O top de esperas - o dado que o Method R manda olhar primeiro - quase nunca
    traz '%' na linha: o sinal esta no cabecalho (`% DB time`, `PCT_DB_TIME`) e
    a celula e um numero solto. Procurando so por '61.2%', o leitor via os
    eventos e perdia justamente o peso de cada um, que e o que separa 'aparece'
    de 'domina'.
    """
    linhas = _linhas(texto)
    for i, linha in enumerate(linhas):
        if "event" not in linha.lower():
            continue
        col = _colunas(linhas, i)
        span = next((v for k, v in col.items()
                     if "%" in k or "pct" in k or "perc" in k), None)
        if not span:
            continue
        fora = {}
        for dado in linhas[i + 2:]:
            cel = _celula(dado, span)
            if _RE_SO_NUMERO.fullmatch(cel):
                fora[dado] = float(cel.replace(",", "."))
        if fora:
            return fora
    return {}


def _ler_eventos(texto: str, _pergunta: str) -> list:
    """Eventos de espera reconhecidos, com o percentual ou o tempo da linha.

    O numero e o que separa 'aparece' de 'domina' - e o motor pontua mais alto
    quando o proprio achado carrega um percentual relevante.
    """
    out = []
    vistos = set()
    por_coluna = _pct_da_coluna(texto)
    # O pico que define "a maior da tabela" ignora as esperas idle: se o topo
    # da lista for uma sessao ociosa, o titulo estaria coroando o nada.
    idle_baixo = tuple(r.lower() for r in EVENTOS_IDLE)
    pico_geral = max((v for l, v in por_coluna.items()
                      if not any(i in l.lower() for i in idle_baixo)),
                     default=0.0)
    for linha in _linhas(texto):
        baixo = linha.lower()
        # Da linha, o nome MAIS ESPECIFICO que ela contem. A lista tem o geral
        # e o particular ('enq: tx' e 'enq: tx - row lock contention'), e
        # parar no primeiro que casasse fazia a segunda linha do mesmo evento
        # cair no rotulo curto: um unico enfileiramento virava dois achados,
        # com dois nomes, como se fossem duas contencoes diferentes.
        cand = sorted((e for e in EVENTOS if e[0] in baixo),
                      key=lambda e: -len(e[0]))
        if not cand:
            continue
        rotulo = cand[0][1]
        if rotulo in vistos:
            continue
        vistos.add(rotulo)
        if rotulo in EVENTOS_IDLE:
            out.append(_ach(
                f"espera '{rotulo}' presente - evento IDLE, nao e contencao",
                "info",
                "espera da classe idle: a sessao aguarda a aplicacao, nao o "
                "banco - o proprio AWR a exclui do DB time"))
            continue
        pcts = [float(p.replace(",", ".")) for p in _RE_PCT.findall(linha)]
        if not pcts and linha in por_coluna:
            pcts = [por_coluna[linha]]
        segs = [int(s) for s in _RE_SEG.findall(linha)]
        if pcts:
            pico = max(pcts)
            # Method R: o que decide nao e a lista de esperas, e qual delas
            # carrega o DB time. Dizer qual e a maior poupa o passo de reler a
            # tabela para achar o topo.
            topo = (pico_geral and pico >= pico_geral
                    and len(por_coluna) > 1)
            out.append(_ach(f"espera '{rotulo}' concentra {pico:g}% na "
                            f"saida observada" +
                            (" - e a maior da tabela" if topo else ""),
                            "crit" if pico >= 30 else "warn",
                            "evento com percentual na linha colada",
                            f"{pico:g}% do tempo"))
        elif segs:
            pico = max(segs)
            out.append(_ach(f"espera '{rotulo}' com {pico}s acumulados",
                            "crit" if pico >= 600 else "warn",
                            "evento com tempo na linha colada",
                            f"{pico}s de espera"))
        else:
            out.append(_ach(f"espera '{rotulo}' presente na saida observada",
                            "warn", "evento reconhecido na saida colada"))
    return out


# Parametro -> (valor que importa, severidade, frase que o motor entende).
# A frase repete o vocabulario do catalogo de hipoteses de proposito: e o que
# faz um `log_mode NOARCHIVELOG` colado REFUTAR 'FRA sem espaco' na hora.
PARAMETROS = (
    ("log_mode", "noarchivelog", "crit",
     "log_mode = NOARCHIVELOG na saida observada"),
    ("database_role", "physical standby", "info",
     "banco e PHYSICAL STANDBY na saida observada"),
    ("open_mode", "read only", "info",
     "banco aberto em READ ONLY na saida observada"),
    ("open_mode", "mounted", "crit",
     "banco em MOUNTED (nao aberto) na saida observada"),
    ("restricted", "yes", "crit",
     "banco/PDB em modo RESTRICTED na saida observada"),
)


# Quantas linhas abaixo do cabecalho ainda contam como "o valor desta coluna".
# O SQL*Plus imprime `LOG_MODE` / `------------` / `NOARCHIVELOG`: exigir nome e
# valor na MESMA linha reprovaria o formato mais comum de todos. Passar de
# poucas linhas, por outro lado, faria um valor de outra consulta ser lido como
# se fosse deste parametro.
_JANELA_TABELA = 3


def _ler_parametros(texto: str, _pergunta: str) -> list:
    """Valor de parametro/estado, nas duas formas em que o SQL*Plus imprime.

    1. `NAME  VALUE` na mesma linha (saida de `v$parameter` com colunas);
    2. cabecalho numa linha e valor logo abaixo (tabela de coluna unica).
    """
    linhas = _linhas(texto)
    out = []
    for nome, valor, sev, frase in PARAMETROS:
        achou = ""
        for i, linha in enumerate(linhas):
            if nome not in linha.lower():
                continue
            # forma 1: nome e valor lado a lado
            if valor in linha.lower():
                achou = "mesma linha"
                break
            # forma 2: cabecalho, separador de hifens e valor logo abaixo
            for prox in linhas[i + 1:i + 1 + _JANELA_TABELA]:
                if set(prox.strip()) <= {"-", " "}:
                    continue          # linha separadora do SQL*Plus
                if valor in prox.lower():
                    achou = "coluna abaixo do cabecalho"
                    break
                # Valor diferente logo abaixo do cabecalho ja responde a
                # pergunta: ARCHIVELOG aqui significa que NAO e NOARCHIVELOG.
                break
            if achou:
                break
        if achou:
            out.append(_ach(frase, sev,
                            f"'{nome}' com valor '{valor}' na saida "
                            f"({achou})"))
    return out


# ── espaco (tablespace, FRA, diskgroup, filesystem) ───────────
# A saida mais colada do plantao e uma tabela de ocupacao. Sem este leitor ela
# caia no "nao reconheci nada": o DBA via 98% com os proprios olhos e o motor
# continuava com a foto da coleta.
#
# Duas formas, porque as duas chegam: percentual com '%' (df -h, relatorios
# prontos) e coluna PCT_USED sem sinal nenhum, que e como o SQL*Plus imprime.
# 'usable' saiu da lista e virou 'usable_file'/'usable_mb': a marca curta casava
# dentro de "UNUSABLEBEGINNING#" e "UNUSABLEBEFORE#", nomes de coluna que o
# dbms_xplan imprime no texto dos PREDICADOS. E a mesma armadilha do `temp`
# dentro de "tempo", agora em ingles: marca curta dentro de palavra maior.
# O que ela quer pegar de verdade e a coluna USABLE_FILE_MB do v$asm_diskgroup.
_MARCAS_ESPACO = ("tablespace", "diskgroup", "recovery area", "flash recovery",
                  "recovery_file_dest", "v$asm", "filesystem", "mounted on",
                  "recovery_area", "percent_space_used", "pct_used",
                  "used_pct", "pct used", "%used", "usable_file", "usable_mb",
                  "free_mb", "free_gb")

# ── saida de PLANO nao e tabela de dados ──────────────────────
# `dbms_xplan` e o SQL Monitoring Report sao DESENHO, nao dado tabular: a
# moldura usa '|', o cabecalho tem '%' embutido (`Cost (%CPU)`) e o texto dos
# predicados carrega nomes de coluna e literais SQL que casam com a marca de
# entrada de praticamente qualquer leitor.
#
# Medido nos 23 servidores da bateria: `planos.txt` produzia
# "tablespace |*N | HASH em N% de uso - sem espaco para crescer" em CRITICO -
# um alarme de espaco, hipotese de classe PARADA, extraido de um plano de
# execucao. E o inverso, que e pior: em outro servidor saiu
# "espaco dentro do esperado na verificacao (maior uso:
# NVL(\"UNUSABLEBEGINNING#\",0)=0 THEN em 1%)", e essa frase e a marca de
# REFUTACAO da hipotese de espaco. Um dump de plano podia DESCARTAR um
# problema real de tablespace cheia.
#
# Quem le plano de proposito e o leitor `xplan`, que continua intacto.
_RE_SAIDA_DE_PLANO = re.compile(
    r"^\s*\|\s*Id\s*\|\s*Operation|Plan hash value|PLAN_TABLE_OUTPUT|"
    r"Predicate Information|SQL Monitoring Report|"
    r"^\s*-+\s*$\n^\s*\|\s*Id", re.I | re.M)


def _e_saida_de_plano(texto: str) -> bool:
    return bool(_RE_SAIDA_DE_PLANO.search(texto or ""))

_RE_COL_PCT = re.compile(r"(pct|percent|%)", re.I)

# 80% e onde o acompanhamento comeca; 90% e onde a transacao corre risco de
# cair. Abaixo de 80 a tabela inteira vira REFUTACAO - e esse o valor escondido
# aqui: um print de espaco saudavel fecha um caminho da investigacao.
ESPACO_WARN = 80.0
ESPACO_CRIT = 90.0


def _linhas_pct(texto: str) -> list:
    """(nome, percentual, linha) das linhas de dado com ocupacao."""
    out, idx, ncols = [], None, 0
    for linha in _linhas(texto):
        if set(linha.strip()) <= {"-", " "}:
            continue
        toks = linha.split()
        # Cabecalho: guarda em que coluna esta o percentual, para ler as linhas
        # de baixo. Sem numero na linha, senao um dado viraria cabecalho.
        if not re.search(r"\d", linha):
            cols = [i for i, t in enumerate(toks) if _RE_COL_PCT.search(t)]
            if cols:
                idx, ncols = cols[-1], len(toks)
            continue
        pcts = _RE_PCT.findall(linha)
        if pcts:
            out.append((toks[0], max(float(p.replace(",", "."))
                                     for p in pcts), linha))
            continue
        if idx is None:
            continue
        # A coluna e localizada de TRAS para a frente: nome com espaco
        # ('ARCHIVED LOG') muda a contagem de tokens da linha, mas nao a
        # distancia entre o valor e o fim.
        pos = len(toks) - (ncols - idx)
        if pos <= 0 or pos >= len(toks):
            continue
        try:
            out.append((_nome_linha(toks), float(toks[pos].replace(",", ".")),
                        linha))
        except ValueError:
            pass
    return out


# Palavras de ESTADO que aparecem entre o nome e os numeros (tipico do
# `v$asm_diskgroup`: NAME, STATE, TYPE, depois os MB). Sem cortar aqui, o titulo
# saia como "diskgroup DG_DATA01 CONNECTED EXTERN em 91% de uso" - o nome do
# recurso misturado com o estado dele, que e o tipo de detalhe que faz o DBA
# desconfiar da leitura inteira.
_ESTADOS_NA_LINHA = {
    "connected", "mounted", "dismounted", "broken", "unknown",
    "extern", "normal", "high", "flex", "extended",
    "online", "offline", "available", "unavailable",
    "yes", "no", "permit", "deny", "read", "write", "only",
}


def _nome_linha(toks: list) -> str:
    """O que a linha esta medindo: os tokens antes do primeiro numero."""
    nome = []
    for t in toks:
        if re.fullmatch(r"[\d.,]+[a-zA-Z%]?", t):
            break
        if t.lower() in _ESTADOS_NA_LINHA:
            break
        nome.append(t)
    return " ".join(nome[:3]) or (toks[0] if toks else "?")


def _recurso(linha: str, texto: str) -> str:
    """De que recurso e esta linha - e a palavra tem de ser a do catalogo.

    'FRA em 97%' casa com a hipotese de archive; 'tablespace X em 97%' casa com
    a de espaco. Errar o substantivo aqui e pontuar a hipotese errada.
    """
    for alvo, marcas in (
            ("diskgroup", ("diskgroup", "asm")),
            ("FRA", ("recovery area", "recovery_area", "flash recovery",
                     "fra", "archived log", "archivelog")),
            ("filesystem", ("/dev/", "mounted on", "filesystem"))):
        if any(m in linha.lower() for m in marcas):
            return alvo
    baixo = texto.lower()
    for alvo, marcas in (
            ("diskgroup", ("diskgroup", "v$asm")),
            ("FRA", ("recovery area", "recovery_area", "flash recovery",
                     "recovery_file_dest", "percent_space_used")),
            ("filesystem", ("mounted on", "filesystem"))):
        if any(m in baixo for m in marcas):
            return alvo
    return "tablespace"


def _ler_espaco(texto: str, _pergunta: str) -> list:
    """Ocupacao de espaco na saida colada - inclusive quando esta tudo bem."""
    baixo = texto.lower()
    if not any(m in baixo for m in _MARCAS_ESPACO):
        return []
    if _e_saida_de_plano(texto):
        return []      # `Cost (%CPU)` nao e ocupacao - ver _RE_SAIDA_DE_PLANO
    # Linha de evento de espera tambem tem percentual: quem le aquilo e o
    # leitor de eventos, e ler duas vezes contaria a mesma evidencia em dobro.
    dados = [(n, p, l) for n, p, l in _linhas_pct(texto)
             if 0 <= p <= 100
             and not any(marca in l.lower() for marca, _ in EVENTOS)]
    if not dados:
        return []

    altos = [d for d in dados if d[1] >= ESPACO_WARN]
    if not altos:
        nome, pct, linha = max(dados, key=lambda d: d[1])
        rec = _recurso(linha, texto)
        frase = ("archive/FRA sem pendencia na verificacao" if rec == "FRA"
                 else "espaco dentro do esperado na verificacao")
        return [_ach(f"{frase} (maior uso: {nome} em {pct:g}%)", "info",
                     f"nenhum recurso acima de {ESPACO_WARN:g}% na saida "
                     f"colada", leitor="espaco")]

    out = []
    for nome, pct, linha in sorted(altos, key=lambda d: -d[1])[:5]:
        rec = _recurso(linha, texto)
        crit = pct >= ESPACO_CRIT
        titulo = (f"FRA em {pct:g}% de uso" if rec == "FRA"
                  else f"{rec} {nome} em {pct:g}% de uso")
        if crit and rec != "FRA":
            titulo += " - sem espaco para crescer"
        out.append(_ach(titulo, "crit" if crit else "warn",
                        "percentual de ocupacao lido na saida colada",
                        f"{pct:g}% ocupado", leitor="espaco"))
    return out


# ── Data Guard: apply / transport lag ─────────────────────────
# `v$dataguard_stats` devolve INTERVALO, nao numero ('+00 02:15:33'). Sem um
# leitor proprio isso nao casava com nada, e o atraso do standby - risco de
# PERDA, nao de lentidao - ficava so na tela de quem conduzia.
_RE_LAG = re.compile(
    r"(apply|transport)\s+lag[^\d+]*\+?\s*(?:(\d+)\s+)?"
    r"(\d{1,3}):(\d{2}):(\d{2})", re.I)

# Abaixo de 5 minutos o standby esta acompanhando: e refutacao, nao alarme.
# Meia hora de atraso ja e RPO perdido num failover.
LAG_WARN = 300
LAG_CRIT = 1800


def _humano(seg: int) -> str:
    h, resto = divmod(seg, 3600)
    m, s = divmod(resto, 60)
    if h:
        return f"{h}h{m:02d}"
    return f"{m}min{s:02d}" if m else f"{s}s"


def _ler_lag(texto: str, _pergunta: str) -> list:
    """Atraso do standby, do jeito que o Data Guard imprime (intervalo)."""
    out, vistos = [], set()
    for m in _RE_LAG.finditer(texto):
        tipo = m.group(1).lower()
        if tipo in vistos:
            continue
        vistos.add(tipo)
        dias = int(m.group(2) or 0)
        seg = (dias * 86400 + int(m.group(3)) * 3600 +
               int(m.group(4)) * 60 + int(m.group(5)))
        if seg < LAG_WARN:
            # Standby acompanhando: a hipotese de gap CAI, e dizer isso vale
            # mais do que registrar "lag pequeno" como se fosse achado.
            out.append(_ach(f"standby sem gap na verificacao "
                            f"({tipo} lag de {_humano(seg)})", "info",
                            "apply/transport lag abaixo de "
                            f"{_humano(LAG_WARN)} na saida colada",
                            leitor="lag"))
            continue
        out.append(_ach(f"standby com {tipo} lag de {_humano(seg)}",
                        "crit" if seg >= LAG_CRIT else "warn",
                        "intervalo de lag lido na saida de v$dataguard_stats",
                        f"{seg}s de atraso", leitor="lag"))
    # Um lag alto e um lag zerado na mesma saida (apply parado, transport em
    # dia) nao podem sustentar e refutar ao mesmo tempo: o atraso manda.
    if any(a["severity"] != "info" for a in out):
        out = [a for a in out if a["severity"] != "info"]
    return out


# ── MRP: o processo de apply do standby ───────────────────────
# Lag baixo nao garante apply vivo: o MRP pode ter morrido agora, e o lag so
# cresce depois. Quem cola `v$managed_standby` esta perguntando exatamente
# isso - e a AUSENCIA da linha do MRP e a resposta.
_RE_MRP = re.compile(r"\bmrp\d*\b", re.I)


def _ler_mrp(texto: str, _pergunta: str) -> list:
    """Apply do standby rodando ou nao, a partir de `v$managed_standby`.

    So conclui quando a saida DIZ que e essa a consulta: procurar 'MRP' em
    qualquer texto colado transformaria silencio em diagnostico.
    """
    baixo = texto.lower()
    if "managed_standby" not in baixo:
        return []
    if not _RE_MRP.search(baixo):
        return [_ach("MRP nao esta rodando no standby - apply parado", "crit",
                     "v$managed_standby sem linha de MRP na saida colada",
                     leitor="mrp")]
    # MRP presente e' o esperado: registra como leitura, sem pontuar hipotese.
    estado = "aplicando" if "applying_log" in baixo else "presente"
    return [_ach(f"MRP {estado} no standby", "info",
                 "linha de MRP encontrada em v$managed_standby",
                 leitor="mrp")]


# ── swap do host ──────────────────────────────────────────────
# `free -m` e `swapon -s` sao colados direto do shell. Host em swap deixa tudo
# lento por igual - e o sintoma que faz procurar SQL culpado onde o problema e
# memoria do sistema operacional.
_RE_SWAP = re.compile(r"^\s*swap:?\s+(\d+)\s+(\d+)\s+(\d+)", re.I | re.M)

# Abaixo de 10% e swap de rodape (paginacao antiga que ninguem trouxe de volta);
# metade do swap em uso e pressao real de memoria.
SWAP_WARN = 10.0
SWAP_CRIT = 50.0


def _ler_swap(texto: str, _pergunta: str) -> list:
    """Uso de swap no host, do `free -m` colado."""
    m = _RE_SWAP.search(texto)
    if not m:
        return []
    total, usado = int(m.group(1)), int(m.group(2))
    if total <= 0:
        return []
    pct = 100.0 * usado / total
    if pct < SWAP_WARN:
        # Swap parado NAO descarta falta de memoria (ORA-04031 acontece com
        # swap zerado): vale como leitura, nao como refutacao.
        return [_ach(f"host praticamente sem swap em uso ({pct:.0f}%)", "info",
                     "linha 'Swap:' do free -m na saida colada",
                     leitor="swap")]
    return [_ach(f"host em swap: {usado}MB de {total}MB ({pct:.0f}%)",
                 "crit" if pct >= SWAP_CRIT else "warn",
                 "linha 'Swap:' do free -m na saida colada",
                 f"{pct:.0f}% do swap em uso", leitor="swap")]


# ── estatisticas: idade do LAST_ANALYZED ──────────────────────
# A data vem escrita como o NLS do ambiente manda, e nao ha uma so forma. O que
# importa nao e a data em si: e a IDADE dela. Estatistica de seis meses explica
# plano ruim melhor do que qualquer suspeita de bug.
_MESES = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_MESES.update({m: i for i, m in enumerate(
    ("jan", "fev", "mar", "abr", "mai", "jun",
     "jul", "ago", "set", "out", "nov", "dez"), 1)})

_RE_DATA = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})"                      # 2026-08-12
    r"|(\d{2})/(\d{2})/(\d{2,4})"                   # 12/08/2026
    r"|(\d{1,2})-([a-z]{3})-(\d{2,4})", re.I)       # 12-AUG-26

# 30 dias e o limite de "ninguem analisou desde a ultima carga"; 90 dias e
# estatistica de outro banco, praticamente.
STATS_WARN = 30
STATS_CRIT = 90


def _data_do_token(m: re.Match) -> date | None:
    """A data que o match encontrou, em qualquer das tres formas."""
    g = m.groups()
    try:
        if g[0]:
            return date(int(g[0]), int(g[1]), int(g[2]))
        if g[3]:
            ano = int(g[5])
            return date(ano + 2000 if ano < 100 else ano, int(g[4]), int(g[3]))
        if g[6]:
            mes = _MESES.get(g[7].lower())
            ano = int(g[8])
            if not mes:
                return None
            return date(ano + 2000 if ano < 100 else ano, mes, int(g[6]))
    except ValueError:
        return None      # 31/02: data impossivel nao vira idade inventada
    return None


# Coluna em que a PROPRIA consulta ja entregou a idade, calculada contra o
# SYSDATE do banco. Quando ela existe, e a fonte melhor - ver `_idade_da_coluna`.
_COLS_DIAS = ("dias_sem_stats", "dias", "idade_dias", "days_since",
              "dias_sem_coleta")
# Nome do objeto: owner e tabela ficam em colunas separadas na saida do SQL
# Health Check, e so as duas juntas identificam o alvo.
_COLS_OWNER = ("owner", "schema", "proprietario")
_COLS_TABELA = ("tabela", "table_name", "objeto", "segment_name")


def _idade_da_coluna(texto: str) -> tuple:
    """(dias, alvo) lidos das COLUNAS da saida, ou (None, '').

    Por que preferir a coluna a reparsear a data: a consulta calculou a idade
    contra o SYSDATE do BANCO, no momento da coleta. Reparsear `LAST_ANALYZED`
    e compará-la com o relogio de quem esta lendo produz dois erros que se
    somam - o fuso da estacao e, sobretudo, o TEMPO QUE PASSOU desde a coleta.
    Medido na evidencia real de APPHO600: a coluna `DIAS_SEM_STATS` dizia 11
    dias e o leitor anunciava 39, porque a evidencia foi reprocessada 28 dias
    depois. Isso importa exatamente no caso que este framework se propoe a
    fazer - reanalisar o caso ja gravado e comparar com a analise anterior: a
    mesma evidencia mudava de veredito com o passar do calendario.
    """
    linhas = _linhas(texto)
    for i in range(len(linhas)):
        col = _colunas(linhas, i)
        if not col:
            continue
        cd = next((col[c] for c in _COLS_DIAS if c in col), None)
        if not cd:
            continue
        co = next((col[c] for c in _COLS_OWNER if c in col), None)
        ct = next((col[c] for c in _COLS_TABELA if c in col), None)
        pior = None
        for dado in linhas[i + 2:]:
            if _RE_TITULO_SECAO.match(dado) or _RE_REGUA_COLUNAS.match(dado):
                break
            bruto = _celula(dado, cd)
            if not bruto or not re.fullmatch(r"\d{1,6}", bruto):
                continue
            dias = int(bruto)
            nome = ".".join(x for x in (_celula(dado, co) if co else "",
                                        _celula(dado, ct) if ct else "") if x)
            if pior is None or dias > pior[0]:
                pior = (dias, nome)
        if pior:
            return pior
    return (None, "")


def _ler_estatisticas(texto: str, _pergunta: str) -> list:
    """Idade da ultima coleta de estatisticas, quando a saida e sobre isso.

    Exige a marca da consulta (`last_analyzed` / `num_rows` / `dbms_stats`):
    qualquer data solta num texto colado nao e data de estatistica.
    """
    baixo = texto.lower()
    if not any(m in baixo for m in
               ("last_analyzed", "num_rows", "dbms_stats", "stale_stats")):
        return []
    if _e_saida_de_plano(texto):
        # O SQL Monitoring Report traz 'Rows' e datas de execucao, e o leitor
        # anunciava "estatistica de N dias em Execution" - lendo o rotulo
        # 'Execution Started' como nome de tabela.
        return []

    # 1) a idade que o BANCO calculou, se a saida a trouxer
    dias, alvo = _idade_da_coluna(texto)
    if dias is None:
        # 2) so entao reparsear a data, comparando com hoje. E o caminho de
        #    quem colou um `SELECT last_analyzed` sem coluna de idade.
        hoje = data_de_referencia()
        pior = None
        for linha in _linhas(texto):
            for m in _RE_DATA.finditer(linha):
                d = _data_do_token(m)
                if not d:
                    continue
                d_dias = (hoje - d).days
                if d_dias < 0:
                    continue     # data no futuro: nao ha idade a inventar
                if pior is None or d_dias > pior[0]:
                    pior = (d_dias, linha.split()[0])
        if pior is None:
            return []
        dias, alvo = pior
    alvo = alvo or "objeto nao identificado na saida"
    if dias < STATS_WARN:
        # Estatistica fresca DESCARTA a hipotese - e o que evita mandar rodar
        # DBMS_STATS num banco que acabou de coletar.
        return [_ach(f"estatisticas recentes na verificacao ({dias} dia(s))",
                     "info", "last_analyzed recente na saida colada",
                     leitor="estatisticas")]
    return [_ach(f"estatistica de {dias} dias em {alvo}",
                 "crit" if dias >= STATS_CRIT else "warn",
                 "idade do last_analyzed lido na saida colada",
                 f"{dias} dias sem coleta", leitor="estatisticas")]


# ── instancias do cluster ─────────────────────────────────────
# `gv$instance` responde a pergunta que ninguem faz cedo: TODAS as instancias
# estao abertas? Com um no fora, a carga cai no que sobrou e o banco "fica
# lento" sem nada ter mudado na aplicacao.
_ESTADOS_INST = ("open", "mounted", "started", "nomount", "down")


def _ler_instancias(texto: str, _pergunta: str) -> list:
    """Estado de cada instancia, a partir de `gv$instance`."""
    baixo = texto.lower()
    if "gv$instance" not in baixo and "inst_id" not in baixo:
        return []
    fora, abertas = [], 0
    for linha in _linhas(texto):
        toks = linha.split()
        estado = next((t.lower() for t in toks
                       if t.lower() in _ESTADOS_INST), "")
        if not estado:
            continue
        if "status" in linha.lower():
            continue          # cabecalho, nao linha de dado
        if estado == "open":
            abertas += 1
        else:
            fora.append((toks[0], estado.upper()))
    if fora:
        nomes = ", ".join(f"{n} ({e})" for n, e in fora[:4])
        return [_ach(f"instancia fora do cluster: {nomes}", "crit",
                     "gv$instance com instancia em estado diferente de OPEN",
                     f"{len(fora)} instancia(s) fora", leitor="instancias")]
    if abertas >= 2:
        return [_ach(f"instancias todas abertas na verificacao "
                     f"({abertas} no ar)", "info",
                     "gv$instance com todas as instancias em OPEN",
                     leitor="instancias")]
    return []


# ══ Pacote de tuning ═══════════════════════════════════════
# A pergunta que decide o rumo do tuning e uma so: o banco ficou mais LENTO ou
# passou a fazer MAIS? Responder errado custa o plantao - tuna-se um SQL que nao
# mudou nada enquanto a origem da carga segue crescendo. Os leitores abaixo
# extraem exatamente isso da saida colada.

# ── serie por hora (carga) ──────────────────────────────────
# `GROUP BY TO_CHAR(hora)` e como todo mundo mede aumento de carga, e a saida e
# sempre a mesma: rotulo de hora + contagem. Um numero solto ('350 sessoes')
# nao diz nada; a SERIE diz, porque traz o proprio comparativo.
_MARCAS_CARGA = ("sess", "logon", "execu", "hora", "hh24", "snap", "carga",
                 "conex", "connect")

# Nome da coluna que carrega a HORA. A bateria contra 23 bancos reais mostrou
# por que isso tem de vir do cabecalho: `parse.txt` comeca com `INST_ID` = 1, e
# o leitor lia esse 1 como "hora 1", transformando estatistica acumulada desde o
# startup em "aumento de carga: pico de 27 milhoes de execucoes". Falso positivo
# que SUSTENTAVA uma hipotese - o pior tipo de defeito que este modulo pode ter.
_COLS_HORA = ("hora", "hh24", "hh", "hour", "sample_time", "snap_time",
              "periodo", "data_hora", "dia_hora", "minuto")

# Rotulo que caracteriza uma linha da serie: hora cheia, hora:minuto ou
# data+hora. Sem exigir isso, qualquer tabela de duas colunas viraria "carga".
_RE_ROTULO_HORA = re.compile(
    r"^(?:\d{4}-\d{2}-\d{2}[ t]?|\d{2}/\d{2}(?:/\d{2,4})?[ t]?)?"
    r"\d{1,2}(?::\d{2})?(?:h|:00)?$", re.I)

# Menos de 3 pontos nao e serie: e anedota. E preciso ter com o que comparar.
CARGA_MIN_PONTOS = 3

# Piso absoluto para concluir. "Pico de 8 contra mediana de 2" e aritmetica, nao
# mudanca de patamar: numa serie de numeros pequenos qualquer oscilacao vira 4x.
CARGA_MIN_PICO = 10
# O dobro da mediana e o limite classico de "mudou de patamar"; o triplo ja e
# outro sistema rodando em cima do mesmo banco.
CARGA_FATOR_WARN = 2.0
CARGA_FATOR_CRIT = 3.0


# SQL*Plus e o AWR entregam contagem grande com separador de milhar por padrao
# (`1,234,567`). Exigindo so digitos, a linha inteira era descartada em
# silencio - e a serie ficava com os pontos PEQUENOS, justamente os que nao
# tem pico. O resultado era "carga estavel" numa saida que mostrava o dobro.
#
# So a virgula seguida de exatamente tres digitos conta como milhar. '98,7' e
# decimal em pt-BR e continua fora: contagem por hora nao tem fracao, e chutar
# aqui trocaria um dado perdido por um dado errado.
_RE_MILHAR = re.compile(r"^\d{1,3}(?:,\d{3})+$")
# A mesma formatacao com casas decimais - `3,450.7` e o que o AWR devolve numa
# coluna de segundos. Aqui a virgula NAO tem como ser decimal: o ponto ja e o
# separador decimal do proprio token.
_RE_MILHAR_DEC = re.compile(r"^\d{1,3}(?:,\d{3})+\.\d+$")


def _inteiro(tok: str):
    """Contagem inteira do token, aceitando o separador de milhar do SQL*Plus."""
    if re.fullmatch(r"\d{1,12}", tok):
        return int(tok)
    if _RE_MILHAR.fullmatch(tok):
        return int(tok.replace(",", ""))
    return None


def _mediana(vals: list) -> float:
    ordenado = sorted(vals)
    n = len(ordenado)
    meio = n // 2
    if n % 2:
        return float(ordenado[meio])
    return (ordenado[meio - 1] + ordenado[meio]) / 2.0


def _serie_horaria(texto: str) -> tuple:
    """((rotulo, valor), ...) da serie por hora, e o nome da coluna medida.

    A coluna da hora e localizada pelo CABECALHO, nunca por "o primeiro token
    parece um numero pequeno": `INST_ID`, `THREAD#` e `SNAP_ID` tambem parecem.
    """
    serie, unidade = [], ""
    i_hora = ncols = None
    for linha in _linhas(texto):
        if set(linha.strip()) <= {"-", " "}:
            continue
        toks = linha.split()
        if len(toks) < 2:
            continue
        if not re.search(r"\d", linha):
            achou = next((i for i, t in enumerate(toks)
                          if t.lower().strip("#") in _COLS_HORA), None)
            if achou is not None:
                i_hora, ncols = achou, len(toks)
                # A unidade sai do nome da ULTIMA coluna: dizer "sessoes" para
                # uma serie de switches de redo seria descrever outra medicao.
                unidade = toks[-1].lower().strip("#()")
            continue
        if i_hora is None:
            continue
        # Quantas colunas vem DEPOIS da hora e fixo pelo cabecalho; com isso o
        # rotulo pode ocupar mais de um token ('2026-08-11 07') sem desalinhar.
        depois = ncols - 1 - i_hora
        fim = len(toks) - depois
        if fim <= i_hora:
            continue
        rotulo = " ".join(toks[i_hora:fim])
        if not _RE_ROTULO_HORA.match(rotulo):
            continue
        # O valor e o ultimo numero inteiro da linha: a contagem costuma ser a
        # ultima coluna, e datas na frente nao podem ser lidas como volume.
        nums = [n for n in (_inteiro(t) for t in toks[fim:]) if n is not None]
        if not nums:
            continue
        serie.append((rotulo, nums[-1]))
    return serie, unidade


# Unidade de VOLUME DE DADO. Uma serie nessas unidades mede quanto o banco
# gerou/leu, nao quantas vezes foi chamado - e a hipotese de carga fala de
# NUMERO DE CHAMADAS ("mais execucoes", "pico de sessoes").
#
# Medido no `archive_rate.txt` real de APPHO600: a serie de MB de archive por
# hora (pico 419 MB as 03h contra mediana 31,5) saia como "aumento de carga:
# 13.3x a carga tipica", CRITICO. Sao duas coisas erradas de uma vez: 13x de
# archive as 3 da manha e a assinatura de um batch/backup noturno, e CRITICO em
# comportamento rotineiro gasta a credibilidade de todo CRITICO do relatorio.
#
# O outro lado e mais sutil e igualmente errado: a serie ESTAVEL de MB emitia
# "carga estavel", frase que REFUTA aumento de carga. Mas carga de LEITURA
# cresce sem gerar um byte de redo - refutar por ai descarta a hipotese certa
# com a medicao errada. Por isso o volume nao vota em nenhuma direcao.
_UNIDADES_VOLUME = ("mb", "gb", "kb", "tb", "mbytes", "gbytes", "bytes",
                    "byte", "blocks", "blocos", "size", "tamanho")

# Switch de redo tambem NAO e "mais chamadas": e quantas vezes o redo log
# encheu, funcao do volume de redo e do TAMANHO do grupo. Pico alto quer dizer
# redo pequeno para a carga - conserto de dimensionamento, nao de capacidade.
#
# Medido na frota: dois servidores saiam com "aumento de carga: pico de 21
# switches por hora", CRITICO - mandando o DBA olhar aplicacao e capacidade
# quando a acao e aumentar o grupo de redo. Pior, o proprio framework ja
# discordava de si mesmo: `analyzers/saude.py` le o MESMO redo.txt com o limiar
# declarado no catalogo (switches_per_hour: warn 12, crit 30, fonte no
# Administrator's Guide) e classificaria 21 como ATENCAO, nao critico.
#
# Entao aqui o leitor para de inventar veredito e passa a usar a MESMA fonte -
# e a nomear o achado com "switch de redo", que e a marca da hipotese de
# redo/commit no catalogo de raciocinio, em vez da marca de carga.
_UNIDADES_SWITCH = ("switches", "switch", "trocas")


def _limiar_switch() -> dict:
    """warn/crit de switches por hora, do catalogo (fonte unica com o analisador)."""
    try:
        from .analyzers.saude import limiar
        return limiar("redo.txt", "switches_per_hour")
    except Exception:
        # Mesmo fallback declarado em analyzers/saude.py PADROES.
        return {"warn": 12.0, "crit": 30.0}


def _ler_carga(texto: str, _pergunta: str) -> list:
    """Serie de sessoes/execucoes por hora: mudou de patamar ou nao.

    A resposta muda a ACAO. Carga que dobrou nao se resolve tunando SQL; carga
    estavel com o banco pesado aponta para custo (plano), nao para volume.
    """
    baixo = texto.lower()
    if not any(m in baixo for m in _MARCAS_CARGA):
        return []
    serie, medida = _serie_horaria(texto)
    if len(serie) < CARGA_MIN_PONTOS:
        return []
    valores = [v for _, v in serie]
    med = _mediana(valores)
    if med <= 0:
        return []
    rotulo, pico = max(serie, key=lambda x: x[1])
    if pico < CARGA_MIN_PICO:
        # Serie pequena demais para dizer qualquer coisa - nem alarme nem
        # refutacao. Chamar isso de "carga estavel" descartaria a hipotese com
        # base em ruido.
        return []
    fator = pico / med

    if medida in _UNIDADES_SWITCH:
        t = _limiar_switch()
        warn, crit = t.get("warn", 12.0), t.get("crit", 30.0)
        if pico < warn:
            return [_ach(f"switch de redo dentro do esperado na verificacao "
                         f"(pico {pico}/hora, limite {warn:g})", "info",
                         f"serie de {len(serie)} pontos abaixo de {warn:g} "
                         f"switches/hora", leitor="carga")]
        return [_ach(f"switch de redo excessivo: {pico} por hora em {rotulo} "
                     f"(esperado ate ~4-6; limite {warn:g})",
                     "crit" if pico >= crit else "warn",
                     f"switches/hora acima do limiar declarado no catalogo "
                     f"(warn {warn:g}, crit {crit:g})",
                     f"{pico} switches na pior hora", leitor="carga")]

    if medida in _UNIDADES_VOLUME:
        # Volume gerado: observacao, nunca voto. Nem "aumento de carga" (que
        # sustenta) nem "carga estavel" (que refuta) - ver o comentario acima.
        if fator < CARGA_FATOR_WARN:
            return [_ach(f"volume por hora sem variacao relevante "
                         f"(pico {pico} {medida} contra mediana {med:g})",
                         "info", f"serie de {len(serie)} pontos em {medida} - "
                         "volume gerado nao mede numero de chamadas",
                         leitor="carga")]
        return [_ach(f"pico de volume: {pico} {medida} em {rotulo} contra "
                     f"mediana de {med:g} ({fator:.1f}x)", "warn",
                     f"serie por hora medida em {medida} (volume gerado, nao "
                     "numero de chamadas)",
                     f"{fator:.1f}x o volume tipico", leitor="carga")]
    unidade = medida or ("execucoes" if "execu" in baixo else "sessoes")
    if fator < CARGA_FATOR_WARN:
        return [_ach(f"carga estavel na verificacao (pico {pico} contra "
                     f"mediana {med:g} {unidade}/hora)", "info",
                     f"serie de {len(serie)} pontos sem mudanca de patamar",
                     leitor="carga")]
    return [_ach(f"aumento de carga: pico de {pico} {unidade} por hora em "
                 f"{rotulo} contra mediana de {med:g}",
                 "crit" if fator >= CARGA_FATOR_CRIT else "warn",
                 f"serie por hora com pico {fator:.1f}x a mediana",
                 f"{fator:.1f}x a carga tipica", leitor="carga")]


# ── leitura de tabela por nome de coluna ──────────────────────
# Os leitores de tuning precisam da MESMA coisa: achar colunas pelo nome no
# cabecalho e ler a celula certa em cada linha. Cada um resolvia isso por
# conta, e a diferenca entre eles era onde os bugs moravam.
#
# As colunas sao contadas DE TRAS PARA A FRENTE. A primeira coluna costuma ser
# texto de largura variavel (sql_id, nome de modulo) e pode conter espaco; as
# de numero, nao. Contando do fim, um rotulo com espaco desalinha nada.
def _casa_coluna(token: str, apelidos: tuple) -> bool:
    """O cabecalho e o apelido sao a mesma coluna.

    Alem do nome exato, aceita o SUFIXO DE UNIDADE que quem escreve a consulta
    poe no alias: `ELAPSED_TIME_S`, `EXECUTIONS_H`. Sem isso a leitura falhava
    em silencio de um jeito traicoeiro - o teste de porta olhava a saida
    inteira ('elapsed_time' aparece dentro de 'elapsed_time_s' e passava), mas
    a busca da coluna exigia igualdade e nao achava nada. O leitor entrava e
    devolvia vazio.
    """
    if token in apelidos:
        return True
    return any(token.startswith(a + "_") and
               re.fullmatch(r"[a-z]{1,4}\d?", token[len(a) + 1:])
               for a in apelidos)


def _linhas_de_tabela(texto: str, familias: dict) -> list:
    """[{familia: token}] por linha de dado, para as colunas pedidas.

    `familias` mapeia um nome logico a todos os cabecalhos que o representam
    ({"sqlid": ("sql_id",), "plano": ("plan_hash_value",)}). Linha sem a
    coluna pedida simplesmente nao traz a chave - cabe a quem chamou decidir
    se isso e motivo para desistir da linha.
    """
    idx, ncols, fora = {}, 0, []
    for linha in _linhas(texto):
        if set(linha.strip()) <= {"-", " ", "|"}:
            continue
        toks = [t.strip("|") for t in linha.split() if t.strip("|")]
        if not toks:
            continue
        if not re.search(r"\d", linha):
            baixos = [t.lower().strip("#():") for t in toks]
            achou = {}
            for nome, apelidos in familias.items():
                i = next((k for k, t in enumerate(baixos)
                          if _casa_coluna(t, apelidos)), None)
                if i is not None:
                    achou[nome] = i
            if achou:
                idx, ncols = achou, len(toks)
            continue
        if not idx or not ncols:
            continue
        celulas = {}
        for nome, i in idx.items():
            pos = len(toks) - (ncols - i)
            if 0 <= pos < len(toks):
                celulas[nome] = toks[pos]
        if celulas:
            fora.append(celulas)
    return fora


# ── troca de plano ──────────────────────────────────────────
# Dois PLAN_HASH_VALUE para o mesmo SQL_ID e a prova de 'ontem estava bom'. Um
# so e a REFUTACAO - e ela vale ouro, porque impede o plantao inteiro de
# perseguir uma regressao que nao existe.
#
# "PARA O MESMO SQL_ID" e a parte que faltava. Contando os hashes da saida
# inteira, um top SQL - a tabela mais colada num caso de tuning - virava
# "troca de plano: 5 plan_hash_value diferentes" so porque cinco SQLs
# diferentes tem, naturalmente, cinco planos diferentes. Nada tinha mudado. E
# o falso positivo mais caro do framework: manda fixar um plano que nunca
# regrediu, no SQL errado.
_RE_HASH = re.compile(r"\b\d{6,20}\b")


def _ler_planos(texto: str, _pergunta: str) -> list:
    """Quantos planos diferentes o historico do SQL mostra.

    O numero e lido da COLUNA do cabecalho, nao de qualquer numero grande da
    linha. Foi um bug real: `ELAPSED_TIME` e `BUFFER_GETS` tem 6+ digitos, e
    varrer a linha inteira transformava dois snapshots do MESMO plano em
    "troca de plano" - a hipotese mais caramente errada que existe, porque
    manda fixar um plano que nunca mudou.
    """
    baixo = texto.lower()
    if "plan_hash_value" not in baixo and "plan hash value" not in baixo:
        return []

    # Rotulo do dbms_xplan ('Plan hash value: 123456789') vem solto na linha,
    # fora de qualquer tabela. Dois planos assim sao dois planos do mesmo SQL:
    # e a saida de quem pediu o plano duas vezes.
    soltos = set()
    for linha in _linhas(texto):
        b = linha.lower()
        if "plan hash value" in b or "plan_hash_value" in b:
            if ":" in linha or "=" in linha:
                soltos.update(_RE_HASH.findall(linha))

    por_sql = {}
    for cel in _linhas_de_tabela(texto, {"plano": ("plan_hash_value", "plan",
                                                   "plan_hash"),
                                         "sqlid": ("sql_id", "sqlid")}):
        h = cel.get("plano", "")
        if not _RE_HASH.fullmatch(h):
            continue
        # Sem coluna de sql_id, a saida e o historico de UM SQL - foi o que o
        # DBA pediu. Todas as linhas caem no mesmo balde.
        por_sql.setdefault(cel.get("sqlid", ""), set()).add(h)
    if soltos:
        por_sql.setdefault("", set()).update(soltos)

    if not por_sql:
        return []
    trocaram = {s: h for s, h in por_sql.items() if len(h) > 1}
    if not trocaram:
        n = sum(len(h) for h in por_sql.values())
        # Varios SQLs com um plano cada nao e "plano estavel" do caso: e uma
        # lista onde nada mudou. Dizer que um plano especifico esta estavel
        # seria refutar a regressao com base numa saida que nao a examinou.
        if len(por_sql) > 1:
            return [_ach(f"nenhuma troca de plano na verificacao ({len(por_sql)}"
                         f" SQL distintos, um plano cada)", "info",
                         "cada sql_id da saida aparece com um unico "
                         "plan_hash_value", leitor="planos")]
        return [_ach("plano estavel na verificacao (plan_hash_value unico)",
                     "info", f"um so plan_hash_value no historico colado "
                             f"({n} linha(s))", leitor="planos")]

    alvo, hashes = max(trocaram.items(), key=lambda x: len(x[1]))
    de_quem = f" do SQL {alvo}" if alvo else ""
    return [_ach(f"troca de plano: {len(hashes)} plan_hash_value diferentes "
                 f"no historico{de_quem}", "crit",
                 "mais de um plan_hash_value para o MESMO sql_id na saida",
                 f"{len(hashes)} planos distintos", leitor="planos")]


# ── mudanca na aplicacao (o deploy que ninguem avisou) ─────────
# `FIRST_LOAD_TIME` e `LAST_DDL_TIME` de hoje respondem a pergunta que o chamado
# nunca traz: mudou alguma coisa? Achar isso na saida colada e o que evita
# passar a madrugada tunando um SQL que nasceu ontem a tarde.
_MARCAS_MUDANCA = ("first_load_time", "last_ddl_time", "last_active_time",
                   "created", "module")

# Ate 2 dias e "acabou de entrar": e a janela em que um deploy explica o
# sintoma de hoje sem forcar coincidencia.
MUDANCA_DIAS = 2


def _ler_mudanca(texto: str, _pergunta: str) -> list:
    """Objeto recompilado ou SQL novo, pela data mais RECENTE da saida."""
    baixo = texto.lower()
    if not any(m in baixo for m in _MARCAS_MUDANCA):
        return []
    hoje = data_de_referencia()
    melhor = None
    for linha in _linhas(texto):
        for m in _RE_DATA.finditer(linha):
            d = _data_do_token(m)
            if not d:
                continue
            dias = (hoje - d).days
            if dias < 0:
                continue
            if melhor is None or dias < melhor[0]:
                melhor = (dias, linha.split()[0])
    if melhor is None:
        return []
    dias, alvo = melhor
    if dias > MUDANCA_DIAS:
        return [_ach(f"sem mudanca recente na verificacao (mais nova tem "
                     f"{dias} dias)", "info",
                     "nenhuma data das ultimas 48h na saida colada",
                     leitor="mudanca")]
    quando = "hoje" if dias == 0 else f"ha {dias} dia(s)"
    if "last_ddl_time" in baixo:
        titulo = f"objeto recompilado {quando}: {alvo} (last_ddl_time)"
    else:
        titulo = f"SQL novo carregado {quando}: {alvo} (first_load_time)"
    return [_ach(titulo, "warn",
                 "data das ultimas 48h na saida colada - mudanca na aplicacao "
                 "que o chamado nao menciona",
                 f"mudanca {quando}", leitor="mudanca")]


# ── custo por execucao: o discriminador do tuning ─────────────
# Aqui esta a resposta que separa CARGA de REGRESSAO, e nenhuma outra evidencia
# resolve isso: se o custo POR EXECUCAO nao mudou, o SQL nao piorou - alguem
# passou a chama-lo mais. Se subiu, o volume e desculpa.
#
# O veredito sai de uma RAZAO (pico contra mediana), nao de um valor absoluto -
# e por isso vale com o `elapsed_time` em microssegundos do Oracle ou com o
# numero ja dividido por quem montou a consulta. Unidade errada estragaria um
# limite fixo; nao estraga uma proporcao.
_COLS_EXEC = ("executions", "execucoes", "execs", "exec")
_COLS_ELAPSED = ("elapsed_time", "elapsed", "ela_time", "elapsed_seg",
                 "tempo_total")
_COLS_PER_EXEC = ("elapsed_per_exec", "per_exec", "por_exec", "seg_por_exec",
                  "ms_por_exec", "custo_exec")

CUSTO_FATOR_WARN = 2.0
CUSTO_FATOR_CRIT = 3.0


def _num(tok: str):
    """Numero da celula, com a virgula lida pelo que ela e naquele token.

    A mesma virgula significa duas coisas na saida do Oracle: milhar em
    `1,234,567` (padrao do SQL*Plus e do AWR) e decimal em `98,7` (pt-BR).
    Tratando as duas como decimal, `50,000,000` de elapsed_time viraria 50 - e
    o custo por execucao sairia um milhao de vezes menor, sem erro nenhum na
    tela.
    """
    if _RE_MILHAR.fullmatch(tok) or _RE_MILHAR_DEC.fullmatch(tok):
        return float(tok.replace(",", ""))
    try:
        return float(tok.replace(",", "."))
    except ValueError:
        return None


def _base_de_comparacao(valores: list) -> float:
    """A referencia contra a qual o pico e julgado: a mediana SEM o pico.

    A mediana crua parece a escolha obvia e mente no caso mais comum de todos,
    o de dois pontos - antes e depois. Com [1000, 9000] ela e 5000, e uma
    regressao de 9x aparece como 1.8x: abaixo de qualquer limite, o achado sai
    como "custo estavel" e a hipotese certa e REFUTADA pela propria evidencia
    que a confirmava.

    Tirando o pico, a base passa a ser "como era quando nao estava ruim". Com
    dois pontos isso e exatamente o ponto antigo; com muitos, continua sendo a
    mediana robusta - um valor a menos nao move a mediana de uma serie.
    """
    if len(valores) < 2:
        return 0.0
    resto = sorted(valores)[:-1]
    return _mediana(resto)


def _ler_custo(texto: str, _pergunta: str) -> list:
    """Custo por execucao do SQL: subiu (regressao) ou esta igual (carga).

    A comparacao so vale DENTRO do mesmo sql_id. Um top SQL - a tabela mais
    colada num caso de tuning - traz varios SQLs distintos, e o mais caro
    deles e naturalmente muitas vezes a mediana da lista: nada regrediu, eles
    so sao diferentes entre si. Comparar linha com linha ali produzia
    "regressao no custo por execucao" em toda coleta de tuning, e essa frase
    SUSTENTA a hipotese de regressao de plano.
    """
    baixo = texto.lower()
    tem_par = (any(c in baixo for c in _COLS_EXEC)
               and any(c in baixo for c in _COLS_ELAPSED))
    if not tem_par and not any(c in baixo for c in _COLS_PER_EXEC):
        return []

    # O per-exec e procurado primeiro porque 'elapsed_per_exec' tambem casa
    # com 'elapsed'; a familia mais especifica tem de vencer.
    linhas = _linhas_de_tabela(texto, {
        "per": _COLS_PER_EXEC, "exec": _COLS_EXEC, "ela": _COLS_ELAPSED,
        "sqlid": ("sql_id", "sqlid"), "rotulo": ("sql_id", "sqlid", "snap_id",
                                                 "hora", "modulo", "module")})
    custos = []
    for cel in linhas:
        por_exec = _num(cel.get("per", "")) if cel.get("per") else None
        if por_exec is None:
            execs, ela = _num(cel.get("exec", "")), _num(cel.get("ela", ""))
            if not execs or ela is None or execs <= 0:
                continue
            # 'elapsed' e 'per_exec' podem cair na mesma coluna quando o
            # cabecalho tem so um dos dois: descartar aqui evita dividir um
            # numero por ele mesmo.
            por_exec = ela / execs
        if por_exec is not None and por_exec > 0:
            custos.append((cel.get("sqlid", ""),
                           cel.get("rotulo") or cel.get("sqlid", "") or "linha",
                           por_exec))
    if not custos:
        return []

    if len(custos) == 1:
        # Um ponto nao compara nada: registrar como leitura e honesto, votar
        # nao seria.
        return [_ach(f"custo por execucao medido: {custos[0][2]:.4g} "
                     f"(unidade da propria saida)", "info",
                     "uma linha so - sem comparativo para concluir",
                     leitor="custo")]

    # Agrupa por sql_id. Sem a coluna, todas as linhas sao do mesmo SQL - foi
    # o historico dele que o DBA pediu.
    grupos = {}
    for sqlid, rotulo, custo in custos:
        grupos.setdefault(sqlid, []).append((rotulo, custo))
    comparaveis = {s: v for s, v in grupos.items() if len(v) >= 2}

    if not comparaveis:
        # Varios sql_id com uma linha cada: e um ranking, nao uma serie. O que
        # da para dizer e QUAL olhar primeiro - dizer "regressao" seria
        # afirmar uma mudanca que esta saida nao tem como mostrar.
        alvo, pico = max(((r, c) for _s, r, c in custos), key=lambda x: x[1])
        base = _base_de_comparacao([c for _s, _r, c in custos])
        quanto = f", {pico / base:.0f}x a mediana dos demais" if base > 0 else ""
        return [_ach(f"SQL mais caro por chamada na lista: {alvo}{quanto}",
                     "info",
                     f"{len(custos)} SQL distintos, um ponto cada - ranking, "
                     "nao comparacao no tempo", leitor="custo")]

    piores = []
    for sqlid, pontos in comparaveis.items():
        valores = [c for _r, c in pontos]
        base = _base_de_comparacao(valores)
        if base <= 0:
            continue
        rotulo, pico = max(pontos, key=lambda x: x[1])
        piores.append((pico / base, sqlid, rotulo, len(valores)))
    if not piores:
        return []
    fator, sqlid, rotulo, n = max(piores)
    de_quem = f" do SQL {sqlid}" if sqlid else ""
    # Sem coluna de snap/hora, o rotulo da linha E o proprio sql_id: repetir
    # daria "regressao do SQL 9xy: 9xy esta 9x acima".
    onde = "" if (not rotulo or rotulo == sqlid) else f" em {rotulo}"

    if fator < CUSTO_FATOR_WARN:
        return [_ach(f"custo por execucao estavel na verificacao "
                     f"({n} pontos{de_quem}, pico {fator:.1f}x a base)",
                     "info",
                     "o custo de cada execucao nao mudou - se o total subiu, "
                     "subiu o numero de chamadas", leitor="custo")]
    return [_ach(f"regressao no custo por execucao{de_quem}{onde}: "
                 f"{fator:.1f}x acima do custo anterior",
                 "crit" if fator >= CUSTO_FATOR_CRIT else "warn",
                 "custo por execucao comparado entre pontos do MESMO sql_id",
                 f"{fator:.1f}x o custo tipico", leitor="custo")]


# ── plano de execucao (dbms_xplan) ────────────────────────────
# O plano colado e a evidencia mais rica que existe num caso de tuning, e era a
# que o framework ignorava por completo. Aqui so o que e objetivo: acesso full
# em tabela e o volume estimado - o resto (cardinalidade errada, join order) e
# leitura humana, e chutar isso seria inventar.
_RE_XPLAN_FULL = re.compile(
    r"table access (full|storage full)\s*\|?\s*([a-z0-9_$#.]+)?", re.I)


def _ler_plano_execucao(texto: str, _pergunta: str) -> list:
    """Acesso full em tabela no plano colado."""
    baixo = texto.lower()
    if "table access" not in baixo:
        return []
    alvos = []
    for linha in _linhas(texto):
        m = _RE_XPLAN_FULL.search(linha)
        if not m:
            continue
        # No dbms_xplan o nome da tabela e a coluna seguinte a operacao.
        campos = [c.strip() for c in linha.split("|") if c.strip()]
        nome = m.group(2) or ""
        if not nome:
            for i, c in enumerate(campos):
                if "table access" in c.lower() and i + 1 < len(campos):
                    nome = campos[i + 1].split()[0]
                    break
        if nome and nome not in alvos:
            alvos.append(nome)
    if not alvos:
        return []
    lista = ", ".join(alvos[:4])
    return [_ach(f"full table scan no plano: {lista}", "warn",
                 "operacao TABLE ACCESS FULL na saida do plano colada",
                 f"{len(alvos)} tabela(s) em acesso full", leitor="xplan")]


# ── top SQL por DB time ───────────────────────────────────────
# Priorizar por DB time e a regra numero um do Method R, e o passo que produz
# essa lista era justamente o que voltava sem leitura: o framework via uma
# tabela de sql_id e percentual e nao dizia nada. Nomear o SQL dominante e o
# que transforma "o banco esta lento" em "este SQL esta consumindo o banco".
_RE_SQLID = re.compile(r"\b[0-9a-z]{13}\b")

# Abaixo disso nao ha SQL dominante: o tempo esta espalhado, e apontar o
# primeiro da lista seria dar a ele uma importancia que o numero nao sustenta.
SQL_DOMINA_WARN = 20.0
SQL_DOMINA_CRIT = 40.0

# Piso absoluto de DB time para eleger um culpado. No ASH a amostragem e de uma
# por segundo, entao a soma da coluna de amostras e, na pratica, o total de
# SEGUNDOS de DB time da janela.
#
# Sem esse piso, um banco PARADO produzia o achado mais alarmante do relatorio:
# uma unica amostra na janela inteira vira '100% do DB time' e saia como
# critico, mandando tunar um SQL que rodou uma vez. E a mesma armadilha do
# `CARGA_MIN_PICO` - proporcao sobre numero pequeno e aritmetica, nao
# diagnostico. Cem segundos de DB time acumulado nao sao problema de
# desempenho em janela nenhuma.
TOP_SQL_MIN_AMOSTRAS = 100
_COLS_AMOSTRAS = ("db_time_samples", "samples", "sample_count", "num_samples",
                  "amostras", "dbtime_samples")


def _ler_top_sql(texto: str, _pergunta: str) -> list:
    """SQL_ID que concentra o DB time, quando algum concentra.

    Exige a marca da consulta (`sql_id` no cabecalho): 13 caracteres
    alfanumericos aparecem em qualquer hash, e ler um deles como SQL_ID
    inventaria um culpado.
    """
    baixo = texto.lower()
    if "sql_id" not in baixo:
        return []

    # Caminho principal: a COLUNA de percentual. A saida de uma query traz
    # `PCT_DBTIME` no cabecalho e `62.4` na celula - o sinal de '%' fica no
    # nome da coluna e nao se repete linha a linha. Procurando o glifo '%' em
    # cada linha, o top SQL do AWR - a lista que este leitor existe para ler -
    # nao casava nada e o passo voltava mudo.
    achados = []
    amostras = []
    for cel in _linhas_de_tabela(texto, {
            "sqlid": ("sql_id", "sqlid"),
            "amostras": _COLS_AMOSTRAS,
            "pct": ("pct_dbtime", "pct_db_time", "pct", "percent", "perc",
                    "pct_total", "pctdbtime", "percentual")}):
        sid, bruto = cel.get("sqlid", ""), cel.get("pct", "")
        if not _RE_SQLID.fullmatch(sid) or not re.search(r"\d", sid):
            continue
        pct = _num(bruto.rstrip("%")) if bruto else None
        if pct is None or not 0 < pct <= 100:
            continue
        n = _inteiro(cel.get("amostras", ""))
        if n is not None:
            amostras.append(n)
        achados.append((sid, pct))

    # Janela sem DB time: percentual sobre quase nada e aritmetica, nao
    # diagnostico. So vale quando a saida DIZ quantas amostras tem - sem a
    # coluna nao da para saber, e supor seria trocar um alarme falso por um
    # silencio falso.
    if achados and amostras and sum(amostras) < TOP_SQL_MIN_AMOSTRAS:
        total = sum(amostras)
        return [_ach(f"janela praticamente ociosa na verificacao "
                     f"({total} amostra(s) de DB time no total)", "info",
                     f"menos de {TOP_SQL_MIN_AMOSTRAS} amostras de ASH na "
                     "janela - nao ha DB time suficiente para eleger um SQL "
                     "dominante", leitor="top_sql")]

    # Reserva: texto corrido com o '%' escrito na linha.
    if not achados:
        for linha in _linhas(texto):
            if "sql_id" in linha.lower():
                continue          # cabecalho
            m = _RE_SQLID.search(linha)
            if not m or not re.search(r"\d", m.group(0)):
                continue
            # Precisa de um numero de peso na MESMA linha: sem percentual (ou
            # amostras de ASH), a linha nao diz que aquele SQL domina nada.
            pcts = [float(p.replace(",", ".")) for p in _RE_PCT.findall(linha)]
            if not pcts:
                continue
            achados.append((m.group(0), max(pcts)))
    if not achados:
        return []
    sql_id, pct = max(achados, key=lambda x: x[1])
    if pct < SQL_DOMINA_WARN:
        return [_ach(f"DB time espalhado na verificacao (maior SQL: {sql_id} "
                     f"com {pct:g}%)", "info",
                     f"nenhum SQL acima de {SQL_DOMINA_WARN:g}% do DB time - "
                     "nao ha um culpado unico", leitor="top_sql")]
    return [_ach(f"SQL {sql_id} concentra {pct:g}% do DB time",
                 "crit" if pct >= SQL_DOMINA_CRIT else "warn",
                 "percentual de DB time por sql_id na saida colada",
                 f"{pct:g}% do tempo de banco", leitor="top_sql")]


# ── limite de processes / sessions ────────────────────────────
# `v$resource_limit` responde a pergunta que ninguem faz antes do ORA-00020:
# quanto falta para o banco RECUSAR conexao? Encostar no limite nao e lentidao,
# e indisponibilidade - e o sintoma chega como "a aplicacao nao conecta".
_COLS_ATUAL = ("current_utilization", "atual", "current")
_COLS_LIMITE = ("limit_value", "limite", "limit")

LIMITE_WARN = 80.0
LIMITE_CRIT = 90.0


def _ler_resource_limit(texto: str, _pergunta: str) -> list:
    """Uso de processes/sessions contra o limite configurado."""
    baixo = texto.lower()
    if "resource_limit" not in baixo and not (
            any(c in baixo for c in _COLS_LIMITE)
            and any(c in baixo for c in _COLS_ATUAL)):
        return []
    linhas, i_at, i_lim, ncols = [], None, None, 0
    for linha in _linhas(texto):
        if set(linha.strip()) <= {"-", " ", "|"}:
            continue
        toks = [t.strip("|") for t in linha.split() if t.strip("|")]
        if not toks:
            continue
        baixos = [t.lower() for t in toks]
        if not re.search(r"\d", linha):
            i_at = next((i for i, t in enumerate(baixos)
                         if t in _COLS_ATUAL), None)
            i_lim = next((i for i, t in enumerate(baixos)
                          if t in _COLS_LIMITE), None)
            ncols = len(toks)
            continue
        if i_at is None or i_lim is None or not ncols:
            continue

        def valor(idx, toks=toks, ncols=ncols):
            pos = len(toks) - (ncols - idx)
            if pos < 0 or pos >= len(toks):
                return None
            return _num(toks[pos])

        atual, limite = valor(i_at), valor(i_lim)
        # UNLIMITED vira None: recurso sem teto nao tem percentual, e inventar
        # um denominador aqui produziria alarme onde nao existe limite.
        if atual is None or not limite or limite <= 0:
            continue
        linhas.append((toks[0], atual, limite, 100.0 * atual / limite))
    if not linhas:
        return []
    nome, atual, limite, pct = max(linhas, key=lambda x: x[3])
    if pct < LIMITE_WARN:
        return [_ach(f"limite de conexao com folga na verificacao "
                     f"(maior uso: {nome} em {pct:.0f}%)", "info",
                     f"nenhum recurso acima de {LIMITE_WARN:g}% do limite "
                     "configurado", leitor="limites")]
    return [_ach(f"limite de {nome} em {pct:.0f}% "
                 f"({atual:g} de {limite:g})",
                 "crit" if pct >= LIMITE_CRIT else "warn",
                 "current_utilization contra limit_value na saida colada",
                 f"{pct:.0f}% do limite - risco de ORA-00020",
                 leitor="limites")]


def _ler_single_instance(texto: str, _pergunta: str) -> list:
    """`gv$instance` com uma linha so: o banco NAO e RAC.

    Vale como refutacao explicita - e o que impede um caso single-instance de
    seguir investigando cache fusion por causa de um 'gc' visto de relance.
    """
    baixo = texto.lower()
    if "cluster_database" in baixo and re.search(
            r"cluster_database\s+false", baixo):
        return [_ach("banco single-instance (nao RAC) na saida observada",
                     "info", "cluster_database = FALSE na saida")]
    return []


# O que o modulo sabe ler, em uma linha por leitor. Existe para a TELA poder
# dizer isso ao DBA: o maior desperdicio do recurso nao era erro de leitura, era
# ninguem saber o que valia a pena colar. Fonte unica - leitor novo sem entrada
# aqui reprova em `tests/test_saida.py`.
O_QUE_LEIO = (
    ("erros_ora", "codigo ORA-", "ORA-00257, ORA-01555"),
    ("bloqueio", "cadeia de bloqueio", "gv$session com BLOCKING_SESSION"),
    ("eventos", "evento de espera", "top de waits com % ou tempo"),
    ("parametros", "estado do banco", "log_mode, open_mode, cluster_database"),
    ("espaco", "ocupacao de espaco", "tablespace, FRA, diskgroup, df -h"),
    ("lag", "atraso do standby", "v$dataguard_stats (+00 02:15:33)"),
    ("mrp", "apply do standby", "v$managed_standby"),
    ("swap", "memoria do host", "free -m"),
    ("estatisticas", "idade das estatisticas", "last_analyzed, num_rows"),
    ("instancias", "instancias do cluster", "gv$instance"),
    ("single_instance", "banco nao e RAC", "cluster_database = FALSE"),
    ("carga", "carga por hora", "sessoes/execucoes agrupadas por HH24"),
    ("planos", "troca de plano", "historico de plan_hash_value"),
    ("mudanca", "deploy recente", "first_load_time, last_ddl_time"),
    ("custo", "custo por execucao", "executions + elapsed_time (carga x "
     "regressao)"),
    ("xplan", "plano de execucao", "dbms_xplan (TABLE ACCESS FULL)"),
    ("top_sql", "SQL que domina o DB time", "top SQL com sql_id e %"),
    ("limites", "limite de processes/sessions", "v$resource_limit "
     "(risco de ORA-00020)"),
    ("vazio", "consulta sem linhas", "no rows selected (descarta hipotese)"),
    ("falha", "comando que nao rodou", "ORA-00942, SP2-"),
)


LEITORES = (
    ("falha", _ler_falha),
    ("erros_ora", _ler_erros),
    ("bloqueio", _ler_bloqueio),
    ("eventos", _ler_eventos),
    ("parametros", _ler_parametros),
    ("espaco", _ler_espaco),
    ("lag", _ler_lag),
    ("mrp", _ler_mrp),
    ("swap", _ler_swap),
    ("estatisticas", _ler_estatisticas),
    ("instancias", _ler_instancias),
    ("carga", _ler_carga),
    ("planos", _ler_planos),
    ("mudanca", _ler_mudanca),
    ("custo", _ler_custo),
    ("xplan", _ler_plano_execucao),
    ("top_sql", _ler_top_sql),
    ("limites", _ler_resource_limit),
    ("single_instance", _ler_single_instance),
    ("vazio", _ler_vazio),
)


def interpretar(texto: str, pergunta: str = "", referencia=None) -> dict:
    """Le a saida colada e devolve os achados que ela sustenta.

    `pergunta` e o titulo/objetivo do passo que gerou a saida: e o que permite
    concluir alguma coisa de uma consulta que voltou vazia.

    `referencia` e a data contra a qual se mede IDADE (de estatistica, por
    exemplo). O padrao - hoje - esta certo no uso normal, porque a saida acabou
    de sair do banco. Quem RELE evidencia gravada precisa informar a data da
    COLETA, senao o tempo que passou desde ela entra na conta: medido em
    APPHO600, a mesma evidencia migrava de "estatistica recente (refuta)" para
    "estatistica velha (sustenta)" 28 dias depois, sem nada ter mudado no banco.
    Ver `troubleshoot/melhoria.py` e `tests/test_corpus_real.py`.

    Devolve sempre `achados`, `leitores` (quem reconheceu o que) e `aviso` -
    quando nada foi reconhecido, o aviso e a resposta honesta.
    """
    texto = texto or ""
    if not texto.strip():
        return {"achados": [], "leitores": [], "linhas": 0,
                "aviso": "nada colado"}
    if referencia is not None:
        marca = _REFERENCIA.set(referencia)
        try:
            return _interpretar(texto, pergunta)
        finally:
            _REFERENCIA.reset(marca)
    return _interpretar(texto, pergunta)


def _interpretar(texto: str, pergunta: str) -> dict:

    # Quando o comando falhou, o resto da saida nao e resultado: interpretar as
    # outras linhas seria concluir a partir de uma consulta que nao rodou.
    falha = _ler_falha(texto, pergunta)
    if falha:
        return {"achados": falha, "leitores": ["falha"],
                "linhas": len(_linhas(texto)),
                "aviso": "o comando nao executou - corrija e cole de novo"}

    achados, usados = [], []
    for nome, fn in LEITORES:
        if nome == "falha":
            continue
        try:
            novos = fn(texto, pergunta)
        except Exception:
            # Um leitor com defeito nao pode custar a leitura inteira: o DBA
            # colou algo real e merece o que os outros conseguiram entender.
            novos = []
        if novos:
            achados.extend(novos)
            usados.append(nome)

    # 'vazio' so vale quando NADA mais foi reconhecido. Uma saida com linhas de
    # dados pode conter a frase 'no rows selected' de um segundo comando; deixar
    # a ausencia conviver com a presenca produziria conclusoes contraditorias.
    if len(usados) > 1 and "vazio" in usados:
        achados = [a for a in achados if a.get("leitor") != "vazio"]
        usados.remove("vazio")

    aviso = ""
    if not achados:
        aviso = ("nao reconheci nada nesta saida - cole a tabela completa "
                 "(com o cabecalho das colunas) ou registre a leitura como "
                 "nota do passo")
    return {"achados": achados, "leitores": usados,
            "linhas": len(_linhas(texto)), "aviso": aviso}
