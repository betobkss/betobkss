"""
Roteiro de apresentacao do deck de IMPACTO - o que fazer e o que falar.

As notas do apresentador (dentro do .pptx) dizem o ARGUMENTO de cada slide.
Este arquivo e outra coisa: o **passo a passo da apresentacao inteira** - o que
preparar antes, quanto tempo gastar em cada slide, a frase literal de abertura,
as perguntas dificeis com a resposta pronta e como fechar pedindo uma decisao.

Duas decisoes que sustentam este modulo:

1. **Os numeros vem de `impacto.py`, nunca digitados aqui.** Um roteiro que
   diz "1h44" enquanto o slide projetado diz outra coisa e pior que roteiro
   nenhum: quem apresenta perde o chao no meio da frase.
2. **A ordem dos topicos e a ordem real dos slides**, travada por teste. Deck
   e roteiro divergirem e o jeito mais rapido de transformar preparacao em
   gagueira.

Sai em Markdown de proposito: e para ser lido no celular, impresso ou colado
no OneNote na vespera - nao para virar mais um artefato que precisa de
ferramenta para abrir.
"""
from __future__ import annotations

import os

from . import impacto as imp
from .apresentacao import OUTPUT_DIR, numeros

# Titulos na ORDEM em que os slides saem de `_deck_impacto`. O teste
# `test_roteiro.py` compara esta lista com os titulos do .pptx gerado - se
# alguem acrescentar um slide e esquecer o roteiro, a suite avisa.
ORDEM = [
    "Capa",
    "O problema",
    "O ciclo",
    "Por que e seguro rodar",
    "O tempo da maquina",
    "Tempo de atendimento por senioridade",
    "MTTA e MTTR",
    "Represamento",
    "Mais rapido cobrindo mais",
    "Assertividade",
    "Padronizacao",
    "Nossos diferenciais",
    "Como esses numeros foram calculados",
    "Em uma tela",
    "Referencias publicas",
    "Demonstracao",
    "O que estamos pedindo",
]

# Slides que podem cair se o tempo apertar, em ordem de sacrificio. O que NAO
# entra nesta lista tambem e uma decisao: seguranca, premissas e o pedido final
# ficam ate o fim - sao eles que separam apresentacao de propaganda.
CORTAVEIS = ["Referencias publicas", "Mais rapido cobrindo mais",
             "Demonstracao", "O ciclo"]

# O slide que fica projetado enquanto a plateia pergunta. Nao entra em
# CORTAVEIS de proposito: e barato (1 minuto) e e o que a sala leva na memoria.
SLIDE_DE_PERGUNTAS = "Em uma tela"


def _preparacao() -> list[str]:
    return [
        "### D-2 | Prepare o material",
        "",
        "1. Gere os artefatos novos: `.\\.venv\\Scripts\\python.exe "
        "APRESENTAR.py --impacto` (o deck e este roteiro saem juntos). Os "
        "numeros sao recontados na hora - deck velho na mao e numero velho "
        "na boca.",
        "2. Leia o comparativo que o CLI imprime no terminal ANTES de abrir o "
        "PPTX. Se voce nao defende algum numero, ajuste a premissa em "
        "`troubleshoot/impacto.py` e regere. Nunca apresente numero que voce "
        "nao sustenta.",
        "3. Escolha **um caso real que voce conduziu** e prepare a versao "
        "mascarada dele para a demonstracao (banco de homologacao ou nomes "
        "trocados).",
        "4. Descubra quem estara na sala. Se houver DBA senior, o slide de "
        "seguranca vira o mais importante; se for so gestao, o de "
        "represamento.",
        "",
        "### D-1 | Ensaie",
        "",
        "1. Apresente em voz alta, cronometrando. Duas vezes. A primeira "
        "sempre estoura o tempo.",
        "2. Ensaie a demonstracao separadamente, com o caso JA coletado "
        "aberto. Coletar ao vivo depende de rede, senha e sorte.",
        "3. Decore so as frases marcadas com `FALE ASSIM` neste roteiro. "
        "O resto e seu, com as suas palavras - texto decorado inteiro soa "
        "falso e trava na primeira interrupcao.",
        "4. Salve o dashboard do caso em HTML e deixe aberto numa aba. E o "
        "plano B se o painel travar.",
        "",
        "### 10 minutos antes",
        "",
        "1. Abra: o PPTX, o painel ja no ar e o dashboard HTML de reserva.",
        "2. Feche o que tiver nome de banco de producao na tela - inclusive a "
        "lista de conexoes e o titulo de janelas em segundo plano.",
        "3. Silencie notificacoes. Um alerta de chamado real aparecendo no "
        "meio do slide de seguranca e uma ironia cara.",
        "4. Deixe agua por perto e o cronometro do celular a vista.",
    ]


