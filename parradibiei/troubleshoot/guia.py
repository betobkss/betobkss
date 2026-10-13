"""
guia.py - o troubleshooting GUIADO: um passo por vez, com risco e registro.

Por que existe: as pecas do fluxo DBA Master ja estavam todas no framework -
84 checks de coleta, 28 playbooks com passo/risco/armadilha, ciclo de vida com
hipotese e acao, Template Padrao. **O que faltava era o fio.** O playbook nao
sabia em que passo o DBA estava; o caso nao sabia qual playbook estava sendo
seguido. Quem amarrava isso era a memoria de quem conduzia - e e exatamente o
que se perde quando o plantao troca no meio do incidente.

Tres decisoes de desenho, e o motivo de cada uma:

1. **O estado vive no `meta.json` do caso**, junto da evidencia. Nao em
   localStorage nem em memoria do processo: o roteiro tem de sobreviver a fechar
   o navegador, ao reinicio do painel e a troca de turno, e tem de viajar para o
   SharePoint com o resto do caso. Mesma razao de `caso.py`.

2. **A arvore de decisao le os ACHADOS, nao o DBA.** O prompt pergunta "ha wait
   dominante? e de I/O?" - mas o framework JA analisou a evidencia e sabe a
   resposta. Perguntar de novo seria teatro de guia: o ramo e escolhido pelo
   `<caso>_findings.json` e o DBA so confirma (ou usa 'proximo', que registra o
   descarte como qualquer outra hipotese).

3. **Passo que muda o ambiente nao aparece sem a Matriz de Risco.** O comando
   fica retido atras do bloco impacto/rollback/aprovacao (`matriz_risco`), e a
   confirmacao GRAVA a acao no ciclo de vida com o risco correspondente. Antes,
   registrar a acao dependia de o DBA lembrar de digitar `CASO.py --acao` depois
   - no meio de um incidente, e a primeira coisa que nao acontece.

Este modulo nao conecta em banco: ele conduz, mostra e registra. Quem executa a
VERIFICACAO e o `executor.py`, chamado pelo CLI/painel - e so ela: passo que
muda o ambiente continua atras da Matriz de Risco, executado por gente. A
conexao mora na borda (GUIA.py), como em DETALHAR_SQL.py e JANELA.py, para que
a conducao e o estado sigam testaveis sem banco do outro lado.
"""
from __future__ import annotations

import os

from . import caso as caso_lifecycle
from . import kb, raciocinio
from . import saida as leitor_saida

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(BASE_DIR, "output", "troubleshooting")

# Risco do passo (vocabulario da KB) -> risco da acao (vocabulario do ciclo de
# vida). Sao escalas diferentes porque nasceram para publicos diferentes: a KB
# descreve o QUE o comando faz; o caso registra QUANTO doi se der errado.
RISCO_KB_PARA_CASO = {
    "leitura": "nenhum",
    "mudanca": "medio",
    "destrutivo": "alto",
}

# A arvore de decisao MUDOU DE CASA: as causas candidatas, os sinais que as
# confirmam, os que as refutam e as tags de playbook agora vivem em
# `troubleshoot/raciocinio.py`. Aqui ficou so a conducao.
#
# O motivo de nao manter as duas: eram a MESMA informacao em dois lugares. Uma
# marca nova de I/O escrita so num deles produziria um guia que sugere o
# playbook certo e um motor que descarta a hipotese - discordando dentro do
# mesmo caso, na mesma tela.

# Checklist pos-acao (o do prompt). So nasce depois de acao que MUDA algo:
# criar pendencia depois de um SELECT seria ruido, e ruido treina o time a
# ignorar a lista inteira.
POS_ACAO = [
    ("recoletar", "Recoletar e confirmar que o sintoma cessou",
     "Sem re-coleta, 'resolvido' e opiniao. O mesmo comando da coleta inicial "
     "serve: o que importa e comparar com a foto anterior."),
    ("alertlog", "Conferir o alert log em busca de erro NOVO",
     "Acao corretiva costuma trocar um erro por outro. O alert log e onde o "
     "novo aparece primeiro."),
    ("ha", "Validar replicacao / HA (Data Guard, GoldenGate, RAC)",
     "Vale mesmo quando a acao nao teve relacao aparente com HA: o gap so "
     "aparece depois, e ai ninguem liga uma coisa a outra."),
    ("redija", "Redigir o caso com os horarios reais",
     "O 'redija' le o que foi registrado. Se a acao nao entrou no caso, ela "
     "nao existe para o chamado nem para a auditoria."),
]


