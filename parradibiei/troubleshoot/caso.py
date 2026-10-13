"""
Ciclo de vida do caso de troubleshooting (hipoteses, acoes, status, RCA).

Por que existe: o framework sempre soube registrar a EVIDENCIA (o que o banco
respondeu) e nunca o RACIOCINIO (que hipotese estava sendo testada, que acao foi
executada, com que risco). O resultado e que todo relatorio saia com "A
confirmar com os dados coletados" no campo de hipotese, mesmo quando o DBA tinha
a hipotese clara desde o inicio.

O encaixe ja existia sem uso: build_incident_md le 'hipotese_inicial',
'hipotese_atual', 'timeline', 'impacto', 'prevencao', 'status' e 'escalado' de
meta.json, caindo em texto genérico quando faltam. Este modulo e quem preenche.

Onde grava: troubleshooting/<caso>/meta.json - arquivo, nao localStorage. Isso e
o ponto: o histórico acompanha o caso na pasta, vai para o SharePoint junto com
as evidencias e sobrevive a limpeza de cache do navegador.

Toda escrita PRESERVA as chaves existentes (title, kind, conn, dsn...), porque o
mesmo arquivo e escrito pela coleta.
"""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASES_DIR = os.path.join(BASE_DIR, "troubleshooting")
OUTPUT_DIR = os.path.join(BASE_DIR, "output", "troubleshooting")

# Os mesmos quatro niveis do fluxo DBA Master. 'alto' e acao critica (kill,
# flush, rebuild, shrink, drop) e por isso aparece destacada no relatorio e no
# consolidado: e a informacao que alguem vai procurar se algo der errado depois.
RISCOS = ("nenhum", "baixo", "medio", "alto")
STATUS_ABERTOS = ("Investigando", "Escalado", "Aguardando")


def _agora() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def caminho_meta(caso: str, cases_dir: str = "") -> str:
    return os.path.join(cases_dir or CASES_DIR, caso, "meta.json")


def ler(caso: str, cases_dir: str = "") -> dict:
    """meta.json do caso, ou {} se nao existir/estiver corrompido."""
    p = caminho_meta(caso, cases_dir)
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh) or {}
    except Exception:
        return {}


def gravar(caso: str, dados: dict, cases_dir: str = "") -> str:
    p = caminho_meta(caso, cases_dir)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(dados, fh, ensure_ascii=False, indent=2)
    return p


def _mutar(caso: str, cases_dir: str, fn) -> dict:
    """Le, aplica fn e grava. Garante criado_em/status em qualquer operacao."""
    d = ler(caso, cases_dir)
    d.setdefault("criado_em", _agora())
    d.setdefault("status", "Investigando")
    fn(d)
    d["atualizado_em"] = _agora()
    gravar(caso, d, cases_dir)
    return d


# ── hipoteses ─────────────────────────────────────────────────
def nova_hipotese(caso: str, texto: str, motivo: str = "",
                  cases_dir: str = "", origem: str = "") -> dict:
    """Define a hipotese inicial ou avanca para a proxima (comando 'proximo').

    A hipotese anterior nao e apagada: vai para o historico com o motivo do
    descarte. Esse rastro e o que permite, semanas depois, saber o que JA foi
    testado e nao repetir o mesmo caminho.

    `origem` registra DE ONDE a hipotese veio, pelo mesmo motivo do
    `causa_origem`: "motor:plano" e o DBA CONFIRMANDO o que o framework
    propos; vazio e ele escrevendo a propria. Semanas depois as duas ficam
    indistinguiveis no texto, e a diferenca e justamente o que permitiria
    medir se o motor acerta - hoje um numero desconhecido, porque de 27 casos
    da frota so 1 tem causa registrada.

    A origem entra no HISTORICO junto com a hipotese descartada, e nao so na
    atual: saber que o motor propos algo que depois caiu vale tanto quanto
    saber que ele acertou.
    """
    texto = (texto or "").strip()
    if not texto:
        raise ValueError("hipotese vazia")

    def _f(d):
        atual = (d.get("hipotese_atual") or "").strip()
        hist = d.setdefault("hipoteses", [])
        if not d.get("hipotese_inicial"):
            d["hipotese_inicial"] = texto
        elif atual and atual != texto:
            hist.append({"texto": atual, "descartada_em": _agora(),
                         "motivo": motivo or "nao confirmada pelos dados",
                         "desde": d.get("hipotese_desde", ""),
                         "origem": d.get("hipotese_origem", "")})
        d["hipotese_atual"] = texto
        # A origem acompanha a hipotese ATUAL. Escrever por cima quando a
        # hipotese muda e o certo: guardar a origem antiga faria a nova
        # parecer vinda do motor quando foi digitada a mao.
        if atual != texto:
            d["hipotese_origem"] = origem or ""
        # Desde quando esta hipotese esta em teste. Sem isso a linha do tempo
        # nao sabe onde encaixar a hipotese atual entre as acoes.
        if atual != texto:
            d["hipotese_desde"] = _agora()

    return _mutar(caso, cases_dir, _f)