def _numeros_de_cor(c: dict, cap: dict, n: dict, med: dict) -> list[str]:
    """Os poucos numeros que precisam sair sem consultar o slide."""
    niv, cn = c["niveis"], cap["niveis"]
    origem = (f"cronometrado em {med['medido_em']}: coleta de "
              f"{med['coleta_amostras']} execucao(oes) real(is) + pipeline "
              f"com mediana de {med['pipeline_amostras']} execucoes"
              if med["medido"] else
              "premissa conservadora - **sem medicao travada nesta copia**")
    linhas = [
        "| Numero | Valor | De onde vem |",
        "| --- | --- | --- |",
        f"| Checks do catalogo | **{c['checks']}** | contado de "
        "`mcp/catalog.json` |",
        f"| Itens de um checklist manual | **{c['checks_na_mao']}** | maior "
        "lista publica que achamos |",
        f"| Cobertura | **{c['cobertura_vezes']:.1f}x** | os dois acima |",
        f"| Tempo de MAQUINA (coleta + analise + relatorios) | "
        f"**{med['segundos']:.0f}s** | {origem} |",
    ]
    for nv in imp.NIVEIS:
        linhas.append(
            f"| {imp.ROTULO[nv]}: na mao -> com o framework | "
            f"**{imp.mm(niv[nv]['manual'])} -> {imp.mm(niv[nv]['nosso'])}** "
            f"({niv[nv]['vezes']:.1f}x) | modelo em `impacto.py` |")
    linhas += [
        f"| Fila de {cap['fila']} chamados (pleno) | "
        f"**{cn['pleno']['fila_dias_manual']:.1f} turnos -> "
        f"{imp.mm(cn['pleno']['fila_min_nosso'])}** | "
        f"{imp.mm(cap['jornada_min'])} liquidos por turno |",
        f"| Testes automatizados | **{n['testes']}** | contado de `tests/` |",
        f"| Playbooks da base de conhecimento | **{n['playbooks']}** | "
        "contado de `kb/` |",
    ]
    return linhas