# ── leitura dos achados ───────────────────────────────────────
def achados_do_caso(caso: str, out_dir: str = "", cases_dir: str = "") -> list:
    """O que se sabe do caso AGORA: a coleta inicial mais o que foi observado.

    A coleta e a foto do momento em que se abriu o caso. O que o DBA cola no
    passo a passo e o que esta acontecendo enquanto se conduz - e por isso vem
    DEPOIS na lista, para que um achado observado agora possa refutar uma
    hipotese que a foto antiga sustentava.

    Le a coleta pelo `raciocinio` para que exista UM lugar que saiba onde o
    arquivo de achados mora e como ele pode estar quebrado. Caso ainda nao
    analisado devolve so o observado - o guia funciona sem analise nenhuma,
    conduzido apenas pelo que se cola nele.
    """
    return raciocinio.achados(caso, out_dir) + observados(caso, cases_dir)


def ramo_por_achados(registros: list) -> dict:
    """A hipotese que a evidencia sustenta, no formato que a tela consome.

    Delega ao motor de `raciocinio`: aqui nao ha regra propria, so a traducao
    para o vocabulario do guia ("ramo"). Alem da hipotese vencedora vem o que
    foi DESCARTADO - o descarte e o que impede o proximo plantao de refazer o
    caminho ja andado.

    Devolve {} quando nada aponta para lugar nenhum. Isso e resposta legitima e
    esta no prompt: "sem waits mas lento" manda olhar rede e aplicacao, nao
    inventar um gargalo dentro do banco.
    """
    lista = raciocinio.avaliar(registros)
    vencedora = next((h for h in lista
                      if h["veredito"] in ("sustentada", "possivel")), None)
    if not vencedora:
        return {}
    return {
        "ramo": vencedora["id"],
        "rotulo": vencedora["rotulo"],
        "pontos": vencedora["pontos"],
        "veredito": vencedora["veredito"],
        "explica": vencedora["explica"],
        "decide": vencedora["decide"],
        "coletar": vencedora["coletar"],
        "evidencia": [p["achado"] for p in vencedora["a_favor"]][:3],
        "descartadas": [{"rotulo": h["rotulo"],
                         "porque": (h["contra"][0]["marca"] if h["contra"] else "")}
                        for h in lista if h["veredito"] == "refutada"],
        # Sintoma nao e descarte: o sinal e real, so tem dono. Separar os dois
        # evita que a tela diga "I/O descartado" num caso em que o I/O esta
        # acontecendo de verdade - por causa do plano.
        "sintomas": [{"rotulo": h["rotulo"], "causa": h.get("causa", "")}
                     for h in lista if h["veredito"] == "consequencia"],
        "em_aberto": [h["rotulo"] for h in lista
                      if h["veredito"] in ("sustentada", "possivel")
                      and h["id"] != vencedora["id"]][:3],
    }


# Pesos da sugestao de playbook. O ramo pesa MAIS que o casamento por secao, e
# isso foi aprendido na primeira execucao real: um caso claramente de I/O
# ('User I/O concentra 50%') sugeria o playbook de LOCKS em primeiro lugar,
# porque a secao 'Distribuicao de esperas (ASH)' esta declarada em cinco
# playbooks diferentes - ela diz onde o sintoma apareceu, nao qual e o problema.
# O ramo, esse sim, sai do conteudo do achado.
PESO_RAMO = 10
PESO_RAMO_SECUNDARIO = 7  # casou por tag vizinha, nao pela tag central do ramo
PESO_SECAO_GRAVE = 4      # secao de um achado CRITICO
PESO_SECAO = 1            # secao de qualquer achado