# ── acoes ─────────────────────────────────────────────────────
def registrar_acao(caso: str, descricao: str, risco: str = "nenhum",
                   cases_dir: str = "") -> dict:
    descricao = (descricao or "").strip()
    if not descricao:
        raise ValueError("descricao da acao vazia")
    risco = (risco or "nenhum").strip().lower()
    if risco not in RISCOS:
        raise ValueError(f"risco invalido: {risco} (use {', '.join(RISCOS)})")

    def _f(d):
        d.setdefault("acoes", []).append(
            {"descricao": descricao, "risco": risco, "em": _agora()})
        # 'riscos' e o campo que o relatorio ja imprime em "Riscos mitigados".
        # Mantido derivado aqui para o relatorio nao precisar saber da lista.
        criticas = [a for a in d["acoes"] if a.get("risco") == "alto"]
        if criticas:
            d["riscos"] = (f"{len(criticas)} acao(oes) de risco ALTO executadas: "
                           + "; ".join(a["descricao"] for a in criticas[:3])
                           + ". Executadas com aviso de impacto e rollback planejado.")

    return _mutar(caso, cases_dir, _f)


# ── status / RCA ──────────────────────────────────────────────
# `causa_origem` registra DE ONDE veio a causa raiz (ex.: "proposta:bloqueio-0").
# Sem isso, causa escolhida numa caixinha do painel fica indistinguivel de causa
# digitada a mao - e, semanas depois, ninguem sabe se aquilo foi conclusao do DBA
# ou sugestao aceita. Nao aparece no chamado: e rastro de auditoria.
_CAMPOS_LIVRES = ("status", "timeline", "impacto", "prevencao", "evento",
                  "hostname", "causa_raiz", "solucao", "escalado",
                  "causa_origem", "familia")


def definir(caso: str, cases_dir: str = "", **campos) -> dict:
    """Grava campos livres do caso (status, timeline, impacto, prevencao...)."""
    invalidos = set(campos) - set(_CAMPOS_LIVRES)
    if invalidos:
        raise ValueError(f"campo(s) desconhecido(s): {', '.join(sorted(invalidos))}")

    def _f(d):
        for k, v in campos.items():
            if v is not None and str(v).strip() != "":
                d[k] = v

    return _mutar(caso, cases_dir, _f)


def escalar(caso: str, para: str = "", cases_dir: str = "") -> dict:
    def _f(d):
        d["escalado"] = f"Sim - {para}" if para else "Sim"
        d["status"] = "Escalado"
        d["escalado_em"] = _agora()

    return _mutar(caso, cases_dir, _f)


def resolver(caso: str, causa: str = "", solucao: str = "",
             prevencao: str = "", cases_dir: str = "") -> dict:
    def _f(d):
        d["status"] = "Resolvido"
        d["resolvido_em"] = _agora()
        if causa:
            d["causa_raiz"] = causa
        if solucao:
            d["solucao"] = solucao
        if prevencao:
            d["prevencao"] = prevencao

    return _mutar(caso, cases_dir, _f)


