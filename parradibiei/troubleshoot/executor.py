"""
executor.py - o passo de VERIFICACAO roda no proprio framework.

O guiado ja dizia o que rodar, o que olhar e qual a armadilha; e ja sabia LER a
saida colada (`saida.py`) e repontuar as hipoteses. O que continuava manual era
justamente o meio do caminho: sair da tela, colar no SQL*Plus, copiar o
resultado e voltar. Em plantao, esse trecho e onde o roteiro morre - alguem roda
o comando, le na tela, resolve de cabeca e o caso fica sem a evidencia.

Este modulo fecha o circuito: **executa o SQL do passo pela mesma trava
read-only da coleta**, grava a saida como evidencia do caso e devolve o texto no
formato que os leitores de `saida.py` entendem. A conducao passa a reagir ao que
o banco respondeu agora, sem depender de copiar e colar.

O QUE ELE NAO FAZ, E POR QUE

1. **Nao executa passo de risco `mudanca` nem `destrutivo`.** Esses continuam
   atras da Matriz de Risco, e executar continua sendo ato humano. A automacao
   aqui existe para VERIFICAR, nao para agir - um `ALTER SYSTEM KILL SESSION`
   disparado por um botao e exatamente o acidente que a Matriz previne.

2. **Nao tem validador proprio.** Quem decide se um comando e leitura pura e o
   `collector.validate_select`, o mesmo da coleta. Gate de seguranca duplicado e
   gate que um dia discorda de si mesmo, e o lado que erra e sempre o que
   ninguem lembrou de atualizar.

3. **Nao adivinha valor de parametro que muda a cada segundo.** SQL_ID e
   tablespace saem dos achados com seguranca; SID e numero de instancia, nao -
   ver `sugerir_parametros`.

4. **Nao grava na raiz da pasta do caso.** A evidencia executada vai para
   `troubleshooting/<caso>/guia/`, uma SUBPASTA, porque `TROUBLESHOOT.analisar_caso`
   varre o caso com `os.listdir` (plano, nao recursivo). Na raiz, a mesma saida
   seria lida DUAS vezes: uma pelos analisadores (virando achado no Health
   Score) e outra por `saida.interpretar` (virando achado observado no
   raciocinio) - e `achados_do_caso` soma os dois. O sinal contaria em dobro e a
   hipotese subiria de veredito sem observacao nova, que e a mesma inflacao de
   pontuacao que a deduplicacao de `guia.observados` foi escrita para impedir.
   Se algum dia alguem mover este arquivo um nivel para cima "para aparecer no
   dashboard", e isso que vai quebrar, em silencio.
"""
from __future__ import annotations

import os
import re

from .collector import CASES_DIR, render_table, validate_select

# Onde a evidencia executada mora. Subpasta - ver ponto 4 do cabecalho.
SUBPASTA = "guia"

# Linha que nao e SQL: comentario ou comando de formatacao do SQL*Plus. Sao
# descartadas em silencio (mas contadas) porque estao nos playbooks a servico de
# quem le, nao do banco: `SET LINESIZE 200` orienta o DBA que vai colar no
# terminal e nao tem o que fazer no driver.
_COMENTARIO = re.compile(r"^\s*(--|#|rem\b)", re.I)
_SQLPLUS = re.compile(
    r"^\s*(set|col|column|ttitle|btitle|spool|prompt|define|undefine|break|"
    r"compute|clear|whenever|accept|pause|host|conn|connect|exit|quit|"
    r"desc|describe|show|store|save|start|@@?|/)\b", re.I)

# Variavel de substituicao do SQL*Plus (`&sql_id`, `&&tablespace`). O framework
# preenche isso; e o unico tipo de lacuna que ele sabe fechar sozinho.
_PARAM = re.compile(r"&&?([a-z_][a-z_0-9]*)", re.I)