def _playbooks_por_tag(ramo: str, kb_dir: str = "") -> list:
    """Playbooks cujas tags declaradas casam com as da hipotese, por token EXATO.

    Nao usa `kb.buscar`: aquela busca e textual e casa pedaco de palavra, o que
    e correto para quem digita na caixa de pesquisa e errado aqui. A tag 'io'
    casava dentro de 'bloqueio' e o roteiro de um caso de I/O comecava pelo
    playbook de locks.
    """
    alvo = list(raciocinio.por_id(ramo).get("tags") or ())
    if not alvo:
        return []
    central, vizinhas = alvo[0], set(alvo[1:])
    out = []
    for pb in kb.carregar(kb_dir):
        tags = {str(t).strip().lower() for t in (pb.get("tags") or [])}
        if central in tags:
            out.append((pb, PESO_RAMO))
        elif tags & vizinhas:
            # 'plano' pertence ao ramo de I/O (plano ruim le demais), mas quem
            # trata I/O de frente e quem declara 'io'. Sem esta diferenca, um
            # empate mandava o roteiro comecar pelo playbook de espaco.
            out.append((pb, PESO_RAMO_SECUNDARIO))
    return out


def playbooks_sugeridos(caso: str, out_dir: str = "", kb_dir: str = "") -> list:
    """Playbooks candidatos para o caso, do mais provavel ao menos.

    Combina duas fontes que ja existiam soltas: o ramo apurado no CONTEUDO dos
    achados e o casamento por secao (`kb.sugerir_para_secao`). Quando as duas
    discordam, o ramo vence - ver o comentario dos pesos, logo acima.
    """
    registros = achados_do_caso(caso, out_dir)
    pontos: dict = {}
    porques: dict = {}
    titulos: dict = {}

    def _pontuar(pb, peso, porque):
        pid = pb.get("id")
        if not pid:
            return
        titulos[pid] = pb.get("titulo", "")
        pontos[pid] = pontos.get(pid, 0) + peso
        # O motivo mostrado e o de maior peso: dizer "trata a secao X" quando o
        # playbook foi escolhido pelo ramo esconde o raciocinio de verdade.
        if peso >= porques.get(pid, (0, ""))[0]:
            porques[pid] = (peso, porque)

    for r in registros:
        grave = (r.get("severity") or "").lower() == "crit"
        for pb in kb.sugerir_para_secao(r.get("section") or "", kb_dir):
            _pontuar(pb, PESO_SECAO_GRAVE if grave else PESO_SECAO,
                     f"trata a secao '{r.get('section')}'")

    escolha = ramo_por_achados(registros)
    if escolha:
        for pb, peso in _playbooks_por_tag(escolha["ramo"], kb_dir):
            _pontuar(pb, peso, f"ramo {escolha['rotulo']}")

    # Empate desempata pela ORDEM curada da KB, nao pelo alfabeto: a ordem dos
    # playbooks foi pensada por quem os escreveu.
    curada = {pb.get("id"): pb.get("ordem", 99) for pb in kb.carregar(kb_dir)}
    ordenado = sorted(pontos, key=lambda p: (-pontos[p], curada.get(p, 99), p))
    return [{"id": p, "titulo": titulos[p], "porque": porques[p][1],
             "pontos": pontos[p]} for p in ordenado]


# Palavras que aparecem em quase todo alerta e nao distinguem playbook nenhum.
# Sem elas a busca dilui: "Oracle Dataguard - GAP na replicacao de dados" trazia
# corrupcao_bloco e clusterware_gi na frente do dataguard_gap, porque 'oracle' e
# 'dados' casam em todo lugar.
# Como o texto do alerta nomeia cada plataforma. A ordem importa: 'sqlserver'
# antes de 'sql' para nao classificar "SQL lento" como MSSQL, e 'postgres' antes
# de 'pg'. O valor e o `plataforma` do playbook (ausente = oracle).
_PLATAFORMA_NO_TEXTO = {
    "db2": "db2", "sql server": "mssql", "sqlserver": "mssql",
    "mssql": "mssql", "postgresql": "postgres", "postgres": "postgres",
    "mysql": "mysql", "mariadb": "mysql", "redis": "redis",
    "mongodb": "mongodb", "mongo": "mongodb", "oracle": "oracle",
}

_GENERICAS = {
    "oracle", "dados", "banco", "base", "erro", "erros", "alerta", "alarme",
    "problema", "incidente", "falha", "servidor", "host", "instancia",
    "database", "prod", "producao", "homologacao", "critico", "critica",
    "alto", "alta", "high", "warning", "critical", "para", "pela", "pelo",
    "esta", "sobre", "mais", "menos", "muito", "sendo",
}


