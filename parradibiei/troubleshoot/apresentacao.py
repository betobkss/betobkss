"""
Deck de apresentacao do PROPRIO framework (nao de um caso).

Duas plateias, dois decks:

- `gestao`  - 10 minutos: qual problema resolve, por que e seguro rodar em
  producao, o que sai no fim e o que estamos pedindo. Sem jargao.
- `tecnico` - 20 minutos: como as pecas se encaixam, como conduzir o caso,
  como o framework se prova (testes e corpus) e como estende-lo.
- `impacto` - 10 minutos, so o ganho: quanto tempo o checklist na mao consome
  por nivel de senioridade (junior/pleno/senior), quanto o framework consome,
  o que acontece com a cobertura, a assertividade e a padronizacao. Os numeros
  saem de `impacto.py`, que separa o que foi CONTADO, o que foi MEDIDO e o que
  e PREMISSA - e o deck imprime a premissa num slide, com as fontes publicas.

Dois cuidados que valem mais que o conteudo:

1. **Nenhum dado de banco real entra aqui.** O deck circula por e-mail. Os
   exemplos sao fixos e ficticios (`INC000123456`, `<servidor>`); nada e lido
   de `stringConexao/` nem da pasta de casos.
2. **Os numeros sao contados do repositorio na hora de gerar** (`numeros()`).
   Numero escrito a mao no slide envelhece calado e vira mentira na terceira
   apresentacao.

O visual reaproveita o kit do relatorio (`report._pptx_kit`): mesma grade,
mesma paleta, mesma marca - quem ja viu um relatorio reconhece o deck.
"""
from __future__ import annotations

import glob
import json
import os
import re

from . import marca
from .report import _P, _pptx_kit

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(BASE_DIR, "output", "apresentacao")

PUBLICOS = ("gestao", "tecnico", "impacto")

# Exemplo ficticio, usado em todo o deck. Nunca usar caso ou servidor real:
# o arquivo sai da maquina.
CASO_EX = "INC000123456"
SRV_EX = "<servidor>"


def _conta(padrao: str, regex: str) -> int:
    n = 0
    for p in glob.glob(os.path.join(BASE_DIR, padrao), recursive=True):
        try:
            with open(p, encoding="utf-8", errors="ignore") as f:
                n += len(re.findall(regex, f.read(), re.M))
        except OSError:
            continue
    return n


def numeros() -> dict:
    """Os numeros do framework, contados do repositorio nesta execucao."""
    n = {"checks": 0, "playbooks": 0, "analisadores": 0, "testes": 0,
         "sql": 0, "erros_ora": 0}
    try:
        with open(os.path.join(BASE_DIR, "mcp", "catalog.json"),
                  encoding="utf-8") as f:
            cat = json.load(f)
        n["checks"] = len(cat.get("checks") or cat)
    except Exception:
        pass
    try:
        from . import kb
        n["playbooks"] = len(kb.buscar(""))
    except Exception:
        pass
    try:
        from .analyzers import ANALYZERS
        n["analisadores"] = len(ANALYZERS)
    except Exception:
        pass
    try:
        from . import erros
        n["erros_ora"] = len(erros.carregar())
    except Exception:
        pass
    n["testes"] = _conta("tests/test_*.py", r"^\s*def test_")
    n["sql"] = len(glob.glob(os.path.join(BASE_DIR, "queries", "*.sql")))
    return n


# ── blocos reaproveitados pelos dois decks ────────────────────
CICLO = [
    ("1  Coletar", "COLETAR.py", "Coleta read-only guiada, uma conexao do\n"
     "repositorio. A senha e digitada na hora."),
    ("2  Analisar", "TROUBLESHOOT.py", "Analisadores leem a evidencia e\n"
     "classificam achados por severidade."),
    ("3  Conduzir", "CASO.py / painel", "Hipotese por vez, acao com risco e\n"
     "hora. Fica tudo registrado."),
    ("4  Redigir", "redija", "Template Padrao pronto para o chamado,\n"
     "com o que falta marcado."),
    ("5  Consolidar", "Fim do dia", "CSV com todos os casos do dia e o\n"
     "que sobrou aberto."),
]

SEGURANCA = [
    ("Read-only por construcao",
     "A coleta so executa SELECT em views de diagnostico. Nada de DDL, DML "
     "ou kill - o catalogo de checks e fixo e versionado."),
    ("Licenca respeitada",
     "O modo Parcial nao consome Diagnostics/Tuning Pack. O que usa pack esta "
     "rotulado na propria tela, antes de rodar."),
    ("O unico comando que escreve exige opt-in",
     "O SQL Tuning Advisor cria tuning tasks no banco: vem desmarcado, so no "
     "modo Completo e com confirmacao explicita antes de rodar."),
    ("A senha nao fica em lugar nenhum",
     "Vai direto para o processo: nao aparece na linha de comando, no log da "
     "execucao nem em arquivo."),
    ("Acao de risco exige decisao humana",
     "Toda acao registrada carrega o risco declarado; risco alto pede aviso "
     "de impacto e plano de volta antes de executar."),
]


# Um icone por etapa do ciclo. A ordem acompanha CICLO.
ICONE_CICLO = ["banco", "grafico", "alvo", "doc", "fila"]


def _grade_ciclo(k, s, topo):
    """As cinco etapas em faixa horizontal - o slide que a plateia memoriza."""
    texto, card, barra = k["texto"], k["card"], k["barra"]
    icone, forma, x, w = k["icone"], k["forma"], k["x"], k["w"]
    for i, (tit, cmd, desc) in enumerate(CICLO):
        left = x(0) + i * (w(12) / 5)
        larg = w(12) / 5 - 0.14
        cor = _P["brand"] if i else _P["acc"]
        card(s, left, topo, larg, 2.72, _P["surf"], _P["line"])
        barra(s, left, topo, larg, 0.055, cor)
        icone(s, left + 0.16, topo + 0.26, 0.54, ICONE_CICLO[i], cor)
        # Seta entre os cartoes: a plateia precisa VER que ha uma ordem, nao
        # cinco caixas soltas lado a lado.
        if i:
            forma(s, k["SHAPE"].RIGHT_ARROW, left - 0.135, topo + 0.42,
                  0.13, 0.2, _P["line2"])
        texto(s, left + 0.16, topo + 0.94, larg - 0.32, 0.3, tit, 13, True)
        texto(s, left + 0.16, topo + 1.28, larg - 0.32, 0.28, cmd, 9, True,
              _P["acc"])
        texto(s, left + 0.16, topo + 1.62, larg - 0.32, 1.0, desc, 9.5, False,
              _P["mut"], espaco=1.22)


def _lista_cartoes(k, s, topo, itens, altura=0.95, gap=0.14, largura=12,
                   icones=None):
    """Um cartao por item: icone + titulo em negrito + explicacao.

    O icone nao e enfeite: numa lista de quatro cartoes iguais o olho nao tem
    onde pousar, e a plateia le tudo de uma vez em vez de acompanhar quem
    fala. O disco colorido a esquerda da a cada item uma ancora visual e deixa
    o apresentador apontar "este aqui".
    """
    texto, card, barra = k["texto"], k["card"], k["barra"]
    icone, x, w = k["icone"], k["x"], k["w"]
    icones = list(icones or [])
    for i, (tit, desc) in enumerate(itens):
        t = topo + i * (altura + gap)
        card(s, x(0), t, w(largura), altura, _P["surf"], _P["line"])
        barra(s, x(0), t, 0.055, altura, _P["brand"])
        nome = icones[i] if i < len(icones) else ""
        if nome:
            d = min(0.62, altura - 0.3)
            icone(s, x(0) + 0.26, t + (altura - d) / 2, d, nome, _P["brand"])
            esq = x(0) + 0.26 + d + 0.24
        else:
            esq = x(0) + 0.22
        larg = w(largura) - (esq - x(0)) - 0.28
        texto(s, esq, t + 0.16, larg, 0.3, tit, 12.5, True)
        texto(s, esq, t + 0.48, larg, altura - 0.55, desc,
              10, False, _P["mut"], espaco=1.18)