# ── leitura ───────────────────────────────────────────────────
def criticidade(caso: str, cases_dir: str = "") -> str:
    """'PRODUCAO', 'HOMOLOGACAO' ou '?' do ambiente do caso.

    Derivada do `conn` e do `hostname` gravados, na mesma funcao que o
    inventario usa - nao ha campo persistido, para nao envelhecer.

    Existe porque a criticidade define a CONDUTA (janela, aprovacao, rollback)
    e ate 24/09/2026 o caso nao a mostrava em lugar nenhum. Dava para abrir um
    caso, registrar hipotese e propor acao sem nunca ter dito de que ambiente
    se tratava - e '?' silencioso e pior que '?' escrito, porque ninguem
    pergunta o que nao ve.
    """
    from troubleshoot import ambientes as amb
    d = ler(caso, cases_dir)
    hosts = [h for h in (d.get("hostname"), d.get("dsn")) if h]
    nome = d.get("conn") or caso
    # A confirmacao humana entra so onde o nome nao revela nada - ver
    # `ambientes.criticidade`. Falha de leitura do inventario nao pode derrubar
    # o caso: sem inventario, a derivacao pelo nome continua valendo.
    try:
        conf = amb.criticidade_confirmada(amb.ler(), nome, hosts)
    except Exception:
        conf = ""
    return amb.criticidade(nome, hosts, conf)


def resumo(caso: str, cases_dir: str = "") -> dict:
    """Estado atual do caso, em forma plana, para CLI/HTML/CSV."""
    d = ler(caso, cases_dir)
    acoes = d.get("acoes") or []
    hips = d.get("hipoteses") or []
    # Sem status explicito e sem ciclo de vida o caso fica '-', nao
    # 'Investigando': um caso apenas coletado nao esta sendo investigado por
    # ninguem, e conta-lo como aberto inflaria o painel com trabalho ficticio.
    tem_vida = bool(d.get("hipotese_atual") or acoes)
    return {
        "caso": caso,
        "status": d.get("status") or ("Investigando" if tem_vida else "-"),
        "hipotese_inicial": d.get("hipotese_inicial", ""),
        "hipotese_atual": d.get("hipotese_atual", ""),
        "hipoteses_descartadas": len(hips),
        "acoes": len(acoes),
        "acoes_risco_alto": sum(1 for a in acoes if a.get("risco") == "alto"),
        "escalado": d.get("escalado", "N/A"),
        "conn": d.get("conn", ""),
        "hostname": d.get("hostname") or d.get("conn", ""),
        "criticidade": criticidade(caso, cases_dir),
        "familia": d.get("familia", ""),
        "evento": d.get("evento", ""),
        "causa_raiz": d.get("causa_raiz", ""),
        "criado_em": d.get("criado_em", ""),
        "resolvido_em": d.get("resolvido_em", ""),
        "tem_ciclo_de_vida": tem_vida,
    }


def linha_do_tempo(caso: str, cases_dir: str = "") -> list:
    """Eventos do caso em ordem cronologica, prontos para render.

    Existe para o painel e o dashboard mostrarem a MESMA historia: o meta.json
    guarda os eventos espalhados (hipoteses descartadas, acoes, marcos de status)
    e cada tela teria de remontar a ordem por conta propria.

    Casos antigos podem nao ter todos os carimbos de hora; esses eventos vao com
    'em' vazio e ficam no fim, em vez de sumir da historia.
    """
    d = ler(caso, cases_dir)
    if not d:
        return []
    ev = []

    def _add(em, tipo, texto, **extra):
        if texto:
            ev.append(dict(em=em or "", tipo=tipo, texto=texto, **extra))

    _add(d.get("criado_em"), "criado",
         d.get("evento") or f"Caso {caso} criado")
    if d.get("hipotese_inicial"):
        _add(d.get("criado_em"), "hipotese_inicial", d["hipotese_inicial"])
    for h in d.get("hipoteses") or []:
        _add(h.get("descartada_em"), "hipotese_descartada", h.get("texto"),
             motivo=h.get("motivo", ""))
    atual = (d.get("hipotese_atual") or "").strip()
    if atual and atual != (d.get("hipotese_inicial") or "").strip():
        _add(d.get("hipotese_desde"), "hipotese_atual", atual)
    for a in d.get("acoes") or []:
        _add(a.get("em"), "acao", a.get("descricao"),
             risco=a.get("risco", "nenhum"))
    # Verificacoes do passo a passo: a saida que o DBA colou, com a hora em que
    # foi observada. Entra na historia porque "as 14:32 nao havia bloqueio" e
    # exatamente o tipo de fato que, faltando no chamado, faz o proximo turno
    # refazer a consulta - e porque uma acao so se justifica pelo que foi
    # verificado ANTES dela, o que a ordem cronologica mostra sozinha.
    for s in ((d.get("guia") or {}).get("saidas") or []):
        for a in s.get("achados") or []:
            _add(s.get("em"), "observado", a.get("title", ""),
                 porque=a.get("porque", ""), passo=s.get("titulo", ""))
    _add(d.get("escalado_em"), "escalado", d.get("escalado"))
    # So entra se o caso foi mesmo fechado: 'causa_raiz' sozinha nao resolve nada,
    # e um texto padrao aqui inventaria um fim que nao aconteceu.
    if d.get("resolvido_em") or d.get("status") == "Resolvido":
        _add(d.get("resolvido_em"), "resolvido",
             d.get("causa_raiz") or "Caso resolvido",
             solucao=d.get("solucao", ""))

    # 'em' e 'YYYY-MM-DD HH:MM': ordena como texto. Vazio vai para o fim.
    return sorted(ev, key=lambda e: e["em"] or "9999")