def playbooks_por_texto(texto: str, kb_dir: str = "") -> list:
    """Playbooks que o TEXTO menciona - pista de partida, nao diagnostico.

    Existe para o minuto zero do caso, quando `playbooks_sugeridos` nao tem o
    que dizer: ele le a evidencia coletada, e a coleta pede senha e vem depois.
    Era justamente a hora de maior necessidade de direcao e a de menor oferta -
    a saida mandava rodar um `python -c` para listar os 39 playbooks a mao.

    **O valor aqui e DESCOBERTA, nao conclusao.** Quem nao sabe que existe um
    playbook de roll-forward incremental nunca vai digitar esse nome; ver o
    titulo na lista e o que torna a opcao possivel. A conclusao continua vindo
    da medicao - todo playbook comeca por passo read-only.

    E por isso o resultado sai rotulado como pista: nos 5 casos de 22 a 24/09
    o texto do alerta descrevia OUTRA coisa em 4 ("reconstruir Data Guard" era
    roll-forward, "log a 100%" eram 38 KB, "datafile em 94%" era diskgroup).
    Tratar isto como diagnostico reproduziria exatamente esse erro.

    Voto por token em vez de busca pela frase inteira: playbook que casa mais
    palavras do alerta vem primeiro.

    A PLATAFORMA citada no texto desempata antes do voto. Sem isso um alerta de
    Db2 trazia o playbook de undo do Oracle empatado com o de log do Db2 - os
    dois casam 'transaction', e um deles nao serve para aquele banco. A deteccao
    e feita no texto cru porque 'db2' tem 3 letras e cai no filtro de token.
    """
    import re
    baixo = (texto or "").lower()
    plat = ""
    for alias, pid in _PLATAFORMA_NO_TEXTO.items():
        if alias in baixo:
            plat = pid
            break
    tokens = {t for t in re.split(r"[^A-Za-z0-9]+", baixo)
              if len(t) >= 4 and t not in _GENERICAS}
    votos: dict = {}
    titulos: dict = {}
    casou: dict = {}
    plat_de: dict = {}
    for t in sorted(tokens):
        for pb in kb.buscar(t, kb_dir):
            pid = pb.get("id")
            if not pid:
                continue
            titulos[pid] = pb.get("titulo", "")
            plat_de[pid] = kb.plataforma_de(pb)
            votos[pid] = votos.get(pid, 0) + 1
            casou.setdefault(pid, []).append(t)
    curada = {pb.get("id"): pb.get("ordem", 99) for pb in kb.carregar(kb_dir)}
    # 0 antes de 1: plataforma do alerta primeiro. Desempate final pela ordem
    # curada da KB, nao pelo alfabeto.
    ordenado = sorted(votos, key=lambda p: (
        0 if (plat and plat_de.get(p) == plat) else 1,
        -votos[p], curada.get(p, 99), p))
    return [{"id": p, "titulo": titulos[p], "pontos": votos[p],
             "plataforma": plat_de.get(p, ""),
             "porque": "o texto menciona: " + ", ".join(casou[p][:4])}
            for p in ordenado]


# ── estado do roteiro ─────────────────────────────────────────
def estado(caso: str, cases_dir: str = "") -> dict:
    return (caso_lifecycle.ler(caso, cases_dir) or {}).get("guia") or {}


def _mutar_guia(caso: str, cases_dir: str, fn) -> dict:
    def _f(d):
        g = d.setdefault("guia", {})
        fn(g)

    caso_lifecycle._mutar(caso, cases_dir, _f)
    return estado(caso, cases_dir)


def iniciar(caso: str, playbook_id: str, cases_dir: str = "",
            kb_dir: str = "") -> dict:
    """Comeca (ou recomeca) o roteiro de um playbook no caso."""
    pb = kb.por_id(playbook_id, kb_dir)
    if not pb:
        raise ValueError(f"playbook desconhecido: {playbook_id}")

    def _f(g):
        # A saida colada e EVIDENCIA do caso, nao do roteiro: trocar de
        # playbook no meio da investigacao nao pode apagar o que ja foi
        # observado no banco. As pendencias seguem a mesma logica - elas
        # nasceram de uma acao que ja mexeu no ambiente.
        preservar = {k: g.get(k) for k in ("saidas", "pendencias")
                     if g.get(k)}
        g.clear()
        g["playbook"] = playbook_id
        g["titulo"] = pb.get("titulo", "")
        g["passo"] = 0
        g["iniciado_em"] = caso_lifecycle._agora()
        g["historico"] = []
        g["pendencias"] = []
        g.update(preservar)

    return _mutar_guia(caso, cases_dir, _f)