def _bullets(k, s, topo, linhas, tam=13, salto=0.46, esq=None, larg=12):
    """Lista com marcador. O ponto vira um quadradinho girado (losango) para
    nao competir com os discos de icone dos cartoes - dois marcadores redondos
    iguais no mesmo deck fazem o olho tratar lista e cartao como a mesma
    coisa."""
    texto, forma = k["texto"], k["forma"]
    x, w = k["x"], k["w"]
    left = x(0) if esq is None else esq
    for i, txt in enumerate(linhas):
        t = topo + i * salto
        d = forma(s, k["SHAPE"].RECTANGLE, left + 0.03, t + 0.12, 0.1, 0.1,
                  _P["brand"])
        d.rotation = 45
        texto(s, left + 0.32, t, (w(larg) if esq is None else larg) - 0.34,
              salto, txt, tam, False, espaco=1.2)


def _numeros_faixa(k, s, topo, pares, icones=None, alt=1.72):
    """Cartoes de numero grande, com acento colorido no topo e icone."""
    texto, card, barra = k["texto"], k["card"], k["barra"]
    icone, x, w, C = k["icone"], k["x"], k["w"], k["ALIGN"].CENTER
    icones = list(icones or [])
    larg = w(12) / len(pares)
    for i, (n, rot) in enumerate(pares):
        left = x(0) + i * larg
        card(s, left, topo, larg - 0.16, alt, _P["surf"], _P["line"])
        # Faixa no topo do cartao: alterna marca/acento para os quatro numeros
        # nao virarem um bloco vermelho unico, que cansa e nivela a leitura.
        cor = _P["brand"] if i % 2 == 0 else _P["acc"]
        barra(s, left, topo, larg - 0.16, 0.06, cor)
        nome = icones[i] if i < len(icones) else ""
        if nome:
            icone(s, left + (larg - 0.16 - 0.46) / 2, topo + 0.22, 0.46,
                  nome, cor)
        y = topo + (0.78 if nome else 0.3)
        texto(s, left, y, larg - 0.16, 0.62, str(n), 30, True, cor, align=C)
        texto(s, left, y + 0.62, larg - 0.16, 0.3, rot, 9, True, _P["mut"],
              align=C)


def _barras_comparativas(k, s, topo, linhas, maximo, altura=0.95, gap=0.5):
    """Duas barras por linha (antes/depois) com o rotulo dentro.

    Grafico de verdade em PPTX (add_chart) sai com a fonte e a paleta do tema
    do Office, nao com a do deck - e o slide inteiro passa a destoar. Duas
    barras desenhadas com retangulos ficam sob o mesmo controle do resto.
    """
    texto, pilula, card = k["texto"], k["pilula"], k["card"]
    x, w = k["x"], k["w"]
    esq, larg = x(0) + 1.35, w(12) - 1.35
    for i, (rot, antes, depois, nota) in enumerate(linhas):
        t = topo + i * (altura + gap)
        # Etiqueta do nivel num cartao proprio: alinha as tres linhas e cria a
        # coluna que faltava para o olho descer comparando so as barras.
        card(s, x(0), t + 0.02, 1.15, 0.76, _P["surf"], _P["line"])
        texto(s, x(0), t + 0.22, 1.15, 0.3, rot, 13, True,
              align=k["ALIGN"].CENTER)
        for j, (val, leg) in enumerate((antes, depois)):
            tb = t + j * 0.44
            cor = _P["brand"] if j else _P["line2"]
            # Trilho claro atras: mostra a escala. Sem ele a barra curta parece
            # so uma barra curta, e nao "o quanto sobrou do que era".
            pilula(s, esq, tb, larg, 0.34, _P["surf2"])
            comp = max(1.6, larg * (val / maximo if maximo else 0))
            pilula(s, esq, tb, comp, 0.34, cor)
            # Rotulo em branco DENTRO da barra so quando cabe nela. A barra
            # vencedora e a mais curta, e o texto dela e o mais longo: escrito
            # em branco transbordando para o trilho claro, sumia da tela.
            largura_texto = len(leg) * 0.078 + 0.3
            if largura_texto <= comp:
                texto(s, esq + 0.16, tb + 0.06, comp - 0.3, 0.24, leg, 10.5,
                      True, _P["bg"])
            else:
                texto(s, esq + comp + 0.16, tb + 0.06,
                      max(0.6, larg - comp - 0.2), 0.24, leg, 10.5, True,
                      _P["txt"] if j else _P["mut"])
        texto(s, esq, t + 0.94, larg, 0.28, nota, 10.5, True, _P["brand"])