def _passos(c: dict, cap: dict, n: dict, med: dict) -> list[tuple]:
    """(titulo, minutos, o que fazer, frase literal, pontos, o que evitar)."""
    niv, cn = c["niveis"], cap["niveis"]
    seg = med["segundos"]
    return [
        ("Capa", 0.5,
         "Fique em silencio 3 segundos com a capa no ar. Deixe a plateia ler "
         "os quatro numeros sozinha.",
         "Em vinte minutos eu mostro por que o nosso troubleshooting de banco "
         "deixou de depender de quem esta de plantao - e o que isso faz com o "
         "nosso tempo de atendimento.",
         ["Diga quanto tempo vai levar e que havera demonstracao.",
          "NAO leia os numeros da capa em voz alta: eles voltam depois."],
         "Comecar pedindo desculpa pelo tempo ou pela ferramenta."),

        ("O problema", 1.5,
         "Conte UM caso concreto que a sala viveu, sem nomear pessoas.",
         "Todo mundo aqui ja abriu o mesmo chamado duas vezes e recebeu dois "
         "diagnosticos diferentes. Nao porque alguem errou - porque cada um "
         "de nos tem o seu roteiro na cabeca.",
         ["Objetivo unico deste slide: concordancia. Se ninguem reconhecer o "
          "problema, o resto vira demonstracao de tecnologia.",
          "Olhe para as pessoas, nao para o slide."],
         "Culpar pessoa, time ou plantao. O problema e de processo."),

        ("O ciclo", 1.5,
         "Passe devagar pelas cinco etapas, apontando na tela.",
         "A ordem e o produto: cada etapa deixa registro para a proxima, e por "
         "isso o texto do chamado e transcricao - nao memoria do fim do dia.",
         ["E o slide que a plateia memoriza.",
          "Se alguem perguntar 'quanto tempo leva isso?', responda 'e o "
          "proximo slide' e siga."],
         "Detalhar comando ou parametro aqui. Isso e o deck tecnico."),

        ("Por que e seguro rodar", 2.0,
         "Se houver DBA senior ou risco/compliance na sala, este e o slide "
         "mais importante. Nao acelere.",
         "Eu nao vou pedir para voces acreditarem que e seguro. Vou mostrar "
         "por que nao ha como nao ser: o catalogo de checks e fixo e "
         "versionado, o unico comando que escreve ficou fora do painel, e a "
         "senha nao existe em arquivo nenhum.",
         ["Diga a palavra 'read-only' pelo menos uma vez olhando para o DBA "
          "mais cetico da sala.",
          "Mencione que licenca de pack e respeitada ANTES que perguntem."],
         "Prometer seguranca sem explicar o mecanismo."),

        ("O tempo da maquina", 2.0,
         "Diga o numero e FIQUE EM SILENCIO tres segundos. Deixe a sala "
         "reagir antes de explicar.",
         f"Tudo que da para automatizar neste processo - coletar os "
         f"{c['checks']} checks, analisar, priorizar, montar o dashboard, o "
         f"PPTX e o relatorio - leva {seg:.0f} segundos. E isso nao e "
         "estimativa: e cronometro.",
         [(f"Medido em {med['medido_em']}, gravado em disco: coleta com "
           f"{med['coleta_amostras']} execucao(oes) real(is), pipeline com "
           f"mediana de {med['pipeline_amostras']} execucoes."
           if med["medido"] else
           "ATENCAO: esta copia esta sem medicao travada e o deck caiu na "
           "premissa. Rode 'APRESENTAR.py --medir' ANTES de apresentar."),
          "Ofereca a verificacao: 'o comando que mede esta no repositorio, "
          "roda offline e nao toca em banco - qualquer um repete'.",
          "Emende no proximo slide: 'e se a maquina leva segundos, o que "
          "sobra do nosso tempo?'"],
         "Arredondar para baixo ou exagerar. O numero medido ja e forte; "
         "inflar destroi tudo que vem depois."),

        ("Tempo de atendimento por senioridade", 2.5,
         "Deixe o slide no ar. Aponte a barra do junior, depois a do senior.",
         f"O junior com a ferramenta entrega em {imp.mm(niv['junior']['nosso'])}. "
         f"O senior sem ela leva {imp.mm(niv['senior']['manual'])}. Nao e que o "
         "junior ficou senior - e que o roteiro parou de morar na cabeca das "
         "pessoas.",
         [f"Pleno: {imp.mm(niv['pleno']['manual'])} viram "
          f"{imp.mm(niv['pleno']['nosso'])} - {niv['pleno']['vezes']:.1f} vezes "
          "mais rapido.",
          f"Aponte a composicao da barra: desses minutos, so {seg:.0f}s sao "
          f"maquina; o resto ({imp.mm(niv['pleno']['humano'])} no pleno) ja e o "
          "DBA lendo e decidindo. Nao estamos cortando o trabalho de pensar.",
          "Complete: 'e o senior fica livre para o que so ele resolve'. Isso "
          "desarma quem ouve 'ferramenta' e entende 'substituicao'.",
          "Se contestarem os minutos: 'boa - o slide de premissas mostra a "
          "conta inteira, e eu quero mesmo o numero de voces'."],
         "Discutir premissa aqui. Empurre para o slide proprio."),

        ("MTTA e MTTR", 2.5,
         "Traduza para as duas metricas que a gestao ja acompanha.",
         "Nos nao prometemos resolver o incidente mais rapido. Nos apagamos a "
         "primeira meia hora dele - a que o mercado inteiro gasta so juntando "
         "contexto. E como essa e exatamente a fase que atrasa o primeiro "
         "retorno, o MTTA cai junto com o MTTR.",
         ["MTTA: o primeiro aceite deixa de ser 'estamos verificando' e passa "
          "a sair com evidencia e achado priorizado anexados.",
          "Ancore no mercado: MTTR medio de 3 a 5 horas, alvo de 1 a 2 horas "
          "no setor financeiro, 55% dos incidentes passando de 2 horas.",
          "Seja preciso sobre o que NAO prometemos. Precisao aqui compra "
          "credibilidade para o slide seguinte."],
         "Prometer reducao percentual de MTTR. Nao medimos isso ainda."),

        ("Represamento", 3.0,
         "Faca a conta em voz alta, devagar. Se puder, escreva no quadro.",
         f"Uma fila de {cap['fila']} chamados ocupa hoje "
         f"{cn['pleno']['fila_dias_manual']:.0f} turnos de um pleno. Com a "
         f"ferramenta, ela cabe em {imp.mm(cn['pleno']['fila_min_nosso'])} - "
         "menos de um turno. A fila nao anda porque falta gente; ela nao anda "
         "porque cada caso custa caro demais para comecar.",
         [f"Base declarada: {imp.mm(cap['jornada_min'])} liquidos por turno, "
          "ja descontando reuniao e interrupcao.",
          f"Capacidade do pleno: {cn['pleno']['por_dia_manual']:.1f} casos por "
          f"turno viram {cn['pleno']['por_dia_nosso']:.0f}.",
          "Se so um slide sobreviver ao corte de tempo, que seja este.",
          "Convide a conferir: 'a conta e divisao simples, de proposito'."],
         "Prometer reducao de quadro. O ganho e de vazao, nao de pessoas - e "
         "dizer o contrario transforma aliado em opositor."),

        ("Mais rapido cobrindo mais", 2.0,
         "Antecipe a objecao antes que ela seja dita.",
         f"A pergunta obvia e: 'e claro que e mais rapido, deve olhar menos'. "
         f"E o contrario. A rotina manual cobre {c['checks_na_mao']} itens; o "
         f"catalogo cobre {c['checks']} - {c['cobertura_vezes']:.1f} vezes mais. "
         "E a comparacao de tempo ja foi generosa com o lado manual.",
         ["Diga que o catalogo sai de performance: recuperabilidade, "
          "integridade, conformidade e erros ORA- catalogados.",
          "Feche com: 'e o que o banco recusou aparece declarado - nao existe "
          "placar verde por falta de dado'."],
         "Passar rapido demais. E aqui que a objecao morre."),

        ("Assertividade", 2.0,
         "Mude o tom: aqui o argumento nao e velocidade, e acerto.",
         "Velocidade sem acerto so erra mais rapido. Por isso a prioridade e "
         "calculada por tempo de banco consumido, a hipotese descartada fica "
         "gravada com o motivo, e o achado que ja apareceu no mesmo banco vem "
         "marcado como reincidente.",
         [f"{n['playbooks']} playbooks ligados ao achado, com o risco de cada "
          "comando declarado antes de rodar.",
          "Use a referencia de mercado como EFEITO esperado de roteiro "
          "padronizado + base de conhecimento, nunca como promessa nossa."],
         "Apresentar o numero de mercado como resultado ja obtido aqui."),

        ("Padronizacao", 1.5,
         "Se houver gestor na sala, olhe para ele neste slide.",
         "Sem padrao nao existe indicador - existe anedota. Hoje 100% dos "
         "casos saem no mesmo template, com data, hora e risco declarado em "
         "cada acao. A auditoria le o caso de janeiro e o de julho no mesmo "
         "formato.",
         ["O que ainda nao foi registrado sai marcado como <preencher>, em "
          "vez de preenchido no chute. Diga isso: e o oposto do que se espera "
          "de uma ferramenta que 'gera relatorio'."],
         "Prometer que o texto sai pronto sem revisao humana."),

        ("Nossos diferenciais", 2.0,
         "Responda ao 'ja nao temos OEM?' antes que a pergunta apareca.",
         "OEM monitora. ORAchk audita configuracao. Script coleta. Nenhum dos "
         "tres conduz o caso ate o texto do chamado - e e exatamente esse vao "
         "que a gente ocupa.",
         ["Sem instalar nada, sem agente, sem Oracle Client, e sem consumir "
          "pack no modo Parcial.",
          f"Se prova sozinho: {n['testes']} testes, corpus de regressao e "
          "bateria contra bancos reais.",
          "Feche com: 'nao substituimos o que ja existe'. Diminuir ferramenta "
          "que alguem na sala escolheu e o jeito mais rapido de criar "
          "opositor."],
         "Comparacao agressiva com produto de fornecedor."),

        ("Como esses numeros foram calculados", 2.0,
         "NAO pule para ganhar tempo. Este slide e o que separa o deck de uma "
         "peca de marketing.",
         "Todo numero que eu mostrei tem uma de tres origens, e o slide diz "
         "qual e cada uma: contado do repositorio, medido nas execucoes "
         "reais, ou premissa declarada. E o placar foi inclinado a favor do "
         "lado manual de proposito.",
         ["Convide a discordar: 'se na rotina de voces sao dois minutos por "
          "check e nao quatro, me diga que eu regero o deck com o numero de "
          "voces'.",
          "Isso transforma um cetico em coautor - e o numero passa a ser "
          "dele."],
         "Ficar na defensiva. A premissa e um convite, nao uma trincheira."),

        ("Em uma tela", 1.0,
         "Deixe este slide no ar. E o unico que fica projetado durante as "
         "perguntas.",
         f"Se voces levarem so uma coisa daqui: a maquina faz em "
         f"{seg:.0f} segundos o que hoje custa "
         f"{imp.mm(niv['pleno']['manual'])}, cobrindo "
         f"{c['cobertura_vezes']:.1f} vezes mais - e 100% dos casos saem no "
         "mesmo template.",
         ["Nao explique os cinco numeros de novo: eles ja foram defendidos. "
          "So aponte e siga.",
          "Termine repetindo o pedido que esta no rodape do slide.",
          "Volte a este slide sempre que uma resposta terminar - ele e a sua "
          "tela de descanso."],
         "Usar este slide como resumo NOVO. Nada aqui pode ser inedito."),

        ("Referencias publicas", 0.5,
         "Slide de consulta, nao de leitura. Passe rapido.",
         "As premissas conversam com o que o mercado publica; as fontes estao "
         "aqui e o deck vai por e-mail para quem quiser conferir.",
         ["Nao leia as sete linhas em voz alta."],
         "Gastar tempo aqui."),

        ("Demonstracao", 5.0,
         "Caso JA coletado, aberto antes de comecar. Siga a ordem ensaiada.",
         "Deixa eu mostrar num caso de verdade - mascarado, porque o deck vai "
         "circular depois.",
         ["Dashboard: Health Score, achado critico, plano priorizado.",
          "Registre uma acao de RISCO ALTO e mostre o aviso que a ferramenta "
          "cobra. E o momento que mais impressiona DBA senior.",
          "Resolva o caso e mostre o texto do chamado saindo pronto, com o "
          "que falta marcado.",
          "Encerre no consolidado do dia (CSV).",
          "Se algo travar: 'e por isso que eu trouxe o plano B' e abra o HTML "
          "salvo. Naturalidade aqui vale mais que a demo perfeita."],
         "Coletar ao vivo. Depende de rede, senha e sorte."),

        ("O que estamos pedindo", 1.5,
         "Termine pedindo uma decisao COM DATA. Nao termine agradecendo.",
         "Se voces so tiverem tempo para uma decisao hoje, eu peco a primeira: "
         "todo chamado de banco passa a ser aberto com um caso no framework. "
         "As outras duas seguem dela. E o proximo passo e desta semana: "
         "escolher um servidor e rodar o primeiro caso junto.",
         ["Pare de falar depois do pedido. O silencio faz o trabalho.",
          "Se a resposta for 'vamos avaliar', pergunte: 'ate quando, e o que "
          "voce precisa de mim ate la?'."],
         "Terminar em 'obrigado, alguma pergunta?'. Apresentacao sem pedido "
         "termina em aplauso e nada muda."),
    ]