def passo_atual(caso: str, cases_dir: str = "", kb_dir: str = "") -> dict:
    """O passo em que o caso esta, ja com risco e matriz quando couber.

    Devolve {} quando nao ha roteiro em curso ou quando ele terminou - o fim do
    roteiro nao e erro, e o momento de redigir.
    """
    g = estado(caso, cases_dir)
    if not g.get("playbook"):
        return {}
    pb = kb.por_id(g["playbook"], kb_dir)
    passos = pb.get("passos") or []
    i = int(g.get("passo") or 0)
    if i >= len(passos):
        return {}
    p = dict(passos[i])
    p["indice"] = i
    p["total"] = len(passos)
    p["playbook"] = g["playbook"]
    p["playbook_titulo"] = pb.get("titulo", "")
    p["matriz_risco"] = matriz_risco(p)
    return p


def _registrar(g: dict, indice: int, titulo: str, resultado: str,
               nota: str = "") -> None:
    g.setdefault("historico", []).append({
        "passo": indice, "titulo": titulo, "resultado": resultado,
        "nota": nota, "em": caso_lifecycle._agora()})


def avancar(caso: str, nota: str = "", cases_dir: str = "",
            kb_dir: str = "") -> dict:
    """Conclui o passo atual e vai para o proximo."""
    atual = passo_atual(caso, cases_dir, kb_dir)
    if not atual:
        raise ValueError("nenhum passo em curso")

    def _f(g):
        _registrar(g, atual["indice"], atual.get("titulo", ""), "concluido", nota)
        g["passo"] = int(g.get("passo") or 0) + 1

    return _mutar_guia(caso, cases_dir, _f)


def pular(caso: str, motivo: str = "", cases_dir: str = "",
          kb_dir: str = "") -> dict:
    """Pula o passo atual, registrando o motivo.

    Pular sem motivo e permitido mas fica marcado: o valor do roteiro esta em
    saber, semanas depois, o que NAO foi verificado.
    """
    atual = passo_atual(caso, cases_dir, kb_dir)
    if not atual:
        raise ValueError("nenhum passo em curso")

    def _f(g):
        _registrar(g, atual["indice"], atual.get("titulo", ""), "pulado",
                   motivo or "sem motivo informado")
        g["passo"] = int(g.get("passo") or 0) + 1

    return _mutar_guia(caso, cases_dir, _f)


def encerrar(caso: str, cases_dir: str = "") -> dict:
    """Fecha o roteiro (o playbook continua no historico do caso)."""
    def _f(g):
        g["encerrado_em"] = caso_lifecycle._agora()
        g["passo"] = 10 ** 6      # alem do ultimo: nao ha passo atual

    return _mutar_guia(caso, cases_dir, _f)


def progresso(caso: str, cases_dir: str = "", kb_dir: str = "") -> dict:
    """Onde o caso esta no roteiro, para a tela e para o relatorio."""
    g = estado(caso, cases_dir)
    if not g.get("playbook"):
        return {}
    pb = kb.por_id(g["playbook"], kb_dir)
    total = len(pb.get("passos") or [])
    hist = g.get("historico") or []
    feitos = min(len(hist), total)
    return {
        "playbook": g["playbook"],
        "titulo": g.get("titulo") or pb.get("titulo", ""),
        "passo": int(g.get("passo") or 0),
        "total": total,
        "concluidos": sum(1 for h in hist if h["resultado"] == "concluido"),
        "pulados": sum(1 for h in hist if h["resultado"] == "pulado"),
        "pct": round(100.0 * feitos / total) if total else 0,
        "encerrado": bool(g.get("encerrado_em")) or feitos >= total,
        "pendencias_abertas": len(pendencias_abertas(caso, cases_dir)),
    }