# Marcador de ambiente que o playbook deixa para o humano trocar: `<db>`,
# `<instancia>`, `<caminho/do/arquivo>`.
#
# A classe EXIGE letra ou '_' imediatamente depois do '<' e proibe espaco. Sem
# isso, um `WHERE tempo < 10 AND pct > 90` casaria o trecho `< 10 AND pct >` e o
# passo seria recusado por "precisa de parametro" - uma recusa inventada sobre
# SQL perfeitamente executavel. O operador `<>` tambem fica de fora pelo mesmo
# motivo.
_AMBIENTE = re.compile(r"<[a-z_][a-z_0-9./:|-]*>", re.I)

# Ferramenta de linha de comando que aparece nos passos e NAO fala SQL*Net.
# Reconhecer o nome permite recusar dizendo qual e o caso, em vez de um
# "comando invalido" que nao ajuda ninguem a decidir o que fazer.
_FERRAMENTAS = (
    ("rman", "RMAN"), ("adrci", "ADRCI"), ("lsnrctl", "listener (lsnrctl)"),
    ("tnsping", "TNS (tnsping)"), ("dgmgrl", "Data Guard broker (dgmgrl)"),
    ("ggsci", "GoldenGate (ggsci)"), ("srvctl", "Grid (srvctl)"),
    ("crsctl", "Clusterware (crsctl)"), ("asmcmd", "ASM (asmcmd)"),
    ("dbv", "DBVERIFY"), ("oerr", "oerr"), ("sqlplus", "SQL*Plus"),
    ("vmstat", "sistema operacional"), ("iostat", "sistema operacional"),
    ("uptime", "sistema operacional"), ("free", "sistema operacional"),
    ("df ", "sistema operacional"), ("ps ", "sistema operacional"),
    ("grep", "sistema operacional"), ("tail", "sistema operacional"),
    ("top", "sistema operacional"), ("sar", "sistema operacional"),
    ("ping", "rede"), ("nslookup", "rede"),
)

# ── Licenca Oracle: Diagnostics / Tuning Pack ─────────────────
# Diagnostics e Tuning Pack sao AUDITADOS e cobrados pela Oracle, e o uso e
# registrado por consultar o objeto - nao por rodar uma ferramenta grafica. A
# coleta ja respeita isso (`--pack auto` le CONTROL_MANAGEMENT_PACK_ACCESS e
# filtra os checks por `requires_pack` do catalogo). O caminho guiado nasceu
# SEM esse portao, e a consequencia era concreta: 26 dos passos executaveis
# tocam DBA_HIST/ASH/DBMS_SQLTUNE, entao um clique num banco sem licenca
# consumia pack que o site nao tem - exatamente o risco que o framework se
# propoe a evitar.
#
# Os passos da KB nao declaram exigencia de pack (nao ha campo para isso), logo
# a classificacao e pelo OBJETO consultado.
_PACK_DIAGNOSTIC = re.compile(
    r"\b(dba_hist_\w+|v\$active_session_history|gv\$active_session_history|"
    r"dba_addm_\w+|dba_advisor_\w+|v\$ash_info)\b", re.I)
_PACK_TUNING = re.compile(
    r"\b(dbms_sqltune|v\$sql_monitor|gv\$sql_monitor|v\$sql_plan_monitor|"
    r"gv\$sql_plan_monitor|dba_sqlset\w*|dba_sql_profiles)\b", re.I)


def pack_exigido(sql: str) -> str:
    """'TUNING', 'DIAGNOSTIC' ou 'NONE' para um statement.

    TUNING primeiro porque quem tem Tuning tem Diagnostics junto: classificar
    um `DBMS_SQLTUNE` como DIAGNOSTIC o liberaria num banco que so tem
    Diagnostics licenciado.

    `DBA_ADVISOR_*` entra como DIAGNOSTIC por CONSERVADORISMO, e vale registrar
    a duvida: o Segment Advisor e gratuito e le essas mesmas views, entao a
    classificacao bloqueia um passo legitimo num banco sem licenca. A assimetria
    decide - passo bloqueado se resolve colando a saida (custa incomodo),
    licenca consumida por engano nao se desfaz (custa dinheiro, em auditoria).
    Quem precisar libera com `--pack`.
    """
    s = sql or ""
    if _PACK_TUNING.search(s):
        return "TUNING"
    if _PACK_DIAGNOSTIC.search(s):
        return "DIAGNOSTIC"
    return "NONE"