def _frases(c: dict, cap: dict, med: dict) -> list[str]:
    niv, cn = c["niveis"], cap["niveis"]
    return [
        f"Tudo que da para automatizar aqui leva {med['segundos']:.0f} "
        "segundos. E nao e estimativa: e cronometro.",
        f"A maquina faz em {med['segundos']:.0f} segundos o que hoje custa "
        f"{imp.mm(niv['pleno']['manual'])} de um pleno.",
        f"O junior com a ferramenta ({imp.mm(niv['junior']['nosso'])}) entrega "
        f"antes do senior sem ela ({imp.mm(niv['senior']['manual'])}).",
        "Nao e que o junior ficou senior - e que o roteiro parou de morar na "
        "cabeca das pessoas.",
        "Nos nao resolvemos o incidente mais rapido. Nos apagamos a primeira "
        "meia hora dele.",
        "A fila nao anda porque falta gente; ela nao anda porque cada caso "
        "custa caro demais para comecar.",
        f"O que hoje ocupa {cn['pleno']['fila_dias_manual']:.0f} turnos de fila "
        f"cabe em {imp.mm(cn['pleno']['fila_min_nosso'])}.",
        "Velocidade sem acerto so erra mais rapido.",
        "Sem padrao nao existe indicador - existe anedota.",
        f"Mais rapido cobrindo {c['cobertura_vezes']:.1f} vezes mais.",
        "Score verde por falta de analise e pior que score ruim.",
        "Nao substituimos o que ja existe - ocupamos o vao entre o alerta que "
        "chega e o chamado que precisa ser respondido.",
        "A ferramenta nao impede o DBA de agir; ela impede que a acao passe "
        "sem registro.",
    ]