# ── deck ──────────────────────────────────────────────────────
def build_deck(publico: str = "gestao", out_path: str | None = None) -> str:
    """Gera o deck de apresentacao do framework. Retorna o caminho do .pptx."""
    if publico not in PUBLICOS:
        raise ValueError(f"publico invalido: {publico!r} (use {PUBLICOS})")

    from pptx import Presentation
    from pptx.util import Inches

    out_path = out_path or os.path.join(
        OUTPUT_DIR, f"FRAMEWORK_{publico.upper()}.pptx")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]
    k = _pptx_kit(prs)
    texto, barra = k["texto"], k["barra"]
    cabecalho, rodape, fundo = k["cabecalho"], k["rodape"], k["fundo"]
    MARG, w = k["MARG"], k["w"]

    n = numeros()
    VISAO = {"gestao": "visao executiva", "tecnico": "visao tecnica",
             "impacto": "impacto e ganho de tempo"}
    rot = "Framework de Troubleshooting Oracle  |  " + VISAO[publico]
    cont = [0]

    def novo(titulo="", sub="", nota=""):
        s = prs.slides.add_slide(blank)
        fundo(s)
        topo = cabecalho(s, titulo, sub) if titulo else 0.5
        cont[0] += 1
        rodape(s, cont[0], rot)
        # A fala sugerida vai nas notas: o slide fica limpo e quem apresenta
        # (nem sempre quem construiu) nao precisa decorar o argumento.
        if nota:
            s.notes_slide.notes_text_frame.text = nota
        return s, topo

    # ── capa ─────────────────────────────────────────────────
    # Antes: uma regua vermelha, tres blocos de texto empilhados e quatro
    # numeros. Correta e sem nenhum ponto focal - a plateia batia o olho e nao
    # sabia para onde olhar. Agora o titulo divide o palco com um painel
    # grafico a direita, e os numeros ganharam icone.
    s = prs.slides.add_slide(blank)
    fundo(s)
    cont[0] += 1
    card, forma, icone, anel = k["card"], k["forma"], k["icone"], k["anel"]
    barra(s, 0, 0, 13.333, 0.28, _P["brand"])

    # Painel de acento a direita: dá peso visual e cria a coluna do titulo.
    card(s, 8.35, 1.15, 4.38, 4.55, _P["surf"], _P["line"], raio=0.05)
    barra(s, 8.35, 1.15, 4.38, 0.07, _P["brand"])
    # Anel: nao e grafico de dado nenhum, e o simbolo do ciclo fechado
    # (coletar -> analisar -> conduzir -> redigir -> consolidar).
    anel(s, 9.72, 1.72, 1.64, 0.8, _P["brand"], espessura=0.17)
    texto(s, 9.72, 2.28, 1.64, 0.44, "5", 26, True, _P["brand"],
          align=k["ALIGN"].CENTER)
    texto(s, 8.55, 3.52, 3.98, 0.3, "ETAPAS, SEMPRE NA MESMA ORDEM", 9, True,
          _P["mut"], align=k["ALIGN"].CENTER)
    for i, (nm, rot) in enumerate((("banco", "Coletar"), ("grafico", "Analisar"),
                                   ("alvo", "Conduzir"), ("doc", "Redigir"))):
        cx = 8.62 + i * 1.03
        icone(s, cx, 4.02, 0.6, nm, _P["brand"] if i % 2 == 0 else _P["acc"])
        texto(s, cx - 0.14, 4.68, 0.88, 0.26, rot, 8.5, True, _P["mut"],
              align=k["ALIGN"].CENTER)
    texto(s, 8.55, 5.12, 3.98, 0.3, "read-only, sem instalar nada", 9.5, False,
          _P["mut"], align=k["ALIGN"].CENTER)

    texto(s, MARG, 0.75, 6, 0.35, "PARRADIBIEI", 12, True, _P["brand"])
    texto(s, MARG, 1.62, 7.5, 1.9, "Framework de\nTroubleshooting Oracle",
          40, True, espaco=1.02)
    barra(s, MARG, 3.62, 1.0, 0.05, _P["brand"])
    texto(s, MARG, 3.88, 7.4, 1.0,
          ("O mesmo checklist, com a mesma profundidade,\n"
           "no tempo de um cafe - seja quem for que esta\nde plantao.")
          if publico == "impacto" else
          ("Coleta read-only, analise automatica e o chamado\n"
           "escrito no mesmo padrao - independente de quem\nesta de plantao."),
          14.5, False, _P["mut"], espaco=1.3)
    _numeros_faixa(k, s, 5.72, [
        (n["checks"], "CHECKS READ-ONLY"),
        (n["analisadores"], "ANALISADORES"),
        (n["playbooks"], "PLAYBOOKS"),
        (n["testes"], "TESTES AUTOMATIZADOS"),
    ], alt=1.08)
    forma(s, k["SHAPE"].RECTANGLE, MARG, 6.96, 12.13, 0.012, _P["line"])
    texto(s, MARG, 7.04, 8.0, 0.24,
          "Apresentacao " + {"gestao": "executiva (10 min)",
                             "tecnico": "tecnica (20 min)",
                             "impacto": "de impacto (10 min)"}[publico],
          9, False, _P["mut"])
    s.notes_slide.notes_text_frame.text = (
        "Abra com a frase que sustenta o resto: 'o troubleshooting deixou de "
        "depender de quem esta de plantao'. Nao leia os numeros da capa - eles "
        "voltam no slide de prova. Diga quanto tempo vai levar e que havera "
        "demonstracao.")

    # ── o problema ───────────────────────────────────────────
    s, topo = novo(
        "O problema", "como um chamado de lentidao andava antes",
        "Conte UM caso concreto que a plateia viveu, sem nomear pessoas. O "
        "objetivo aqui e concordancia: se ninguem reconhecer o problema, o "
        "resto da apresentacao vira demonstracao de tecnologia.")
    _lista_cartoes(k, s, topo, [
        ("O diagnostico dependia de quem atendeu",
         "Cada DBA tinha o seu roteiro e o seu conjunto de queries. Dois "
         "plantoes diferentes chegavam a dois diagnosticos diferentes para o "
         "mesmo sintoma."),
        ("O tempo ia embora na coleta, nao na analise",
         "Abrir sessao, lembrar a query certa, colar resultado no bloco de "
         "notas. A parte que exige um DBA senior comecava depois disso tudo."),
        ("O chamado era reescrito do zero",
         "O texto entregue variava em forma e profundidade, e o passo a passo "
         "executado no banco raramente ficava registrado."),
        ("O conhecimento morava nas pessoas",
         "O que resolveu um ORA- especifico ficava na memoria de quem "
         "resolveu - e saia junto nas ferias."),
    ], altura=1.15, icones=["pessoas", "tempo", "doc", "alvo"])

    # ── o ciclo (compartilhado) ──────────────────────────────
    s, topo = novo(
        "O ciclo", "as cinco etapas, sempre na mesma ordem",
        "E o slide que a plateia memoriza - passe devagar. A frase-chave: cada "
        "etapa deixa registro para a proxima, e por isso o texto do chamado e "
        "transcricao, nao memoria.")
    _grade_ciclo(k, s, topo + 0.35)
    texto(s, MARG, topo + 3.3, w(12), 1.2,
          "A ordem e o produto. Cada etapa deixa registro para a proxima: a "
          "coleta guarda a evidencia, a analise guarda os achados, a conducao "
          "guarda o que foi feito no banco - e o texto do chamado e a\n"
          "transcricao disso, nao um resumo escrito de memoria no fim do dia.",
          12.5, False, espaco=1.3)

    # ── seguranca (compartilhado) ────────────────────────────
    s, topo = novo(
        "Por que e seguro rodar",
        "o que a ferramenta NAO faz - e por construcao",
        "Slide mais importante para DBA senior e para risco/compliance. Nao "
        "prometa 'e seguro': mostre POR QUE nao ha como nao ser - catalogo "
        "fixo de checks, advisor so por opt-in, senha fora do log.")
    _lista_cartoes(k, s, topo, SEGURANCA, altura=0.95,
                   icones=["escudo", "check", "engrenagem", "banco",
                           "alvo"])

    if publico == "gestao":
        _deck_gestao(k, novo, n)
    elif publico == "impacto":
        _deck_impacto(k, novo, n)
    else:
        _deck_tecnico(k, novo, n)

    # ── demonstracao (nos dois decks) ────────────────────────
    s, topo = novo(
        "Demonstracao", "5 minutos, ensaiados - roteiro do apresentador",
        "Este slide e para VOCE: ensaie a sequencia pelo menos duas vezes e "
        "deixe o caso ja coletado aberto antes de comecar a apresentacao. Se "
        "o tempo apertar, corte a demo - nunca corte o slide de seguranca.")
    _bullets(k, s, topo + 0.1, [
        "Comece com um caso JA coletado. Coletar ao vivo depende de rede, "
        "senha e sorte - tres coisas que faltam em apresentacao.",
        "Abra o dashboard: Health Score, o achado critico e o plano de acao "
        "priorizado.",
        "Registre uma hipotese e uma acao de risco alto: mostre o aviso que "
        "a ferramenta cobra.",
        "Resolva o caso e mostre o texto do chamado saindo pronto, com o que "
        "falta marcado como <preencher>.",
        "Encerre com o consolidado do dia (CSV).",
    ], tam=13, salto=0.72)
    texto(s, MARG, topo + 3.85, w(12), 0.9,
          "Plano B: deixe o dashboard salvo em HTML aberto em outra aba. Se o "
          "painel travar, a apresentacao continua.\nNada de dado real na tela: "
          "use o banco de homologacao ou um caso mascarado - a plateia recebe "
          "o deck por e-mail depois.",
          12, True, _P["brand"], espaco=1.3)

    # ── encerramento (nos dois decks) ────────────────────────
    s, topo = novo(
        "O que estamos pedindo",
        "apresentacao sem pedido termina em aplauso e nada muda",
        "Termine pedindo uma decisao com data. Se a plateia so tiver tempo "
        "para uma coisa, peca a adocao pelo time - as outras duas seguem "
        "dela.")
    _lista_cartoes(k, s, topo, [
        ("Adocao pelo time",
         "Todo chamado de banco passa a ser aberto com um caso no framework. "
         "E o unico jeito de o historico e a reincidencia terem valor."),
        ("Acesso a mais bancos de homologacao",
         "A bateria de homologacao so prova o que consegue alcancar. Quanto "
         "mais versoes e topologias, menos surpresa em producao."),
        ("Tempo dedicado para a melhoria continua",
         "A rotina de melhoria (corpus de regressao, radar da comunidade "
         "Oracle) e o que impede a ferramenta de envelhecer."),
    ], altura=1.15, icones=["pessoas", "banco", "engrenagem"])
    texto(s, MARG, topo + 3.9, w(12), 0.4,
          "Proximo passo sugerido: escolher um servidor e rodar o primeiro "
          "caso junto, nesta semana.", 13, True)

    prs.save(out_path)
    return out_path