# ── Matriz de Risco ───────────────────────────────────────────
def matriz_risco(passo: dict) -> dict:
    """Bloco impacto/rollback/aprovacao de um passo, ou {} se for leitura.

    O `{}` para leitura e proposital: exibir "risco: nenhum" em todo SELECT
    dessensibiliza para o aviso que importa. O alerta so vale enquanto for raro.
    """
    risco = (passo or {}).get("risco") or "leitura"
    if risco == "leitura":
        return {}
    destrutivo = risco == "destrutivo"
    return {
        "risco": risco,
        "risco_caso": RISCO_KB_PARA_CASO.get(risco, "medio"),
        "impacto": (passo.get("impacto")
                    or ("Pode causar perda de dado ou indisponibilidade."
                        if destrutivo else
                        "Altera configuracao ou estado da instancia.")),
        "antes_de_executar": passo.get("antes_de_executar", ""),
        "rollback": (passo.get("rollback")
                     or ("SEM ROLLBACK AUTOMATICO: a volta depende de backup / "
                         "RESTORE. Confirme que existe backup valido e testado "
                         "ANTES de executar." if destrutivo else
                         "Reverter significa restaurar o valor anterior - "
                         "anote-o antes de mudar.")),
        "exige_aprovacao": destrutivo,
        "exige_janela": destrutivo,
    }


def confirmar_acao(caso: str, descricao: str = "", cases_dir: str = "",
                   kb_dir: str = "") -> dict:
    """Registra a execucao do passo atual como ACAO do caso, com o risco certo.

    E aqui que o guia paga por si: a acao entra no ciclo de vida (e portanto na
    linha do tempo, no relatorio e no consolidado do dia) sem depender de o DBA
    lembrar de rodar `CASO.py --acao` no meio do incidente. Passo que muda algo
    tambem abre o checklist pos-acao.
    """
    atual = passo_atual(caso, cases_dir, kb_dir)
    if not atual:
        raise ValueError("nenhum passo em curso")
    risco_kb = atual.get("risco") or "leitura"
    risco = RISCO_KB_PARA_CASO.get(risco_kb, "nenhum")
    texto = (descricao or "").strip() or (
        f"[guia {atual['playbook']} passo {atual['indice'] + 1}] "
        f"{atual.get('titulo', '')}")
    caso_lifecycle.registrar_acao(caso, texto, risco, cases_dir)
    if risco_kb != "leitura":
        abrir_pos_acao(caso, texto, cases_dir)
    return {"acao": texto, "risco": risco,
            "pendencias": pendencias_abertas(caso, cases_dir)}


# ── Saida observada: o retorno do comando vira evidencia ──────
# Saida de SQL*Plus e colada inteira, com dezenas de linhas. Guardar tudo
# incharia o meta.json que viaja junto do caso; guardar nada tiraria o lastro
# do achado. O corte fica no que cabe numa conferencia posterior.
_MAX_SAIDA = 4000

# O elo que faltava. O guiado dizia o que rodar e o que olhar; o resultado
# ficava so na tela do terminal e na cabeca de quem conduzia. Colando a saida
# aqui, ela entra no MESMO conjunto de achados que o motor pontua - e a partir
# do passo seguinte a conducao responde ao que esta acontecendo agora, nao ao
# que a coleta viu quando o caso foi aberto.
def observados(caso: str, cases_dir: str = "") -> list:
    """Achados das saidas coladas, sem repetir o mesmo sinal.

    A deduplicacao nao e detalhe de arrumacao: sem ela, colar a MESMA saida
    duas vezes fazia a hipotese subir de 'possivel' para 'sustentada'. O
    veredito passaria a depender de quantas vezes alguem clicou, e nao da
    evidencia - que e a definicao de pontuacao inflada.

    Fica a ocorrencia MAIS RECENTE de cada achado: reobservar depois de uma
    acao e o jeito de dizer "continua acontecendo", e e essa leitura que tem
    de valer.
    """
    ultimo: dict = {}
    for reg in estado(caso, cases_dir).get("saidas") or []:
        for a in reg.get("achados") or []:
            ultimo[a.get("title", "")] = a
    return list(ultimo.values())