# ── Template Padrao (o "redija") ──────────────────────────────
# O que ainda nao foi registrado sai marcado, e nao com um texto plausivel:
# um campo inventado no chamado e pior do que um campo em branco.
FALTA = "<preencher>"

# Nao sao evidencia: sao o proprio rastro da ferramenta.
_NAO_EVIDENCIA = ("meta.json", "history.json", "coleta.log")
_MAX_EVIDENCIAS = 8


def evidencias(caso: str, cases_dir: str = "") -> list:
    """Arquivos de evidencia da pasta do caso (o 'Dados coletados')."""
    d = os.path.join(cases_dir or CASES_DIR, caso)
    if not os.path.isdir(d):
        return []
    return sorted(n for n in os.listdir(d)
                  if os.path.isfile(os.path.join(d, n))
                  and n not in _NAO_EVIDENCIA and not n.startswith("."))


def _uma_linha(texto: str) -> str:
    """Colapsa espacos e quebras de linha.

    Hipotese e acao sao digitadas em <textarea> no painel: o DBA aperta Enter e
    o texto chega com '\\n'. Sem isso, a segunda linha sai colada a esquerda no
    template e parece um campo novo do chamado - o texto fica ambiguo para
    quem le, que e exatamente o que o formato padrao existe para evitar.
    """
    return " ".join(str(texto).split())


def _bloco(linhas: list) -> str:
    """Campo multilinha do template: uma linha por item, indentada."""
    return "\n" + "\n".join("  - " + _uma_linha(l) for l in linhas)