def _deck_gestao(k, novo, n):
    """Slides so da versao executiva: valor, entrega, limites."""
    texto, w, MARG = k["texto"], k["w"], k["MARG"]

    s, topo = novo(
        "O que o time recebe no fim",
        "quatro artefatos, gerados em uma execucao",
        "Se puder, projete o dashboard real (mascarado) por 10 segundos aqui. "
        "Um artefato visto vale mais que quatro descritos.")
    _lista_cartoes(k, s, topo, [
        ("Dashboard HTML",
         "Uma pagina com Health Score, achados por severidade e plano de "
         "acao priorizado. Abre sem internet e pode ser anexada ao chamado."),
        ("Relatorio no Template Padrao",
         "Diagnostico + RCA no mesmo formato sempre, com o que ainda nao foi "
         "registrado marcado como <preencher> em vez de preenchido no chute."),
        ("Apresentacao (PPTX)",
         "O caso resumido para quem tem 10 minutos - util quando o incidente "
         "sobe de nivel."),
        ("Consolidado do dia (CSV)",
         "Todos os casos do dia em uma planilha: o que foi resolvido, o que "
         "ficou aberto e com quem."),
    ], altura=1.15, icones=["grafico", "doc", "alvo", "fila"])

    s, topo = novo(
        "Rastreabilidade", "a pergunta da auditoria, respondida",
        "Aqui costuma vir a pergunta 'e se alguem executar algo errado?'. A "
        "resposta: a ferramenta nao impede o DBA de agir, ela impede que a "
        "acao passe sem registro.")
    _bullets(k, s, topo + 0.1, [
        "Toda acao executada no banco fica registrada com data, hora e o "
        "risco declarado por quem executou.",
        "Acao de risco alto sai destacada no relatorio - nao se perde no "
        "meio do texto.",
        "As hipoteses descartadas ficam gravadas: ninguem refaz o caminho "
        "que ja foi testado e nao explicou o sintoma.",
        "O texto entregue no chamado e a transcricao desse registro, nao uma "
        "reconstrucao de memoria no fim do plantao.",
    ], tam=14, salto=0.82)
    texto(s, MARG, topo + 3.9, w(12), 0.5,
          "Efeito colateral util: quando o caso troca de dono, o proximo DBA "
          "le o que ja foi feito em vez de comecar do zero.",
          12.5, False, _P["mut"])

    s, topo = novo(
        "Resultado", "preencher com um caso real antes de apresentar",
        "NAO apresente este slide com as lacunas. Escolha um caso que voce "
        "conduziu, meca os dois tempos e defenda so o que sustenta se "
        "perguntarem de onde saiu o numero.")
    _lista_cartoes(k, s, topo, [
        ("Tempo ate o primeiro diagnostico",
         "Antes: ____ min.   Agora: ____ min.   (use um caso que voce "
         "conduziu nos dois formatos)"),
        ("Padronizacao do que e entregue",
         "Antes: texto livre, variavel por pessoa.   Agora: mesmo template "
         "em 100% dos casos."),
        ("Reincidencia identificada",
         "Achado que ja tinha aparecido no mesmo servidor agora aparece "
         "marcado - o paliativo repetido fica visivel."),
    ], altura=1.15, icones=["tempo", "doc", "seta"])
    texto(s, MARG, topo + 3.9, w(12), 0.5,
          "Numero inventado aqui destroi a credibilidade do resto. Use o que "
          "voce consegue defender.", 12, True, _P["brand"])

    s, topo = novo(
        "Limites", "o que a ferramenta nao promete",
        "Dizer os limites antes que perguntem e o que compra credibilidade "
        "para tudo que foi dito ate aqui. Nao pule para ganhar tempo.")
    _lista_cartoes(k, s, topo, [
        ("Nao substitui o DBA",
         "Ela coleta, organiza e prioriza. A decisao sobre o que executar no "
         "banco continua sendo humana."),
        ("Depende da qualidade da evidencia",
         "Coleta interrompida gera analise parcial - e o relatorio avisa "
         "quando isso acontece, em vez de mostrar um score verde."),
        ("Parte dos dados exige licenca",
         "Historico de AWR/ADDM depende de Diagnostics Pack. Sem licenca, o "
         "modo Parcial entrega o retrato do agora."),
        ("Nao monitora",
         "Nao substitui a ferramenta de monitoracao: ela entra quando o "
         "chamado ja existe."),
    ], altura=1.15, icones=["pessoas", "escudo", "banco", "alvo"])