def registrar_saida(caso: str, texto: str, cases_dir: str = "",
                    kb_dir: str = "", out_dir: str = "",
                    origem: str = "colado", extra: dict | None = None) -> dict:
    """Interpreta a saida colada, guarda no caso e diz O QUE MUDOU no raciocinio.

    `origem` separa o que o framework EXECUTOU do que alguem colou. Os dois
    valem como evidencia e passam pelos mesmos leitores, mas a auditoria do caso
    precisa distinguir: "o framework leu isto do banco as 14:32" e verificavel,
    "alguem colou isto" depende de onde a pessoa copiou. Sem o campo, seis meses
    depois nao ha como saber qual das duas coisas aconteceu.

    O "o que mudou" e a parte que importa para quem conduz: ver a lista de
    hipoteses de novo nao ajuda, mas saber que *a hipotese de bloqueio caiu
    porque a consulta voltou vazia* e exatamente o proximo passo do raciocinio.

    A saida bruta e guardada junto (limitada em tamanho): sem ela, o achado
    extraido seria uma afirmacao sem lastro, e o valor do registro e poder
    conferir depois de onde a conclusao saiu.
    """
    atual = passo_atual(caso, cases_dir, kb_dir) or {}
    # A pergunta e o que permite concluir de uma saida VAZIA. Sem ela, 'no rows
    # selected' nao refuta nada - nao se sabe o que a consulta procurava.
    pergunta = " ".join(str(atual.get(c, "")) for c in
                        ("titulo", "objetivo", "olhar", "comando"))

    lido = leitor_saida.interpretar(texto, pergunta)

    antes = _resumo_hipoteses(caso, out_dir, cases_dir)

    def _f(g):
        reg = {
            "passo": atual.get("indice", -1),
            "titulo": atual.get("titulo", ""),
            "em": caso_lifecycle._agora(),
            "texto": (texto or "")[:_MAX_SAIDA],
            "achados": lido["achados"],
            "leitores": lido["leitores"],
            "origem": origem,
        }
        reg.update(extra or {})
        g.setdefault("saidas", []).append(reg)

    _mutar_guia(caso, cases_dir, _f)

    depois = _resumo_hipoteses(caso, out_dir, cases_dir)
    return {
        "achados": lido["achados"],
        "leitores": lido["leitores"],
        "aviso": lido["aviso"],
        "origem": origem,
        "mudou": _diferenca(antes, depois),
        "hipoteses": raciocinio.avaliar(achados_do_caso(caso, out_dir,
                                                        cases_dir)),
    }


# ── Executar a verificacao no proprio framework ───────────────
def preparar_execucao(caso: str, cases_dir: str = "", kb_dir: str = "",
                      out_dir: str = "") -> dict:
    """O passo atual roda no framework? Com quais statements e que parametros.

    Junta as duas metades que a tela precisa numa chamada: o veredito do
    `executor` sobre o comando e o valor SUGERIDO de cada parametro, tirado dos
    achados que o caso ja tem. Deixar isso para o chamador faria painel e CLI
    repetirem a mesma composicao - e discordarem no dia em que um dos dois
    mudasse.
    """
    from . import executor

    atual = passo_atual(caso, cases_dir, kb_dir)
    if not atual:
        return {"pode": False, "motivo": "nenhum passo em curso",
                "statements": [], "recusados": [], "parametros": [],
                "sugestoes": {}, "passo": {}}
    v = executor.preparar(atual)
    v["sugestoes"] = executor.sugerir_parametros(
        v.get("parametros") or [], achados_do_caso(caso, out_dir, cases_dir))
    v["passo"] = {"indice": atual["indice"], "titulo": atual.get("titulo", ""),
                  "total": atual["total"], "playbook": atual["playbook"]}
    return v


def passos_executaveis(caso: str, cases_dir: str = "", kb_dir: str = "",
                       out_dir: str = "") -> list:
    """Os passos de VERIFICACAO do playbook em curso que o framework roda.

    Serve ao "executar o playbook todo": num checklist (o `checklist_diario` e
    so verificacao) parar em cada passo para clicar nao agrega nada.

    Passo com `&parametro` ENTRA no lote quando a evidencia do caso responde o
    parametro inteiro - e sai quando nao responde. A primeira versao excluia
    todos, por precaucao, e a medicao mostrou o preco: no `sql_lento` - o
    playbook de tuning, o mais usado - isso deixava **1 de 7** passos no lote,
    porque seis pedem `&sql_id`. E o SQL_ID nao e adivinhacao: sai do achado de
    regressao que o proprio caso ja tem (`nextstep.find_sql_id`). Continuam
    fora os que dependem de SID/instancia, que descrevem uma sessao viva e nao
    podem vir de uma coleta de meia hora atras.

    Cada passo devolve `valores` - o que foi usado - para que a tela e o log
    digam SOBRE QUAL objeto a verificacao rodou. Lote que nao diz isso e pior
    que lote nenhum: o DBA recebe sete respostas sem saber a pergunta.
    """
    from . import executor

    g = estado(caso, cases_dir)
    if not g.get("playbook"):
        return []
    pb = kb.por_id(g["playbook"], kb_dir)
    achados = achados_do_caso(caso, out_dir, cases_dir)
    out = []
    for i, p in enumerate(pb.get("passos") or []):
        v = executor.preparar(p)
        if not v["pode"]:
            continue
        valores = {}
        if v["parametros"]:
            sug = executor.sugerir_parametros(v["parametros"], achados)
            if not all(str(sug.get(n) or "").strip() for n in v["parametros"]):
                continue          # a evidencia nao responde: fica para o clique
            valores = {n: sug[n] for n in v["parametros"]}
        try:
            sts = [executor.aplicar_parametros(s, valores) if valores else s
                   for s in v["statements"]]
        except ValueError:
            continue              # valor sugerido que nao passa na validacao
        out.append({"indice": i, "titulo": p.get("titulo", ""),
                    "statements": sts, "valores": valores})
    return out