def texto_padrao(caso: str, cases_dir: str = "") -> str:
    """O caso no Template Padrao (Diagnostico Support Platform + RCA), em texto
    puro, pronto para colar no chamado.

    Le SO o meta.json - nao depende de o relatorio ter sido gerado, porque o
    momento de redigir e o de fechar o caso, que nem sempre vem depois de um
    `redija.bat`. Onde o relatorio deduz a partir dos achados, aqui fica
    `FALTA`: este texto e a transcricao do que o DBA registrou.
    """
    d = ler(caso, cases_dir)
    r = resumo(caso, cases_dir)

    acoes = d.get("acoes") or []
    if acoes:
        # `or`, nao default do get(): meta antigo tem a chave presente e VAZIA,
        # e o default nao pega esse caso - sairia "  -  [] descricao".
        acao_txt = _bloco([
            f"{a.get('em') or 'sem hora'} "
            f"[{'RISCO ALTO' if a.get('risco') == 'alto' else a.get('risco') or 'nenhum'}] "
            f"{a.get('descricao') or ''}" for a in acoes])
    else:
        acao_txt = FALTA

    if d.get("riscos"):
        riscos = d["riscos"]
    elif acoes:
        riscos = "Nenhuma acao de risco alto executada."
    else:
        riscos = FALTA

    # Pendencias do checklist pos-acao. Lidas direto do meta.json (e nao via
    # troubleshoot.guia) porque `guia` importa este modulo - o import ao
    # contrario fecharia o ciclo. O dado ja esta em `d`, de qualquer forma.
    pend = (d.get("guia") or {}).get("pendencias") or []
    abertas = [p for p in pend if not p.get("feito_em")]
    if abertas:
        # Vai para o chamado de proposito: quem le precisa saber que o ambiente
        # mudou e a validacao ainda nao foi feita. Fechar caso com verificacao
        # pendente e o jeito classico de o incidente voltar no dia seguinte.
        checklist = _bloco([f"PENDENTE: {p.get('texto', '')}" for p in abertas])
    elif pend:
        checklist = _bloco([
            f"{p.get('feito_em') or 'sem hora'} OK: {p.get('texto', '')}"
            for p in pend])
    else:
        # Sem pendencia do fluxo guiado NAO significa que nada mudou: caso
        # conduzido pelo chat registra as acoes por `CASO.py --acao --risco` e
        # nunca passa pelo GUIA, que e quem abre o checklist. Ate 21/09/2026
        # esses casos imprimiam "nenhuma acao que mude o ambiente" no chamado
        # tendo mexido no ambiente - mentira gerada pela ferramenta, na frase
        # que o leitor usa justamente para decidir se precisa validar algo.
        mudaram = [a for a in acoes if (a.get("risco") or "nenhum") != "nenhum"]
        if mudaram:
            niveis = sorted({a.get("risco") or "nenhum" for a in mudaram})
            checklist = (
                f"{len(mudaram)} acao(oes) de risco {'/'.join(niveis)} "
                "registradas fora do fluxo guiado - validar o efeito antes de "
                "encerrar (o checklist automatico so nasce pelo GUIA.py).")
        else:
            checklist = "N/A (nenhuma acao que mude o ambiente foi registrada)"

    if d.get("timeline"):
        timeline = d["timeline"]
    else:
        ev = linha_do_tempo(caso, cases_dir)
        timeline = _bloco([f"{e['em'] or 'sem hora'} [{e['tipo']}] {e['texto']}"
                           for e in ev]) if ev else FALTA

    # Coleta completa passa de 50 arquivos: a lista inteira dentro do chamado
    # vira parede de texto que ninguem le. Cita as primeiras e diz onde estao.
    arqs = evidencias(caso, cases_dir)
    if len(arqs) > _MAX_EVIDENCIAS:
        dados = (f"{len(arqs)} arquivos em troubleshooting\\{caso}\\ ("
                 + ", ".join(arqs[:_MAX_EVIDENCIAS])
                 + f", +{len(arqs) - _MAX_EVIDENCIAS})")
    else:
        dados = ", ".join(arqs)

    campos = [
        ("Diagnóstico Support Platform:", None),
        ("Hostname de referência", _uma_linha(r["hostname"])),
        ("Evento de referência", _uma_linha(r["evento"])),
        ("Hipótese inicial", _uma_linha(r["hipotese_inicial"])),
        ("Hipótese atual", _uma_linha(r["hipotese_atual"])),
        ("Dados coletados", dados),
        ("Ação Support Platform", acao_txt),
        ("Riscos mitigados", _uma_linha(riscos)),
        ("Checklist pós-ação", checklist),
        ("Escalado", d.get("escalado") or "Não"),
        # Caso so coletado nao esta sendo investigado por ninguem ('-' no
        # resumo): escrever 'Investigando' no chamado seria inventar um estado.
        ("Status", r["status"] if r["status"] != "-" else FALTA),
        ("Criado em", r["criado_em"]),
        ("Resolvido em", r["resolvido_em"] or "N/A"),
        ("", None),
        ("RCA:", None),
        ("Timeline", timeline),
        ("Impacto", d.get("impacto", "")),
        ("Causa Raiz", _uma_linha(r["causa_raiz"])),
        ("Solução", d.get("solucao", "")),
        ("Prevenção", d.get("prevencao", "")),
    ]
    linhas = []
    for rot, val in campos:
        if val is None:
            linhas.append(rot)
            continue
        v = str(val).rstrip()
        if v.startswith("\n"):
            # Campo montado por _bloco: cada item na sua linha, mesmo quando ha
            # um so - o chamado fica legivel sempre da mesma forma.
            linhas.append(rot + ":" + v)
        elif "\n" in v:
            # Texto longo escrito a mao (timeline, impacto): as linhas ficam
            # INDENTADAS sob o rotulo, senao a continuacao parece outro campo.
            linhas.append(rot + ":\n" + "\n".join(
                "  " + l.strip() for l in v.splitlines() if l.strip()))
        else:
            linhas.append(f"{rot}: {v.strip() or FALTA}")
    return "\n".join(linhas) + "\n"


MIN_PARA_PLAYBOOK = 3