def _deck_impacto(k, novo, n):
    """Slides so da versao de impacto: tempo, cobertura, assertividade, padrao.

    Este deck e o unico que faz CONTA, e por isso e o unico que carrega um
    slide de premissas e um de fontes. Nao e excesso de zelo: e o que permite
    responder "de onde saiu esse numero?" sem improvisar - e a pergunta vem
    sempre, normalmente de quem menos quer que o numero seja verdade.
    """
    from . import impacto as imp

    texto, w, MARG = k["texto"], k["w"], k["MARG"]
    med = imp.tempo_maquina()
    c = imp.comparativo(n["checks"], med["minutos"])
    niv = c["niveis"]

    # ── o tempo de maquina, cronometrado ─────────────────────
    s, topo = novo(
        "O tempo da maquina", "cronometrado, nao estimado",
        "Este e o slide que cala a sala. Diga o numero e espere. Depois "
        "explique que ele NAO e estimativa: e mediana cronometrada, gravada "
        "em disco com data, e o comando que mede esta no repositorio para "
        "qualquer um repetir.")
    _numeros_faixa(k, s, topo + 0.1, [
        (f"{med['coleta_s']:.0f}s", "COLETA READ-ONLY"),
        (f"{med['pipeline_s']:.1f}s", "ANALISE + RELATORIOS"),
        (f"{med['segundos']:.0f}s", "A MAQUINA INTEIRA"),
        (f"{c['checks']}", "CHECKS NESSE TEMPO"),
    ], icones=["banco", "engrenagem", "tempo", "check"], alt=1.66)
    _lista_cartoes(k, s, topo + 2.0, [
        (f"{med['segundos']:.0f} segundos para tudo que e automatizavel",
         f"Coletar os {c['checks']} checks, analisar, priorizar e gerar "
         "dashboard, PPTX e relatorio. O que sobra para a pessoa e decidir."),
        ("Nao e estimativa - e cronometro",
         (f"Medido em {med['medido_em']} e gravado em disco: "
          f"{med['coleta_amostras']} coleta(s) real(is) + mediana de "
          f"{med['pipeline_amostras']} execucoes do pipeline."
          if med["medido"] else
          "ATENCAO: sem medicao travada. Rode 'APRESENTAR.py --medir'.")),
        ("Qualquer um repete",
         "O comando esta no repositorio, roda offline e nao toca em banco."),
    ], altura=0.95, icones=["raio", "tempo", "check"])

    # ── tempo por senioridade ────────────────────────────────
    s, topo = novo(
        "Tempo de atendimento por senioridade",
        "o mesmo checklist, do preparo ao chamado escrito",
        "O slide inteiro cabe numa frase: o junior com a ferramenta entrega "
        "mais rapido do que o senior sem ela. Se alguem contestar os minutos, "
        "va direto para o slide de premissas - o metodo aguenta a discussao "
        "melhor que o numero.")
    _barras_comparativas(k, s, topo + 0.05, [
        (imp.ROTULO[nv],
         (niv[nv]["manual"], f"na mao   {imp.mm(niv[nv]['manual'])}"),
         (niv[nv]["nosso"],
          f"com o framework   {imp.mm(niv[nv]['nosso'])}   "
          f"({med['segundos']:.0f}s de maquina + "
          f"{imp.mm(niv[nv]['humano'])} de leitura e revisao)"),
         f"{imp.mm(niv[nv]['economia'])} a menos por atendimento  "
         f"({niv[nv]['vezes']:.1f}x mais rapido)")
        for nv in imp.NIVEIS
    ], maximo=max(niv[nv]["manual"] for nv in imp.NIVEIS))
    texto(s, MARG, topo + 4.35, w(12), 0.6,
          f"O junior com o framework ({imp.mm(niv['junior']['nosso'])}) entrega "
          f"antes do senior sem ele ({imp.mm(niv['senior']['manual'])}). E "
          f"repare: do lado direito, so {med['segundos']:.0f}s sao maquina - o "
          "resto ja e o DBA lendo e decidindo.",
          12.5, True, espaco=1.25)

    # ── MTTA e MTTR ────────────────────────────────────────
    s, topo = novo(
        "MTTA e MTTR", "o minuto economizado sai do COMECO do chamado",
        "Traduza o ganho para as duas metricas que a gestao ja acompanha. "
        "Seja preciso: nao prometemos resolver o incidente mais rapido - "
        "apagamos a fase de juntar contexto, que a literatura aponta como a "
        "primeira meia hora de qualquer incidente. E como essa fase e "
        "justamente a que atrasa o primeiro retorno, o MTTA cai junto.")
    _numeros_faixa(k, s, topo + 0.1, [
        ("3-5h", "MTTR MEDIO DE MERCADO"),
        ("1-2h", "ALVO NO SETOR FINANCEIRO"),
        ("30-60min", "SO PARA JUNTAR CONTEXTO"),
        ("55%", "DOS INCIDENTES PASSAM DE 2H"),
    ], icones=["tempo", "alvo", "fila", "grafico"], alt=1.66)
    _lista_cartoes(k, s, topo + 2.0, [
        ("MTTA: o primeiro retorno deixa de ser vazio",
         "A coleta inteira cabe na janela do aceite - que sai com evidencia "
         "e achado priorizado anexados, em vez de 'estamos verificando'."),
        ("MTTR: a fase que cortamos e a primeira",
         "Juntar contexto sai de dezenas de minutos para segundos. O "
         "cronometro do diagnostico so anda a partir dai."),
        ("E o ganho se repete",
         "Cada caso, cada plantao. E a evidencia fica gravada: reabrir o caso "
         "nao custa nova coleta."),
    ], altura=0.95, icones=["seta", "tempo", "grafico"])

    # ── represamento ──────────────────────────────────────
    cap = imp.capacidade(c)
    cn = cap["niveis"]
    s, topo = novo(
        "Represamento", "o mesmo dado, na unidade em que a dor e sentida",
        "Se so um slide sobreviver ao corte de tempo, que seja este. Tempo "
        "por caso convence tecnico; fila que nao anda convence gestao. Faca a "
        "conta em voz alta - ela e simples de proposito, para poder ser "
        "conferida no papel ali mesmo.")
    _barras_comparativas(k, s, topo + 0.05, [
        (imp.ROTULO[nv],
         (cn[nv]["por_dia_manual"],
          f"na mao   {cn[nv]['por_dia_manual']:.1f} casos por turno"),
         (cn[nv]["por_dia_nosso"],
          f"com o framework   {cn[nv]['por_dia_nosso']:.0f} casos por turno"),
         f"fila de {cap['fila']} chamados: "
         f"{cn[nv]['fila_dias_manual']:.1f} turnos na mao  ->  "
         f"{imp.mm(cn[nv]['fila_min_nosso'])} com o framework")
        for nv in imp.NIVEIS
    ], maximo=max(cn[nv]["por_dia_nosso"] for nv in imp.NIVEIS))
    texto(s, MARG, topo + 4.35, w(12), 0.6,
          f"A fila que hoje ocupa {cn['pleno']['fila_dias_manual']:.0f} turnos "
          f"de um pleno cabe em {imp.mm(cn['pleno']['fila_min_nosso'])} - menos "
          f"de um turno. Base: {imp.mm(cap['jornada_min'])} liquidos por dia, "
          "ja descontando reuniao e interrupcao.",
          12.5, True, espaco=1.25)

    # ── cobertura ────────────────────────────────────────────
    s, topo = novo(
        "Mais rapido cobrindo mais",
        "tempo menor costuma significar analise menor - aqui nao",
        "Antecipe a objecao obvia: 'e claro que e mais rapido, deve olhar "
        "menos'. E o contrario. A comparacao de tempo ja era generosa com o "
        "lado manual porque assumiu que ele cobre 16 itens - e mesmo assim "
        "sao 4,6 vezes menos que o catalogo.")
    _numeros_faixa(k, s, topo + 0.1, [
        (c["checks_na_mao"], "ITENS NA ROTINA MANUAL"),
        (c["checks"], "CHECKS NO CATALOGO"),
        (f"{c['cobertura_vezes']:.1f}x", "MAIS COBERTURA"),
        (f"{niv['pleno']['checks_min_nosso']:.0f}", "CHECKS POR MINUTO (PLENO)"),
    ], icones=["pessoas", "banco", "grafico", "raio"], alt=1.66)
    _lista_cartoes(k, s, topo + 2.0, [
        ("A cobertura para de depender de quem atendeu",
         f"Os {c['checks']} checks rodam iguais nos tres niveis. As 3h da "
         "manha, a lista que mora na cabeca encolhe."),
        ("Sai da area de performance",
         "Recuperabilidade, integridade, conformidade de parametros e "
         f"{n['erros_ora']} erros ORA- catalogados."),
        ("Nada de placar verde por falta de dado",
         "Check recusado pelo banco ou analisador que falhou aparece no topo "
         "do relatorio."),
    ], altura=0.95, icones=["pessoas", "escudo", "check"])

    # ── assertividade ────────────────────────────────────────
    s, topo = novo(
        "Assertividade", "a mesma evidencia leva a mesma conclusao",
        "Aqui o argumento nao e velocidade, e acerto. Use os numeros de "
        "mercado como referencia do EFEITO (roteiro padronizado + base de "
        "conhecimento), nao como promessa nossa - a nossa medicao propria "
        "vem depois, com o historico de casos.")
    _lista_cartoes(k, s, topo, [
        ("Prioridade calculada, nao opinada",
         "Ordenado por severidade e por tempo de banco consumido "
         "(DB time / wait class)."),
        ("O caminho ja testado nao se repete",
         "Hipotese descartada fica gravada com o motivo - inclusive quando o "
         "caso troca de dono."),
        ("Reincidencia aparece marcada",
         "Achado repetido no mesmo banco vem sinalizado: o paliativo cronico "
         "vira problema tratavel."),
        (f"{n['playbooks']} playbooks ligados ao achado",
         "O erro ORA- sugere o passo a passo, com o risco de cada comando "
         "declarado antes de rodar."),
    ], altura=1.0, icones=["alvo", "seta", "grafico", "doc"])
    texto(s, MARG, topo + 4.62, w(12), 0.5,
          "Referencia de mercado para o efeito de roteiro padronizado + base "
          "de conhecimento: resolucao no primeiro contato de 48% para 72% e "
          "-55% de escalonamento (Supportbench).",
          10.5, False, _P["mut"], espaco=1.2)

    # ── padronizacao ─────────────────────────────────────────
    s, topo = novo(
        "Padronizacao", "o mesmo chamado, escrito do mesmo jeito, sempre",
        "Se houver gestor na sala, este e o slide dele: e o que torna o "
        "trabalho comparavel entre pessoas e entre meses. Sem padrao nao ha "
        "indicador - ha anedota.")
    _bullets(k, s, topo + 0.25, [
        "100% dos casos no mesmo Template Padrao. O que falta sai marcado "
        "como <preencher>, nao preenchido no chute.",
        "Toda acao com data, hora e risco declarado: o relatorio e "
        "transcricao, nao memoria do fim do dia.",
        "Consolidado do dia em planilha: o que fechou, o que ficou aberto e "
        "com quem.",
        "A auditoria le o caso de janeiro e o de julho no mesmo formato.",
        f"Travado por {n['testes']} testes e por um corpus de regressao.",
    ], tam=14, salto=0.86)

    # ── diferenciais ─────────────────────────────────────────
    s, topo = novo(
        "Nossos diferenciais",
        "por que isso nao e OEM, nao e ORAchk e nao e script de DBA",
        "Alguem VAI perguntar 'ja nao temos OEM?'. Responda antes de "
        "perguntarem, e sem atacar as outras ferramentas: elas respondem "
        "outra pergunta. OEM monitora, ORAchk audita configuracao, script "
        "coleta. Nenhum dos tres conduz o caso ate o texto do chamado.")
    _lista_cartoes(k, s, topo, [
        ("Entrega o caso pronto, nao um monte de saida",
         "Da coleta ao texto do chamado, no mesmo fluxo. As outras param na "
         "saida."),
        ("Sem licenca de pack e sem instalar nada",
         "O modo Parcial nao consome Diagnostics/Tuning. Sem agente, sem "
         "Oracle Client, roda ate por spool no Citrix."),
        ("Read-only por construcao",
         "Catalogo de checks fixo e versionado, advisor so por opt-in "
         "confirmado, toda acao com risco e hora."),
        ("Se prova sozinho",
         f"{n['testes']} testes, corpus de regressao e bateria contra bancos "
         "reais. Check e playbook sao JSON: o time estende sem Python."),
    ], altura=1.0, icones=["doc", "raio", "escudo", "check"])
    texto(s, MARG, topo + 4.62, w(12), 0.4,
          "Nao substituimos o que ja existe - ocupamos o vao entre o alerta "
          "que chega e o chamado que precisa ser respondido.",
          12.5, True, _P["brand"])

    # ── premissas ────────────────────────────────────────────
    s, topo = novo(
        "Como esses numeros foram calculados",
        "o que foi contado, o que foi medido e o que e premissa",
        "NAO pule este slide para ganhar tempo. Ele e o que separa este deck "
        "de uma peca de marketing: mostra o metodo e convida a ajustar a "
        "premissa. Se alguem disser 'na minha rotina sao 2 minutos por "
        "check', anote e regere o deck - o numero vem de um lugar so.")
    prem = imp.PREMISSAS
    _lista_cartoes(k, s, topo, [
        ("CONTADO do repositorio",
         f"{c['checks']} checks vem de mcp/catalog.json e {n['testes']} testes "
         "de tests/. Sao recontados a cada geracao do deck - numero escrito a "
         "mao envelhece calado."),
        ("MEDIDO nas execucoes reais",
         (f"{med['segundos']:.0f}s de maquina por caso, travados em disco em "
          f"{med['medido_em']}: coleta de {med['coleta_amostras']} "
          f"execucao(oes) real(is) mais o pipeline de analise e relatorios, "
          f"mediana de {med['pipeline_amostras']} execucoes."
          if med["medido"] else
          f"O deck usa {imp.mm(imp.COLETA_PADRAO_MIN)} - premissa "
          "conservadora, porque esta copia ainda nao tem medicao travada. "
          "'APRESENTAR.py --medir' cronometra offline e poe o numero real "
          "no lugar.")),
        ("PREMISSA declarada (o lado manual)",
         "Por check, incluindo lembrar a query, rodar, ler e anotar: "
         + ", ".join(f"{imp.ROTULO[nv]} {prem[nv]['manual_por_check']:g} min"
                     for nv in imp.NIVEIS)
         + ". Mais o preparo do ambiente e o tempo de escrever o chamado, "
         f"sobre {c['checks_na_mao']} itens de checklist."),
        ("O placar foi inclinado a favor do lado manual",
         "Usamos a maior lista de checklist publicada (16 itens), o ritmo de "
         "quem ja tem os scripts prontos, e contamos do nosso lado o tempo "
         "humano de ler o dashboard e revisar o texto."),
    ], altura=1.15, icones=["banco", "tempo", "pessoas", "escudo"])

    # ── resumo de uma tela ───────────────────────────────────
    # Existe para ficar PROJETADO durante as perguntas. Sem ele, o slide no ar
    # na hora do debate e o ultimo que voce mostrou - normalmente o de fontes,
    # que nao ajuda ninguem a lembrar do argumento.
    s, topo = novo(
        "Em uma tela", "o que levar desta apresentacao",
        "Deixe ESTE slide no ar durante as perguntas. Enquanto voce responde, "
        "a plateia continua lendo os cinco numeros - e sai da sala com eles, "
        "nao com a ultima duvida levantada.")
    _numeros_faixa(k, s, topo + 0.15, [
        (f"{med['segundos']:.0f}s", "A MAQUINA INTEIRA"),
        (f"{niv['pleno']['vezes']:.1f}x", "MAIS RAPIDO (PLENO)"),
        (f"{c['cobertura_vezes']:.1f}x", "MAIS COBERTURA"),
        (f"{cn['pleno']['por_dia_nosso']:.0f}", "CASOS POR TURNO"),
        ("100%", "NO MESMO TEMPLATE"),
    ], icones=["tempo", "raio", "banco", "fila", "doc"], alt=1.78)
    _bullets(k, s, topo + 2.25, [
        f"O junior com a ferramenta ({imp.mm(niv['junior']['nosso'])}) entrega "
        f"antes do senior sem ela ({imp.mm(niv['senior']['manual'])}).",
        f"A fila de {cap['fila']} chamados sai de "
        f"{cn['pleno']['fila_dias_manual']:.0f} turnos para "
        f"{imp.mm(cn['pleno']['fila_min_nosso'])}.",
        "Read-only por construcao, sem consumir pack e sem instalar nada.",
        "Todo numero deste deck e contado, medido ou premissa declarada.",
    ], tam=14, salto=0.62)
    texto(s, MARG, topo + 5.05, w(12), 0.4,
          "Pedido: todo chamado de banco aberto com um caso no framework. "
          "Primeiro caso conduzido junto, nesta semana.",
          13, True, _P["brand"])

    # ── fontes ───────────────────────────────────────────────
    s, topo = novo(
        "Referencias publicas", "de onde vem a ordem de grandeza",
        "Slide de consulta, nao de leitura. Passe rapido dizendo que as "
        "premissas conversam com o que o mercado publica, e ofereca o deck "
        "para quem quiser conferir depois.")
    for i, (frase, fonte) in enumerate(imp.FONTES):
        t = topo + i * 0.68
        k["ponto"](s, MARG + 0.02, t + 0.14, 0.08, _P["brand"])
        texto(s, MARG + 0.28, t, w(12) - 0.3, 0.42, frase, 11, False,
              espaco=1.18)
        texto(s, MARG + 0.28, t + 0.4, w(12) - 0.3, 0.22, fonte, 9, False,
              _P["mut"])