def filtrar_por_pack(statements, permitidos) -> tuple:
    """(statements que a licenca permite, [(sql, pack) recusados]).

    `permitidos` e o conjunto do `collector._PACK_LEVEL` - a MESMA fonte da
    coleta, para que os dois caminhos nunca discordem sobre o que a licenca
    daquele banco autoriza.
    """
    ok, fora = [], []
    for s in statements or []:
        p = pack_exigido(s)
        if p in (permitidos or {"NONE"}):
            ok.append(s)
        else:
            fora.append((s, p))
    return ok, fora


# Quantas linhas de cada statement entram na evidencia. O mesmo teto da coleta:
# saida de `gv$session` num RAC ocupado passa de mil linhas e o que decide o
# passo esta sempre no topo (as consultas dos playbooks ordenam por tempo,
# espera ou tamanho).
MAX_LINHAS = 60


def _split_statements(texto: str) -> list:
    """Quebra em statements pelo ';' que esta FORA de literal de texto.

    Cortar no ';' cru parece equivalente e nao e: o playbook de locks tem um
    passo que MONTA o comando de kill como string
    (`... || ''' IMMEDIATE;  -- ' || ...`). O ';' ali mora dentro de um literal,
    e um split ingenuo parte a query em duas metades invalidas - o passo falharia
    com erro de sintaxe, num SELECT que esta correto.
    """
    out, atual, em_texto = [], [], False
    i, n = 0, len(texto)
    while i < n:
        c = texto[i]
        if c == "'":
            # '' dentro de literal e um apostrofo escapado, nao o fim dele.
            if em_texto and i + 1 < n and texto[i + 1] == "'":
                atual.append("''")
                i += 2
                continue
            em_texto = not em_texto
            atual.append(c)
        elif c == ";" and not em_texto:
            out.append("".join(atual))
            atual = []
        else:
            atual.append(c)
        i += 1
    out.append("".join(atual))
    return [s.strip() for s in out if s.strip()]


def _limpar(comando: str) -> tuple:
    """(texto sem comentario/SQL*Plus, quantas linhas foram descartadas)."""
    mantidas, fora = [], 0
    for linha in (comando or "").splitlines():
        if not linha.strip():
            continue
        if _COMENTARIO.match(linha) or _SQLPLUS.match(linha):
            fora += 1
            continue
        mantidas.append(linha)
    return "\n".join(mantidas), fora


def _ferramenta(texto: str) -> str:
    low = texto.lower().lstrip()
    for marca, nome in _FERRAMENTAS:
        if low.startswith(marca) or f"\n{marca}" in low:
            return nome
    return ""