def _perguntas(c: dict, n: dict) -> list[tuple[str, str]]:
    """As perguntas que VAO aparecer, com a resposta ja pensada.

    Improvisar resposta para pergunta previsivel e o unico erro caro de uma
    apresentacao bem preparada - e todas estas sao previsiveis.
    """
    return [
        ("Esse tempo de segundos e real ou e demonstracao de laboratorio?",
         "E cronometro, gravado em disco com data e tamanho de amostra. O "
         "comando que mede roda offline, sobre a evidencia ja coletada, sem "
         "tocar em banco - esta no repositorio e qualquer um repete depois "
         "desta reuniao."),
        ("De onde saiu esse numero de tempo?",
         "Do modelo que esta no slide de premissas: tempo por check por nivel, "
         "mais preparo, mais o tempo de escrever o chamado, sobre "
         f"{c['checks_na_mao']} itens. Esta tudo num arquivo so. Se o numero "
         "de voces for outro, me diz que eu regero o deck com ele."),
        ("Ja nao temos o OEM / ORAchk / os scripts do time?",
         "Temos, e eles continuam. OEM monitora, ORAchk audita configuracao, "
         "script coleta. Nenhum deles conduz o caso ate o texto do chamado, "
         "que e onde o nosso tempo vai embora."),
        ("Isso roda em producao? E seguro?",
         "So SELECT em views de diagnostico, com o catalogo de checks fixo e "
         "versionado, sessao marcada como read-only. O unico comando que "
         "escreve - o Tuning Advisor - ficou fora do painel de proposito."),
        ("Consome licenca de Diagnostics/Tuning Pack?",
         "Depende do modo, e a tela diz antes de rodar. O modo Parcial nao "
         "consome nada e mesmo assim entrega perfil de espera, amostrando "
         "gv$session."),
        ("Isso vai substituir DBA?",
         "Nao. Ela coleta, organiza e prioriza; a decisao sobre o que executar "
         "continua humana. O que muda e onde o senior gasta a hora dele."),
        ("E se a ferramenta errar o diagnostico?",
         "Ela nao fecha diagnostico - ela prioriza achado com a evidencia ao "
         "lado. E quando um analisador falha, o relatorio diz isso no topo, "
         "em vez de mostrar um score verde."),
        ("Quem mantem isso quando o autor sair?",
         f"{n['testes']} testes automatizados, corpus de regressao offline e "
         "bateria contra bancos reais. Check, playbook e regra de "
         "conformidade sao JSON - estender nao exige mexer em Python."),
        ("Quanto custa?",
         "Nada de licenca: duas bibliotecas Python e nenhum agente. O custo e "
         "o tempo de melhoria continua, que e o terceiro pedido do slide "
         "final."),
        ("A senha fica guardada onde?",
         "Em lugar nenhum. E digitada por sessao, vai direto para o processo "
         "e nao aparece na linha de comando, no log nem em arquivo."),
        ("Por que o deck nao mostra um caso real nosso?",
         "Porque ele circula por e-mail. Todo exemplo e ficticio, e ha teste "
         "automatizado impedindo que nome de servidor real entre no arquivo."),
    ]