def familias(cases_dir: str = "") -> dict:
    """familia -> [casos], para saber QUANDO ja da para escrever playbook.

    Preferencia do usuario (28/09/2026): nao montar playbook depois de um caso
    so. Um caso e anedota e leva peculiaridade de ambiente para dentro do
    procedimento; tres permitem comparar e separar o que se repete do que era
    daquele host.

    A regra exige CONTAR, e contar exigia ler prosa. Em 28/09/2026 a familia
    'alerta que mede certo e e interpretado errado' tinha tres casos em tres
    plataformas (Db2 com percentual nulo, SQL Server com memoria no teto,
    Oracle com linha de base de APM) e eu quase perdi o terceiro numa busca por
    palavra-chave: a causa dele nao usa 'falso positivo', diz 'lento e
    inofensivo'. Campo explicito resolve; regex sobre texto livre nao.

    Valor LIVRE de proposito, sem catalogo fechado: lista fechada envelhece e
    forca encaixar caso novo em rotulo errado. Quem chama mostra as familias que
    ja existem para a grafia ser reaproveitada.
    """
    fam: dict = {}
    for nome in listar_casos(cases_dir, so_troubleshooting=True):
        chave = (ler(nome, cases_dir).get("familia") or "").strip()
        if chave:
            fam.setdefault(chave, []).append(nome)
    return dict(sorted(fam.items(), key=lambda kv: (-len(kv[1]), kv[0])))


def e_troubleshooting(caso: str, cases_dir: str = "") -> bool:
    """O caso e TROUBLESHOOTING, ou so uma foto de coleta?

    `troubleshooting/` guarda duas coisas muito diferentes com a mesma cara de
    pasta, e o consolidado contava as duas como "caso". Medido em 25/09/2026:
    37 pastas, das quais 7 eram investigacao de verdade, 28 eram healthcheck /
    bateria de homologacao e 2 eram `teste` e `DEMO`.

    O criterio NAO e aberto x fechado (preferencia do usuario, 25/09/2026).
    Filtrar por 'Resolvido' esconderia os 4 casos em andamento - e mostrar o que
    esta em aberto e justamente a funcao do Fim do dia. O que separa e ter
    alguem CONDUZINDO:

      - hipotese ou acao registrada -> alguem esta raciocinando sobre isto;
      - hostname informado          -> foi um humano que digitou o ambiente.

    A foto de coleta grava `conn` e `dsn` (a coleta sabe onde se conectou), nunca
    `hostname`, e nao tem ciclo de vida - entao cai fora sozinha, sem lista de
    excecao por nome. `teste` e `DEMO` tambem, pelo mesmo caminho: e melhor que
    um filtro por nome, que envelhece na primeira pasta nova.
    """
    d = ler(caso, cases_dir)
    return bool(d.get("hipotese_atual") or d.get("hipoteses")
                or d.get("acoes") or d.get("hostname"))


def listar_casos(cases_dir: str = "", so_troubleshooting: bool = False) -> list:
    """Casos existentes na pasta de evidencias, mais recentes primeiro.

    O padrao continua trazendo TUDO, e isso e deliberado: `MELHORIA.py --corpus`
    reanalisa a evidencia gravada para provar que uma mudanca de codigo nao
    alterou diagnostico ja entregue, e o corpus dele sao justamente as 28 fotos
    de coleta. Filtrar por padrao esvaziaria essa rede de protecao em silencio.
    Quem quer so investigacao pede explicitamente.
    """
    base = cases_dir or CASES_DIR
    if not os.path.isdir(base):
        return []
    nomes = [n for n in os.listdir(base) if os.path.isdir(os.path.join(base, n))]
    if so_troubleshooting:
        nomes = [n for n in nomes if e_troubleshooting(n, base)]
    return sorted(nomes, key=lambda n: ler(n, base).get("atualizado_em")
                  or ler(n, base).get("criado_em") or "", reverse=True)


# ── Fim do dia ────────────────────────────────────────────────
_COLUNAS = [
    "caso", "status", "severidade", "score", "hostname", "evento",
    "hipotese_inicial", "hipotese_atual", "hipoteses_descartadas",
    "acoes", "acoes_risco_alto", "escalado", "causa_raiz", "achados",
    "criado_em", "resolvido_em", "analisado_em", "dashboard",
]