def _resumo_hipoteses(caso: str, out_dir: str, cases_dir: str) -> dict:
    return {h["id"]: h["veredito"]
            for h in raciocinio.avaliar(achados_do_caso(caso, out_dir,
                                                        cases_dir))}


def _diferenca(antes: dict, depois: dict) -> list:
    """O que a saida colada mudou no raciocinio, em linguagem de conducao."""
    out = []
    for hid, novo in depois.items():
        velho = antes.get(hid, "sem_evidencia")
        if velho == novo:
            continue
        rot = raciocinio.por_id(hid).get("rotulo", hid)
        out.append({"id": hid, "rotulo": rot, "de": velho, "para": novo,
                    "texto": _FRASE.get(novo, "mudou para " + novo).format(rot)})
    # Quem foi descartado ou passou a sustentar vem primeiro: e o que muda o
    # proximo passo de quem conduz.
    ordem = {"refutada": 0, "sustentada": 1, "consequencia": 2, "possivel": 3}
    out.sort(key=lambda m: ordem.get(m["para"], 9))
    return out


_FRASE = {
    "refutada": "{} foi DESCARTADA pela saida observada",
    "sustentada": "{} agora esta SUSTENTADA pela saida observada",
    "consequencia": "{} passou a ser sintoma de outra causa",
    "possivel": "{} ganhou evidencia e entrou como possivel",
    "sem_evidencia": "{} perdeu a evidencia que tinha",
}


def saidas(caso: str, cases_dir: str = "") -> list:
    """As saidas coladas, para a tela e para a auditoria do caso."""
    return list(estado(caso, cases_dir).get("saidas") or [])


# ── Checklist pos-acao ────────────────────────────────────────
def abrir_pos_acao(caso: str, origem: str = "", cases_dir: str = "") -> list:
    """Cria as pendencias do checklist pos-acao (sem duplicar as abertas)."""
    def _f(g):
        pend = g.setdefault("pendencias", [])
        abertos = {p["id"] for p in pend if not p.get("feito_em")}
        for pid, texto, porque in POS_ACAO:
            if pid in abertos:
                continue
            pend.append({"id": pid, "texto": texto, "porque": porque,
                         "origem": origem, "aberta_em": caso_lifecycle._agora(),
                         "feito_em": ""})

    _mutar_guia(caso, cases_dir, _f)
    return pendencias_abertas(caso, cases_dir)


def pendencias(caso: str, cases_dir: str = "") -> list:
    return list(estado(caso, cases_dir).get("pendencias") or [])


def pendencias_abertas(caso: str, cases_dir: str = "") -> list:
    return [p for p in pendencias(caso, cases_dir) if not p.get("feito_em")]


def concluir_pendencia(caso: str, pid: str, nota: str = "",
                       cases_dir: str = "") -> list:
    achou = [False]

    def _f(g):
        for p in g.get("pendencias") or []:
            if p["id"] == pid and not p.get("feito_em"):
                p["feito_em"] = caso_lifecycle._agora()
                if nota:
                    p["nota"] = nota
                achou[0] = True

    _mutar_guia(caso, cases_dir, _f)
    if not achou[0]:
        raise ValueError(f"pendencia inexistente ou ja concluida: {pid}")
    return pendencias_abertas(caso, cases_dir)