# ── pitch de 3 slides ─────────────────────────────────────────
# Deck separado dos outros, e nao um quarto `publico`, por um motivo pratico:
# os decks de PUBLICOS tem uma espinha comum (ciclo, seguranca, demo, pedido) e
# 9+ slides. Este aqui e o oposto - tres telas para uma janela de corredor ou o
# primeiro contato com quem decide. Um slide a mais aqui ja e o deck errado.
#
# A ordem responde as tres perguntas de quem compra, nesta sequencia: quanto
# tempo isso me devolve, como voce prova, e por que nao e o que eu ja tenho.
def build_pitch(out_path: str | None = None) -> str:
    """Tres slides para VENDER o framework. Retorna o caminho do .pptx.

    Todo numero vem de `numeros()` (contado do repositorio) ou de `impacto.py`
    (medido, ou premissa declarada) - o mesmo material do deck de impacto. Se a
    medicao ainda nao foi travada, o slide DIZ que o tempo e premissa em vez de
    apresentar estimativa com cara de cronometro.
    """
    from pptx import Presentation
    from pptx.util import Inches

    from . import impacto as imp

    out_path = out_path or os.path.join(OUTPUT_DIR, "FRAMEWORK_VENDAS.pptx")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]
    k = _pptx_kit(prs)
    texto, barra, card = k["texto"], k["barra"], k["card"]
    cabecalho, rodape, fundo = k["cabecalho"], k["rodape"], k["fundo"]
    anel, forma = k["anel"], k["forma"]
    MARG, w, C = k["MARG"], k["w"], k["ALIGN"].CENTER

    n = numeros()
    med = imp.tempo_maquina()
    c = imp.comparativo(n["checks"], med["minutos"])
    niv = c["niveis"]
    cap = imp.capacidade(c)
    cn = cap["niveis"]
    rot = "Framework de Troubleshooting Oracle  |  o ganho em 3 telas"
    cont = [0]

    def novo(titulo="", sub="", nota=""):
        s = prs.slides.add_slide(blank)
        fundo(s)
        topo = cabecalho(s, titulo, sub) if titulo else 0.5
        cont[0] += 1
        rodape(s, cont[0], rot)
        # A marca acompanha TODO slide: o deck e projetado e reenviado, e slide
        # sem assinatura circula sem dono. Fica no canto direito do cabecalho,
        # onde nao disputa com o titulo.
        marca.pptx_assinatura(k, s, 11.16, 0.34, 0.44, _P["txt"],
                              _P["brand"], 12)
        if nota:
            s.notes_slide.notes_text_frame.text = nota
        return s, topo

    # ── 1. o numero que abre a conversa ──────────────────────
    s = prs.slides.add_slide(blank)
    fundo(s)
    cont[0] += 1
    barra(s, 0, 0, 13.333, 0.28, _P["brand"])
    # Logo grande na capa: e o slide que a plateia ve antes de voce falar.
    marca.pptx_assinatura(k, s, MARG, 0.52, 0.78, _P["txt"], _P["brand"], 20)
    # Banner Oracle: diz de que mundo e o problema antes da primeira frase.
    marca.pptx_banner_oracle(k, s, 8.35, 0.58, 4.38, 0.62)
    texto(s, MARG, 1.62, 7.5, 1.7,
          "Do alerta ao chamado\nescrito, em minutos.", 34, True, espaco=1.04)
    barra(s, MARG, 3.30, 1.0, 0.05, _P["brand"])
    texto(s, MARG, 3.52, 7.5, 1.9,
          f"O tempo de analise que hoje ocupa "
          f"{imp.mm(niv['pleno']['manual'])} de um DBA pleno cabe em "
          f"{imp.mm(niv['pleno']['nosso'])} - e destes, so "
          f"{med['segundos']:.0f}s sao maquina.\nO resto ja e o DBA lendo o "
          "diagnostico pronto e decidindo.",
          14.5, False, _P["mut"], espaco=1.32)

    # Anel a direita: a fracao do tempo antigo que sobra com a ferramenta.
    # E o unico grafico da capa de proposito - dois graficos aqui competiriam.
    card(s, 8.35, 1.42, 4.38, 3.91, _P["surf"], _P["line"], raio=0.05)
    barra(s, 8.35, 1.42, 4.38, 0.07, _P["brand"])
    fracao = (niv["pleno"]["nosso"] / niv["pleno"]["manual"]
              if niv["pleno"]["manual"] else 0)
    anel(s, 9.79, 1.70, 1.42, fracao, _P["brand"], espessura=0.19)
    texto(s, 9.79, 2.14, 1.42, 0.44, f"{fracao * 100:.0f}%", 22, True,
          _P["brand"], align=C)
    texto(s, 8.6, 3.20, 3.88, 0.6,
          "DO TEMPO ANTERIOR\nE O QUE AINDA SE GASTA", 9.5, True, _P["mut"],
          align=C, espaco=1.25)
    texto(s, 8.6, 4.06, 3.88, 0.9,
          f"{niv['pleno']['vezes']:.1f}x mais rapido, cobrindo "
          f"{c['cobertura_vezes']:.1f}x mais itens que a rotina manual.",
          12, True, align=C, espaco=1.25)
    _numeros_faixa(k, s, 5.55, [
        (f"{med['segundos']:.0f}s", "A MAQUINA INTEIRA"),
        (f"{niv['pleno']['vezes']:.1f}x", "MAIS RAPIDO (PLENO)"),
        (f"{c['checks']}", "CHECKS READ-ONLY"),
        (f"{cn['pleno']['por_dia_nosso']:.0f}", "CASOS POR TURNO"),
    ], icones=["tempo", "raio", "banco", "fila"], alt=1.16)
    forma(s, k["SHAPE"].RECTANGLE, MARG, 6.92, 12.13, 0.012, _P["line"])
    texto(s, MARG, 7.0, 9.0, 0.24,
          "Coleta read-only  |  sem instalar nada  |  sem consumir "
          "Diagnostics/Tuning Pack no modo Parcial", 9, False, _P["mut"])
    texto(s, 9.6, 7.0, 3.13, 0.24, marca.TAGLINE.replace("&middot;", "\u00b7"),
          9, False, _P["mut"], align=k["ALIGN"].RIGHT)
    s.notes_slide.notes_text_frame.text = (
        "Abra pelo numero e pare de falar por dois segundos: o tempo de "
        f"analise sai de {imp.mm(niv['pleno']['manual'])} para "
        f"{imp.mm(niv['pleno']['nosso'])} num DBA pleno. Depois faca a "
        f"distincao que sustenta o resto: {med['segundos']:.0f}s sao maquina "
        "(cronometrados) e o resto e a pessoa lendo o diagnostico - nao "
        "estamos prometendo substituir ninguem, estamos apagando a fase de "
        "juntar contexto.")

    # ── 2. a evidencia do ganho ──────────────────────────────
    s, topo = novo(
        "O ganho de tempo, por senioridade",
        "o mesmo checklist, do preparo ate o chamado escrito",
        "Este e o slide de prova. A frase que fecha: o junior COM a ferramenta "
        f"({imp.mm(niv['junior']['nosso'])}) entrega antes do senior SEM ela "
        f"({imp.mm(niv['senior']['manual'])}) - o resultado deixou de depender "
        "de quem esta de plantao. Se contestarem os minutos, va para o metodo "
        "no pe do slide: o lado manual usa a maior lista de checklist "
        "publicada e o ritmo de quem ja tem os scripts prontos.")
    _barras_comparativas(k, s, topo + 0.02, [
        (imp.ROTULO[nv],
         (niv[nv]["manual"], f"na mao   {imp.mm(niv[nv]['manual'])}"),
         (niv[nv]["nosso"],
          f"com o framework   {imp.mm(niv[nv]['nosso'])}   "
          f"({med['segundos']:.0f}s de maquina + "
          f"{imp.mm(niv[nv]['humano'])} de leitura e revisao)"),
         f"{imp.mm(niv[nv]['economia'])} a menos por atendimento  "
         f"({niv[nv]['vezes']:.1f}x mais rapido)")
        for nv in imp.NIVEIS
    ], maximo=max(niv[nv]["manual"] for nv in imp.NIVEIS))
    texto(s, MARG, topo + 4.32, w(12), 0.56,
          f"O junior com o framework ({imp.mm(niv['junior']['nosso'])}) entrega "
          f"antes do senior sem ele ({imp.mm(niv['senior']['manual'])}). "
          f"A fila de {cap['fila']} chamados sai de "
          f"{cn['pleno']['fila_dias_manual']:.0f} turnos para "
          f"{imp.mm(cn['pleno']['fila_min_nosso'])}.",
          13, True, espaco=1.25)
    texto(s, MARG, topo + 4.98, w(12), 0.62,
          ("Metodo: os "
           f"{c['checks']} checks e os {n['testes']} testes sao CONTADOS do "
           f"repositorio; os {med['segundos']:.0f}s de maquina foram MEDIDOS "
           + (f"e travados em disco em {med['medido_em']}"
              if med["medido"] else
              "- esta copia ainda usa a premissa conservadora "
              f"de {imp.mm(imp.COLETA_PADRAO_MIN)}")
           + f"; o lado manual e PREMISSA declarada sobre "
           f"{c['checks_na_mao']} itens de checklist - a maior lista publicada, "
           "com o ritmo de quem ja tem os scripts prontos."),
          10, False, _P["mut"], espaco=1.22)

    # ── 3. por que somos diferentes + o pedido ───────────────
    s, topo = novo(
        "Por que isso nao e OEM, ORAchk nem script de DBA",
        "as vantagens que sustentam o numero do slide anterior",
        "Alguem VAI dizer 'ja temos OEM'. Responda antes de perguntarem, e sem "
        "atacar as outras ferramentas: elas respondem outra pergunta. OEM "
        "monitora, ORAchk audita configuracao, script coleta - nenhum dos tres "
        "conduz o caso ate o texto do chamado. Termine no pedido, com data: "
        "um primeiro caso conduzido junto nesta semana.")
    _lista_cartoes(k, s, topo, [
        ("Entrega o CASO pronto, nao um monte de saida",
         "Da coleta ao texto do chamado no mesmo fluxo: dashboard com Health "
         "Score, plano de acao priorizado e o relatorio no Template Padrao. "
         "As outras ferramentas param na saida."),
        ("Raciocina offline, e nao inventa",
         f"O motor mantem as hipoteses vivas, pontua com a evidencia e "
         f"REFUTA - com {n['playbooks']} playbooks ligados ao achado e "
         f"{n['erros_ora']} erros ORA- catalogados. Sem nuvem, sem enviar "
         "dado do banco para fora e sem texto plausivel inventado."),
        ("Read-only por construcao",
         "Catalogo de checks fixo e versionado, o unico comando que escreve "
         "(SQL Tuning Advisor) exige opt-in confirmado, senha nunca gravada e "
         "toda acao registrada com risco e hora."),
        ("Se prova sozinho",
         f"{n['testes']} testes automatizados, corpus de regressao offline e "
         "bateria contra bancos reais. Check e playbook sao JSON: o time "
         "estende sem escrever Python."),
    ], altura=1.18, icones=["doc", "alvo", "escudo", "check"])
    texto(s, MARG, topo + 5.06, w(12), 0.52,
          "Pedido: todo chamado de banco aberto com um caso no framework - e "
          "o primeiro caso conduzido junto, nesta semana.", 13, True,
          _P["brand"])

    prs.save(out_path)
    return out_path