def texto_roteiro() -> str:
    """O roteiro completo em Markdown, com os numeros desta execucao."""
    n = numeros()
    med = imp.tempo_maquina()
    c = imp.comparativo(n["checks"], med["minutos"])
    cap = imp.capacidade(c)

    passos = _passos(c, cap, n, med)
    total = sum(p[1] for p in passos)

    L: list[str] = []
    L.append("# Roteiro - apresentacao de impacto")
    L.append("")
    L.append(f"Deck: `output/apresentacao/FRAMEWORK_IMPACTO.pptx` | "
             f"{len(passos)} slides | **{total:.0f} minutos** de fala, sem "
             "contar perguntas.")
    L.append("")
    L.append("> Gerado por `APRESENTAR.py --impacto`, junto com o deck. Os "
             "numeros abaixo sao os MESMOS do PPTX, calculados na mesma "
             "execucao.")
    L.append("")
    L.append("---")
    L.append("")
    L.append("## 1. Antes de apresentar")
    L.append("")
    L += _preparacao()
    L.append("")
    L.append("---")
    L.append("")
    L.append("## 2. Os numeros que voce precisa saber de cor")
    L.append("")
    L += _numeros_de_cor(c, cap, n, med)
    L.append("")
    L.append(f"**O numero que abre a apresentacao: {med['segundos']:.0f} "
             "segundos de maquina.**"
             + ("" if med["medido"] else
                "  :warning: **esta copia nao tem medicao travada** - rode "
                "`APRESENTAR.py --medir` antes de apresentar."))
    L.append("")
    L.append("Se lembrar de apenas tres: **o tempo do pleno**, **a fila de "
             f"{cap['fila']} chamados** e **a cobertura de "
             f"{c['cobertura_vezes']:.1f}x**.")
    L.append("")
    L.append("---")
    L.append("")
    L.append("## 3. Passo a passo, slide a slide")
    L.append("")
    for i, (titulo, mins, fazer, falar, pontos, evitar) in enumerate(passos, 1):
        L.append(f"### Slide {i} - {titulo}  ({mins:g} min)")
        L.append("")
        L.append(f"**O que fazer:** {fazer}")
        L.append("")
        L.append(f"**[FALE ASSIM]** \"{falar}\"")
        L.append("")
        for p in pontos:
            L.append(f"- {p}")
        L.append("")
        L.append(f"**Evite:** {evitar}")
        L.append("")
    L.append("---")
    L.append("")
    L.append("## 4. As frases que valem decorar")
    L.append("")
    for f in _frases(c, cap, med):
        L.append(f"- \"{f}\"")
    L.append("")
    L.append("---")
    L.append("")
    L.append("## 5. Perguntas dificeis, com a resposta pronta")
    L.append("")
    for q, a in _perguntas(c, n):
        L.append(f"**{q}**")
        L.append("")
        L.append(a)
        L.append("")
    L.append("---")
    L.append("")
    L.append("## 6. Se o tempo apertar")
    L.append("")
    L.append("Corte nesta ordem: " + ", ".join(f"**{t}**" for t in CORTAVEIS)
             + ".")
    L.append("")
    L.append("Nunca corte: **Por que e seguro rodar**, **Represamento**, "
             "**Como esses numeros foram calculados** e **O que estamos "
             "pedindo**. Sao, nesta ordem, a credibilidade tecnica, o "
             "argumento de negocio, a honestidade do metodo e a decisao que "
             "voce foi buscar.")
    L.append("")
    L.append("---")
    L.append("")
    L.append("## 7. Depois da apresentacao")
    L.append("")
    L.append("1. Envie o deck no mesmo dia, enquanto a conversa esta fresca.")
    L.append("2. Mande junto a data do primeiro caso conduzido em conjunto - "
             "compromisso sem data nao existe.")
    L.append("3. Anote toda premissa contestada e regere o deck com os "
             "numeros do time. O cetico de hoje vira o dono do numero de "
             "amanha.")
    L.append("4. Se pediram algo que a ferramenta nao faz, registre e "
             "responda depois. Prometer na hora e como abrir chamado sem "
             "evidencia.")
    L.append("")
    return "\n".join(L) + "\n"


def build_roteiro(out_path: str | None = None) -> str:
    """Grava o roteiro em Markdown. Retorna o caminho."""
    out_path = out_path or os.path.join(OUTPUT_DIR, "ROTEIRO_IMPACTO.md")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(texto_roteiro())
    return out_path


__all__ = ["CORTAVEIS", "ORDEM", "build_roteiro", "texto_roteiro"]