def preparar(passo: dict) -> dict:
    """O passo roda no framework? Se sim, com quais statements e que parametros.

    Devolve sempre a mesma forma, para que a tela e o CLI nao precisem repetir
    a analise:
      pode         - da para executar (talvez faltando parametro)
      motivo       - por que NAO, em linguagem de plantao
      statements   - as leituras que passaram no validate_select
      recusados    - o que sobrou, com o porque de cada um
      parametros   - nomes de &variavel ainda por preencher
      descartadas  - linhas de comentario/SQL*Plus que nao foram ao banco
    """
    passo = passo or {}
    vazio = {"pode": False, "motivo": "", "statements": [], "recusados": [],
             "parametros": [], "descartadas": 0}

    risco = (passo.get("risco") or "leitura").lower()
    if risco != "leitura":
        return dict(vazio, motivo=(
            f"passo de risco {risco.upper()}: muda o ambiente. O framework so "
            "executa VERIFICACAO - este segue atras da Matriz de Risco, "
            "executado por voce, e registrado com --confirmar."))

    comando = passo.get("comando") or ""
    texto, descartadas = _limpar(comando)
    if not texto.strip():
        return dict(vazio, descartadas=descartadas, motivo=(
            "este passo nao tem comando para executar (e consulta a "
            "documentacao ou ao My Oracle Support)."))

    statements = _split_statements(texto)
    ok, recusados = [], []
    for s in statements:
        if not validate_select(s):
            recusados.append({"sql": s, "porque": "nao e leitura pura (SELECT/WITH)"})
        elif _AMBIENTE.search(s):
            alvos = sorted({m.group(0) for m in _AMBIENTE.finditer(s)})
            recusados.append({"sql": s,
                              "porque": f"depende do ambiente: {', '.join(alvos)}"})
        else:
            ok.append(s)

    if not ok:
        ferr = _ferramenta(texto)
        if ferr:
            motivo = (f"comando de {ferr}: nao roda por SQL*Net. Execute no "
                      "servidor e cole a saida aqui - ela e lida do mesmo jeito.")
        elif all("ambiente" in r["porque"] for r in recusados):
            motivo = (f"{recusados[0]['porque']} - troque o marcador e execute "
                      "no terminal, ou cole a saida aqui.")
        else:
            motivo = ("nao ha leitura pura para executar neste passo; "
                      "cole a saida aqui.")
        return dict(vazio, motivo=motivo, recusados=recusados,
                    descartadas=descartadas)

    nomes = []
    for s in ok:
        for m in _PARAM.finditer(s):
            if m.group(1).lower() not in [n.lower() for n in nomes]:
                nomes.append(m.group(1))

    return {"pode": True, "motivo": "", "statements": ok,
            "recusados": recusados, "parametros": nomes,
            "descartadas": descartadas}


# Parametros que o framework preenche a partir da evidencia, e os que ele
# deliberadamente NAO preenche.
#
# SQL_ID e nome de tablespace sao estaveis: o SQL que estava caro na coleta
# continua sendo o mesmo objeto meia hora depois, e a tablespace em 97% nao
# muda de nome. Ja SID, SERIAL# e numero de instancia descrevem uma SESSAO, que
# nasce e morre em segundos - sugerir o SID lido numa coleta de 40 minutos atras
# aponta o DBA para uma sessao que provavelmente nao existe mais, e o pior e que
# o numero PARECE certo. Nesses, o campo fica vazio de proposito, para ser
# preenchido com o que a tela mostra AGORA.
_SUGERE_SQL_ID = ("sql_id", "sqlid")
_SUGERE_TABLESPACE = ("tablespace", "ts", "tbs")
_NAO_SUGERE = ("sid", "serial", "inst", "inst_id", "instancia", "spid", "pid")

_RE_TABLESPACE = re.compile(
    r"\btablespace\s+([A-Z][A-Z0-9_$#]{2,29})\b", re.I)


def _texto_do_achado(a: dict) -> str:
    return " ".join(str(a.get(k) or "") for k in
                    ("title", "detail", "impact", "recommendation"))


_PESO_SEV = {"crit": 0, "warn": 1, "info": 2, "ok": 3}
_RE_IMPACTO_S = re.compile(r"(\d+(?:[.,]\d+)?)\s*s\b")