def _deck_tecnico(k, novo, n):
    """Slides so da versao tecnica: arquitetura, conducao, prova e extensao."""
    texto, w, MARG = k["texto"], k["w"], k["MARG"]

    s, topo = novo(
        "Arquitetura", "quatro pecas, um contrato entre elas",
        "O ponto a fixar: as pecas so conversam pelo modelo do caso. E isso "
        "que permite acrescentar um analisador sem tocar em relatorio, "
        "painel ou PPTX.")
    _lista_cartoes(k, s, topo, [
        ("Coletor",
         f"{n['sql']} arquivos SQL e {n['checks']} checks catalogados em "
         "mcp/catalog.json. Grava evidencia em texto na pasta do caso - "
         "SQL*Net direto ou spool pelo Citrix."),
        ("Analisadores",
         f"{n['analisadores']} modulos independentes. Cada um recebe os "
         "arquivos que reconhece e devolve secoes e achados com severidade. "
         "Falha de um nao derruba os outros."),
        ("Modelo do caso",
         "CaseResult/Section/Finding: a estrutura unica que alimenta "
         "dashboard, relatorio, PPTX, exports e Health Score."),
        ("Ciclo de vida",
         "meta.json por caso: hipoteses, acoes com risco, status, causa "
         "raiz. E a fonte do 'redija' e do consolidado do dia."),
    ], altura=1.15, icones=["banco", "engrenagem", "grafico", "alvo"])

    s, topo = novo(
        "Conduzir o caso", "Method R aplicado no dia a dia",
        "Se houver DBA senior na sala, este e o slide de conversa: pergunte "
        "como cada um prioriza hoje e conecte com DB time / wait class.")
    _bullets(k, s, topo + 0.1, [
        "Priorize por DB time e wait class, nao por palpite: o que consome "
        "tempo do banco e o que merece a proxima hora do seu dia.",
        "Uma hipotese por vez, testada com o dado coletado. Se o dado nao "
        "confirma, registre o descarte e avance.",
        "Toda acao entra com o risco declarado (nenhum / baixo / medio / "
        "alto) e o carimbo de hora.",
        "Risco alto exige aviso de impacto e plano de rollback antes de "
        "executar - a ferramenta cobra isso no texto do relatorio.",
        "Fechar o caso e redigir acontecem na mesma tela: o template sai "
        "pronto, com o que falta marcado.",
    ], tam=13, salto=0.72)

    s, topo = novo(
        "Base de conhecimento",
        f"{n['playbooks']} playbooks com o risco de cada passo",
        "Convide a contribuir aqui: playbook e JSON, nao codigo. O time "
        "escrever os proprios playbooks e o que tira o conhecimento da "
        "cabeca das pessoas.")
    _bullets(k, s, topo + 0.1, [
        "Cada playbook e um passo a passo com o comando e o rotulo de risco: "
        "leitura, mudanca ou destrutivo.",
        f"Os playbooks sao ligados a codigos ORA- ({n['erros_ora']} erros "
        "catalogados): o achado sugere o playbook sozinho.",
        "A KB nao executa nada. Ela mostra o comando para voce revisar e "
        "rodar onde for devido.",
        "Playbook novo e um JSON validado por VALIDAR_KB.py - contribuir nao "
        "exige mexer em codigo Python.",
    ], tam=13, salto=0.72)

    s, topo = novo(
        "Como o framework se prova",
        "o que separa 'script do DBA' de ferramenta",
        "Este slide responde a objecao silenciosa da sala: 'mais um script "
        "que quebra quando o autor sair'. Corpus e suite sao a resposta.")
    _lista_cartoes(k, s, topo, [
        ("Suite de testes",
         f"{n['testes']} testes automatizados. Cada bug corrigido entra com "
         "o teste que o pega de novo, e o comentario diz por que ele existe."),
        ("Corpus de regressao offline",
         "Reanalisa evidencia ja gravada e compara com o baseline: mudanca "
         "de codigo que altera diagnostico aparece antes de ir para o campo. "
         "Nao toca em banco nenhum."),
        ("Bateria de homologacao",
         "Roda a coleta real contra a lista de servidores de homologacao e "
         "aponta traceback, artefato ausente ou check recusado pelo banco."),
        ("Analise incompleta e declarada",
         "Se um analisador falha, o dashboard e o relatorio dizem isso no "
         "topo. Score verde por falta de analise e pior que score ruim."),
    ], altura=1.15, icones=["check", "raio", "banco", "escudo"])

    s, topo = novo(
        "Estender", "o caminho mais curto para contribuir",
        "Feche com um convite concreto: peca a cada pessoa um check ou um "
        "playbook do que ela ja domina. Entrega com teste, sempre.")
    _bullets(k, s, topo + 0.1, [
        "Check novo: acrescente o SQL em queries/ e a entrada em "
        "mcp/catalog.json, marcando se consome pack.",
        "Analisador novo: uma classe com match() e analyze(), registrada em "
        "analyzers/__init__.py. O resto do pipeline nao muda.",
        "Playbook novo: um JSON na KB, com o risco de cada passo, validado "
        "por VALIDAR_KB.py.",
        "Regra: entrega com teste. A suite roda em segundos e e o que "
        "permite mexer no framework sem medo.",
    ], tam=13, salto=0.72)
    texto(s, MARG, topo + 3.5, w(12), 0.5,
          f"Exemplo usado nesta apresentacao: caso {CASO_EX} no servidor "
          f"{SRV_EX} (ficticios - o deck sai da maquina).",
          11, False, _P["mut"])