# O consolidado e feito para ser aberto no Excel. Celula que comeca com um
# destes caracteres o Excel trata como FORMULA, nao como texto - inclusive
# `=cmd|...!A1` (DDE). O conteudo aqui e texto livre digitado no painel, entao a
# planilha do "Fim do dia" nao pode ser o caminho para executar algo na maquina
# de quem a abrir. Prefixar com apostrofo forca texto e o Excel nao a exibe.
_GATILHOS_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def _texto_de_planilha(valor):
    # len > 1: o proprio framework grava '-' como status de caso so coletado, e
    # um traco sozinho nao e formula nenhuma - so ficaria feio na planilha.
    if isinstance(valor, str) and len(valor) > 1 and valor[0] in _GATILHOS_FORMULA:
        return "'" + valor
    return valor


def _indice(output_dir: str = "") -> dict:
    p = os.path.join(output_dir or OUTPUT_DIR, "index.json")
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh) or {}
    except Exception:
        return {}


def consolidar_dia(dia: str = "", out_path: str = "", cases_dir: str = "",
                   output_dir: str = "", todos: bool = False) -> tuple:
    """Comando 'Fim do dia': um CSV dos casos, juntando o ciclo de vida
    (meta.json) com o resultado da analise (index.json).

    Por padrao traz so INVESTIGACAO (`e_troubleshooting`). Ate 25/09/2026 trazia
    as 37 pastas: 7 casos reais afogados em 28 fotos de healthcheck e 2 pastas de
    teste. Um consolidado em que 3 de cada 4 linhas nao e caso nao se le, e o
    numero de "em aberto" no fim - que e o que importa - perdia o sentido.

    `todos=True` volta ao comportamento anterior; nada e apagado do disco, so
    deixa de ser contado como caso.

    Une as duas metades que hoje vivem separadas: o index.json sabe severidade,
    score e numero de achados; o meta.json sabe a hipotese e as acoes. Nenhum
    dos dois sozinho responde "o que aconteceu hoje".

    dia: 'YYYY-MM-DD' filtra por criado_em/analisado_em. Vazio = todos.
    Retorna (caminho_csv, linhas).
    """
    idx = _indice(output_dir)
    casos = set(listar_casos(cases_dir)) | set(idx)
    # O filtro vale para a UNIAO: o index.json traz quem teve dashboard gerado,
    # e a bateria de homologacao gera dashboard para cada coleta. Filtrar so a
    # listagem de pastas deixaria as 26 entradas do indice entrarem por tras.
    if not todos:
        casos = {c for c in casos if e_troubleshooting(c, cases_dir)}

    linhas = []
    for caso in sorted(casos):
        r = resumo(caso, cases_dir)
        i = idx.get(caso) or {}
        analisado = i.get("generated_at", "")
        if dia and not (str(r["criado_em"]).startswith(dia)
                        or str(analisado).startswith(dia)):
            continue
        linhas.append({
            "caso": caso,
            "status": r["status"],
            "severidade": i.get("severity", ""),
            "score": i.get("score", ""),
            "hostname": r["hostname"],
            "evento": r["evento"] or i.get("title", ""),
            "hipotese_inicial": r["hipotese_inicial"],
            "hipotese_atual": r["hipotese_atual"],
            "hipoteses_descartadas": r["hipoteses_descartadas"],
            "acoes": r["acoes"],
            "acoes_risco_alto": r["acoes_risco_alto"],
            "escalado": r["escalado"],
            "causa_raiz": r["causa_raiz"],
            "achados": i.get("n_findings", ""),
            "criado_em": r["criado_em"],
            "resolvido_em": r["resolvido_em"],
            "analisado_em": analisado,
            "dashboard": i.get("html", ""),
        })

    destino = out_path or os.path.join(
        output_dir or OUTPUT_DIR,
        f"consolidado_{dia or datetime.now().strftime('%Y-%m-%d')}.csv")
    os.makedirs(os.path.dirname(os.path.abspath(destino)), exist_ok=True)
    # utf-8-sig: o Excel em pt-BR abre CSV utf-8 sem BOM com acentuacao quebrada.
    with open(destino, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=_COLUNAS, delimiter=";")
        w.writeheader()
        # So na escrita: quem consome `linhas` no Python quer o texto original.
        w.writerows([{k: _texto_de_planilha(v) for k, v in l.items()}
                     for l in linhas])
    return destino, linhas