def _ordem_de_relevancia(achados) -> list:
    """Achados do mais para o menos relevante: severidade, depois IMPACTO.

    A ordem importa porque a sugestao pega o PRIMEIRO valor que encontrar. Sem
    critério, o "primeiro" era o da secao que por acaso vinha antes no
    relatorio - e no caso real de APPHO600 havia tres SQL_ID candidatos, todos
    `warn`, com impactos de 0.1s, 5.3s e 868s. Escolher pela ordem do arquivo
    e sortear qual SQL o plantao vai tunar.

    Impacto em SEGUNDOS e o desempate certo aqui: e a unidade do Method R -
    prioriza-se pelo tempo que o SQL efetivamente custou, nao pelo percentual
    (que faz 0.074s -> 0.185s parecer o pior do relatorio, com +150%).
    """
    def chave(a):
        sev = _PESO_SEV.get((a.get("severity") or "").lower(), 9)
        m = _RE_IMPACTO_S.search(_texto_do_achado(a))
        try:
            impacto = float(m.group(1).replace(",", ".")) if m else 0.0
        except ValueError:
            impacto = 0.0
        return (sev, -impacto)

    return sorted(achados or [], key=chave)


def sugerir_parametros(nomes, achados) -> dict:
    """Valor sugerido para cada &parametro, vindo dos achados do caso.

    So sugere o que a evidencia sustenta. O que nao der, volta como string
    vazia - e a tela pede. Sugestao errada em campo pre-preenchido e pior que
    campo vazio: o DBA confirma sem ler e o passo analisa o objeto errado.

    A varredura segue a ordem de RELEVANCIA (ver `_ordem_de_relevancia`), e nao
    a ordem do relatorio: o parametro sugerido passa a ser o do achado que mais
    custou, que e o que o passo deveria investigar primeiro.
    """
    from . import nextstep

    ordenados = _ordem_de_relevancia(achados)
    out = {}
    for nome in nomes or []:
        low = nome.lower()
        out[nome] = ""
        if low in _NAO_SUGERE:
            continue
        for a in ordenados:
            texto = _texto_do_achado(a)
            if low in _SUGERE_SQL_ID:
                achado = nextstep.find_sql_id(texto)
            elif low in _SUGERE_TABLESPACE:
                m = _RE_TABLESPACE.search(texto)
                achado = m.group(1).upper() if m else ""
            else:
                break
            if achado:
                out[nome] = achado
                break
    return out


# O que pode entrar no lugar de um &parametro. Restritivo de proposito: o valor
# e interpolado no texto do SQL (variavel de substituicao nao existe do lado do
# driver, so no SQL*Plus), entao ele e a UNICA porta por onde texto de fora
# entra na query. Nome de objeto Oracle, SQL_ID e numero cabem aqui; aspas,
# espaco e ';' nao - e sem eles nao ha como fechar um literal nem emendar um
# segundo comando.
_VALOR_OK = re.compile(r"^[A-Za-z0-9_$#.@-]{1,60}$")


def aplicar_parametros(sql: str, valores: dict) -> str:
    """Troca &nome pelo valor informado, recusando valor que nao seja um token.

    Levanta ValueError com o nome do parametro: em vez de sanitizar calado (que
    esconde erro de digitacao), o passo para e diz qual valor nao serve.
    """
    def _sub(m):
        nome = m.group(1)
        bruto = ""
        for k, v in (valores or {}).items():
            if str(k).lower() == nome.lower():
                bruto = str(v or "").strip()
                break
        if not bruto:
            raise ValueError(f"falta o valor de &{nome}")
        if not _VALOR_OK.match(bruto):
            raise ValueError(
                f"valor invalido para &{nome}: {bruto!r} - use so letras, "
                "numeros, '_', '$', '#', '.', '@' ou '-'")
        return bruto

    return _PARAM.sub(_sub, sql or "")


def executar_no_cursor(cur, statements, max_linhas: int = MAX_LINHAS) -> dict:
    """Roda os statements num cursor JA read-only e devolve texto + diagnostico.

    Recebe o cursor de fora (em vez de abrir a conexao) para que a montagem da
    saida seja testavel offline, sem banco: e a mesma razao pela qual
    `saida.interpretar` e funcao pura.

    Falha de UM statement nao derruba os outros: num passo com tres leituras, a
    view que o usuario nao tem privilegio para ler nao pode apagar as duas que
    responderam. O erro entra na saida como `-- ERRO:`, do mesmo jeito que a
    coleta faz - `saida.py` reconhece isso como "o comando nao executou" e nao
    tira conclusao dali.
    """
    partes, erros, linhas_lidas = [], [], 0
    for i, sql in enumerate(statements, 1):
        rotulo = f"CONSULTA {i}/{len(statements)}" if len(statements) > 1 else "CONSULTA"
        # O SQL vai DEPOIS da tabela, e comentado. A posicao nao e estetica: o
        # leitor de `saida.py` acha o cabecalho procurando o nome da coluna
        # (`BLOCKING_SESSION`) e espera a REGUA de tracos na linha seguinte. Com
        # o SQL na frente, ele casava com a linha da propria query - onde o nome
        # da coluna tambem aparece - nao encontrava regua e caia na heuristica
        # aproximada: em vez de "2 sessoes bloqueadas, raiz = SID 907" saia
        # "cadeia ativa em 2 linhas (contagem aproximada)", sem a raiz. Perder a
        # raiz e perder a resposta do passo, porque matar a vitima nao devolve
        # nada ao ambiente. Comentar sem mover NAO resolve - foi medido.
        rodape = "\n".join("-- " + ln for ln in sql.splitlines())
        try:
            cur.execute(sql)
            cols = [d[0] for d in (cur.description or [])]
            rows = cur.fetchall()
            linhas_lidas += len(rows)
            corpo = (render_table(cols, rows, max_rows=max_linhas)
                     if cols else "(sem colunas)")
            if not rows:
                # 'no rows selected' e o vocabulario que o leitor de saida
                # reconhece como verificacao vazia - e vazio REFUTA hipotese.
                # Uma tabela so com cabecalho nao carrega essa informacao.
                corpo += "\n\nno rows selected"
            partes.append(f"=== {rotulo} ===\n{corpo}\n\n-- SQL executado:\n{rodape}")
        except Exception as e:
            primeira = str(e).splitlines()[0] if str(e).splitlines() else str(e)
            erros.append(primeira)
            partes.append(f"=== {rotulo} ===\n-- ERRO: {primeira}\n\n"
                          f"-- SQL executado:\n{rodape}")
    return {"texto": "\n\n".join(partes), "erros": erros,
            "linhas": linhas_lidas, "consultas": len(statements)}


def caminho_evidencia(caso: str, playbook: str, indice: int,
                      cases_dir: str = "") -> str:
    """Onde a saida executada e gravada (subpasta `guia/` - ver cabecalho)."""
    base = cases_dir or CASES_DIR
    # O ponto fica FORA do conjunto permitido: id de playbook nao tem ponto
    # ('02_sessoes_locks'), e sem ponto nao existe '..' para subir de pasta.
    # Barato de garantir aqui, e dispensa confiar na origem do nome.
    seguro = re.sub(r"[^A-Za-z0-9_-]", "_", str(playbook or "passo")) or "passo"
    return os.path.join(base, caso, SUBPASTA, f"{seguro}_p{int(indice) + 1:02d}.txt")


def gravar_evidencia(caminho: str, cabecalho: str, texto: str) -> str:
    """Grava a saida executada, em modo append.

    Append, e nao sobrescrita, porque reexecutar o MESMO passo depois de uma
    acao e o jeito de mostrar que o sintoma cessou (ou nao). Sobrescrever
    apagaria justamente a foto do "antes", que e a metade da prova.
    """
    os.makedirs(os.path.dirname(os.path.abspath(caminho)), exist_ok=True)
    with open(caminho, "a", encoding="utf-8") as fh:
        fh.write(f"{cabecalho}\n{texto}\n\n")
    return caminho
